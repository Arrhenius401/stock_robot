"""会话管理单元测试"""
import sqlite3
import threading
import time
from datetime import date

import pytest

from agent.memory import Memory
from api.sessions import SessionManager, SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


@pytest.fixture
def facts_path(tmp_path):
    return tmp_path / "facts.json"


class TestSessionStore:
    def test_create_and_list(self, store):
        store.create_session("s1", "标题一")
        store.append_message("s1", "user", "你好")
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "s1"
        assert sessions[0]["title"] == "标题一"
        assert sessions[0]["title_source"] == "legacy"
        assert sessions[0]["message_count"] == 1

    def test_initialization_migrates_legacy_database_title_source(self, tmp_path):
        """旧会话库升级后应保留数据并补齐 legacy 来源。"""
        db_path = tmp_path / "legacy.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, title TEXT NOT NULL, "
                "created_at REAL NOT NULL, updated_at REAL NOT NULL)"
            )
            conn.execute(
                "INSERT INTO sessions VALUES ('legacy-1', '历史会话', 1.0, 2.0)"
            )
        store = SessionStore(db_path)
        assert store.list_sessions() == [{
            "session_id": "legacy-1",
            "title": "历史会话",
            "title_source": "legacy",
            "created_at": 1.0,
            "updated_at": 2.0,
            "message_count": 0,
        }]

    def test_automatic_title_update_preserves_manual_title(self, store):
        """手动标题不得被仅允许自动来源的更新覆盖。"""
        store.create_session("s1", "初始标题", source="local")
        assert store.update_title("s1", "我的标题", "manual")
        assert store.update_title("s1", "LLM 标题", "llm", only_if_automatic=True) is False
        session = store.list_sessions()[0]
        assert session["title"] == "我的标题"
        assert session["title_source"] == "manual"

    def test_get_messages_ordered(self, store):
        store.create_session("s1", "t")
        store.append_message("s1", "user", "第一条")
        store.append_message("s1", "tool", "第二条")
        msgs = store.get_messages("s1")
        assert [m["content"] for m in msgs] == ["第一条", "第二条"]

    def test_clear_and_delete(self, store):
        store.create_session("s1", "t")
        store.append_message("s1", "user", "x")
        store.clear_messages("s1")
        assert store.get_messages("s1") == []
        store.delete_session("s1")
        assert not store.session_exists("s1")
        assert store.list_sessions() == []

    def test_artifact_round_trip_and_clear_or_delete_removes_it(self, store):
        """成果写入后可读取，并会随会话清空或删除一并移除。"""
        store.create_session("s1", "t")
        artifact = store.save_artifact(
            "s1",
            kind="stock_report",
            symbol="000001",
            payload={"symbol": "000001", "score": {"final": 7.2}},
        )

        assert artifact["session_id"] == "s1"
        assert artifact["message_id"] is None
        assert artifact["kind"] == "stock_report"
        assert artifact["symbol"] == "000001"
        assert artifact["payload"] == {"symbol": "000001", "score": {"final": 7.2}}
        assert isinstance(artifact["created_at"], float)
        assert artifact["updated_at"] == artifact["created_at"]
        assert store.list_artifacts("s1") == [artifact]
        assert store.get_artifact(artifact["artifact_id"]) == artifact

        store.clear_messages("s1")
        assert store.list_artifacts("s1") == []

        deleted_artifact = store.save_artifact(
            "s1", kind="stock_report", symbol="000001", payload={}
        )
        store.delete_session("s1")
        assert store.get_artifact(deleted_artifact["artifact_id"]) is None

    def test_artifact_id_is_unique_and_payload_uses_default_string_conversion(self, store):
        """成果 ID 不重复，日期等非 JSON 原生值按字符串存储。"""
        store.create_session("s1", "t")
        first = store.save_artifact(
            "s1", kind="stock_report", symbol=None, payload={"date": date(2026, 8, 23)}
        )
        second = store.save_artifact("s1", kind="stock_report", symbol=None, payload={})

        assert first["artifact_id"] != second["artifact_id"]
        assert first["payload"] == {"date": "2026-08-23"}
        assert store.get_artifact(first["artifact_id"])["payload"] == {"date": "2026-08-23"}
        assert store.get_artifact("missing") is None


class TestSessionManager:
    def test_get_or_create_new_session(self, store, facts_path):
        mgr = SessionManager(store, facts_path=facts_path)
        sid, memory = mgr.get_or_create(None, "帮我分析平安银行")
        assert sid
        assert memory.session_id == sid
        assert store.session_exists(sid)
        assert store.list_sessions()[0]["title"] == "平安银行分析"
        assert store.list_sessions()[0]["title_source"] == "local"

    def test_get_or_create_returns_existing_memory(self, store, facts_path):
        mgr = SessionManager(store, facts_path=facts_path)
        sid, m1 = mgr.get_or_create(None, "你好")
        m1.add_message("user", "补充消息")
        sid2, m2 = mgr.get_or_create(sid)
        assert sid2 == sid
        assert m2 is m1

    def test_messages_persist_and_restore(self, store, facts_path):
        mgr = SessionManager(store, facts_path=facts_path)
        sid, m1 = mgr.get_or_create(None, "你好")
        m1.add_message("user", "第一条")
        m1.add_message("tool", "结果一")

        mgr2 = SessionManager(store, facts_path=facts_path)  # 模拟重启
        _, m2 = mgr2.get_or_create(sid)
        assert [m["content"] for m in m2.messages] == ["第一条", "结果一"]

    def test_clear_session_keeps_facts(self, store, facts_path):
        mgr = SessionManager(store, facts_path=facts_path)
        sid, m1 = mgr.get_or_create(None, "你好")
        m1.set_fact("pref", "成长股")
        m1.add_message("user", "x")
        assert mgr.clear(sid)
        assert m1.messages == []
        assert m1.facts == {"pref": "成长股"}
        assert store.get_messages(sid) == []

    def test_delete_session(self, store, facts_path):
        mgr = SessionManager(store, facts_path=facts_path)
        sid, _ = mgr.get_or_create(None, "你好")
        assert mgr.delete(sid)
        assert mgr.delete(sid) is False
        assert mgr.get_memory(sid) is None

    def test_title_uses_safe_fallback_for_unknown_request(self, store, facts_path):
        mgr = SessionManager(store, facts_path=facts_path)
        sid, _ = mgr.get_or_create(None, "请分析一下" + "x" * 30)
        sessions = store.list_sessions()
        assert sessions[0]["session_id"] == sid
        assert sessions[0]["title"] == "x" * 20

    def test_empty_first_message_uses_default_title(self, store, facts_path):
        """空首条消息创建默认会话，并标记默认来源。"""
        mgr = SessionManager(store, facts_path=facts_path)
        sid, _ = mgr.get_or_create(None, "")
        session = store.list_sessions()[0]
        assert session["session_id"] == sid
        assert session["title"] == "新会话"
        assert session["title_source"] == "default"

    def test_manager_rename_and_maybe_update_title(self, store, facts_path):
        """管理器手动改名后，自动标题更新应受到保护。"""
        mgr = SessionManager(store, facts_path=facts_path)
        sid, _ = mgr.get_or_create(None, "分析平安银行")
        assert mgr.rename(sid, "银行跟踪")
        assert mgr.maybe_update_title(sid, "LLM 标题", "llm") is False
        session = store.list_sessions()[0]
        assert session["title"] == "银行跟踪"
        assert session["title_source"] == "manual"

    def test_get_session_detail_includes_messages_and_artifacts(self, store, facts_path):
        """详情读取统一返回会话持久化消息和结构化成果。"""
        mgr = SessionManager(store, facts_path=facts_path)
        sid, memory = mgr.get_or_create(None, "分析平安银行")
        memory.add_message("user", "继续分析")
        artifact = mgr.save_artifact(
            sid,
            kind="stock_report",
            symbol="000001",
            payload={"symbol": "000001", "score": {"final": 7.2}},
        )

        assert mgr.get_session_detail("missing") is None
        assert mgr.get_session_detail(sid) == {
            "messages": [{"role": "user", "content": "继续分析"}],
            "artifacts": [artifact],
        }
        assert mgr.get_artifact(artifact["artifact_id"]) == artifact

    def test_concurrent_get_or_create_same_session(self, store, facts_path):
        mgr = SessionManager(store, facts_path=facts_path)
        sid = "shared-session"
        results: list[tuple[str, Memory]] = []
        lock = threading.Lock()

        def worker() -> None:
            result_sid, memory = mgr.get_or_create(sid, "并发")
            with lock:
                results.append((result_sid, memory))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 8
        assert all(result_sid == sid for result_sid, _ in results)
        first_memory = results[0][1]
        assert all(memory is first_memory for _, memory in results)
        assert len(store.list_sessions()) == 1

    def test_updated_at_ordering_most_recent_first(self, store, facts_path):
        mgr = SessionManager(store, facts_path=facts_path)
        s1, _ = mgr.get_or_create(None, "会话一")
        time.sleep(0.01)
        _s2, _ = mgr.get_or_create(None, "会话二")
        time.sleep(0.01)
        _, memory1 = mgr.get_or_create(s1)
        memory1.add_message("user", "新消息")

        sessions = store.list_sessions()
        assert sessions[0]["session_id"] == s1
        assert sessions[0]["updated_at"] > sessions[1]["updated_at"]

    def test_max_messages_propagated_to_memory(self, store, facts_path):
        mgr = SessionManager(store, max_messages=5, facts_path=facts_path)
        _, memory = mgr.get_or_create(None, "你好")
        for i in range(7):
            memory.add_message("user", f"消息{i}")
        assert len(memory.messages) == 5
