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
        """旧会话库升级后应清理没有消息的历史会话。"""
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
        assert store.list_sessions() == []

    def test_restart_purges_empty_sessions_and_their_artifacts(self, tmp_path):
        """重启存储时，零消息会话及其成果都应被清除。"""
        db_path = tmp_path / "sessions.db"
        store = SessionStore(db_path)
        store.create_session("empty", "空会话")
        artifact = store.save_artifact(
            "empty", kind="stock_report", symbol="000001", payload={}
        )
        store.create_session("kept", "保留会话")
        store.append_message("kept", "user", "已有消息")

        restarted = SessionStore(db_path)

        assert [item["session_id"] for item in restarted.list_sessions()] == ["kept"]
        assert restarted.get_artifact(artifact["artifact_id"]) is None

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
        first_id = store.append_message("s1", "user", "第一条")
        second_id = store.append_message("s1", "tool", "第二条")
        msgs = store.get_messages("s1")
        assert isinstance(first_id, int) and second_id == first_id + 1
        assert msgs == [
            {"message_id": first_id, "role": "user", "content": "第一条"},
            {"message_id": second_id, "role": "tool", "content": "第二条"},
        ]
        assert [m["content"] for m in msgs] == ["第一条", "第二条"]

    def test_artifact_round_trip_and_delete_removes_it(self, store):
        """成果写入后可读取，并会随会话删除一并移除。"""
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

        store.delete_session("s1")
        assert store.get_artifact(artifact["artifact_id"]) is None

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
    def test_draft_session_id_creates_real_session_without_draft_row(self, store, facts_path):
        """浏览器草稿 ID 首次发言时必须替换为真实持久化会话 ID。"""
        mgr = SessionManager(store, facts_path=facts_path)

        sid, memory = mgr.get_or_create("draft-browser-1", "分析 000001")

        assert sid != "draft-browser-1"
        assert memory.session_id == sid
        assert store.session_exists("draft-browser-1") is False
        assert store.session_exists(sid) is True

    def test_interleaved_listing_keeps_initial_message_and_artifact(self, tmp_path):
        """列表清理不能删除正在原子创建的首发会话。"""
        class BlockingStore(SessionStore):
            def __init__(self, db_path):
                super().__init__(db_path)
                self.creation_started = threading.Event()
                self.allow_creation = threading.Event()
                self.purge_started = threading.Event()

            def create_session_with_initial_message(self, *args, **kwargs):
                self.creation_started.set()
                assert self.allow_creation.wait(timeout=2)
                return super().create_session_with_initial_message(*args, **kwargs)

            def purge_empty_sessions(self):
                self.purge_started.set()
                return super().purge_empty_sessions()

        store = BlockingStore(tmp_path / "sessions.db")
        manager = SessionManager(store, facts_path=tmp_path / "facts.json")
        result: dict = {}
        errors: list[BaseException] = []

        def create_first_turn() -> None:
            try:
                sid, memory = manager.get_or_create_for_message(
                    "draft-browser-1", "分析 000001")
                message_id = memory.messages[-1]["message_id"]
                result["sid"] = sid
                result["artifact"] = manager.save_artifact(
                    sid,
                    kind="stock_report",
                    symbol="000001",
                    payload={"symbol": "000001"},
                    message_id=message_id,
                    memory=memory,
                )
            except BaseException as exc:  # noqa: BLE001 — 线程失败需交回主断言
                errors.append(exc)

        creator = threading.Thread(target=create_first_turn)
        creator.start()
        assert store.creation_started.wait(timeout=1)

        listing_started = threading.Event()

        def list_sessions() -> None:
            listing_started.set()
            manager.list_sessions()

        lister = threading.Thread(target=list_sessions)
        lister.start()
        assert listing_started.wait(timeout=1)
        assert store.purge_started.wait(timeout=0.1) is False
        store.allow_creation.set()
        creator.join(timeout=2)
        lister.join(timeout=2)

        assert errors == []
        assert store.purge_started.is_set()
        sid = result["sid"]
        assert sid != "draft-browser-1"
        assert store.get_messages(sid)[0]["content"] == "分析 000001"
        assert store.list_artifacts(sid) == [result["artifact"]]
        assert all(not item["session_id"].startswith("draft-")
                   for item in store.list_sessions())

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

    def test_delete_session(self, store, facts_path):
        mgr = SessionManager(store, facts_path=facts_path)
        sid, _ = mgr.get_or_create(None, "你好")
        assert mgr.delete(sid)
        assert mgr.delete(sid) is False
        assert mgr.get_memory(sid) is None

    def test_delete_invalidates_inflight_memory_without_orphans(self, store, facts_path):
        """delete 后旧执行的迟到消息不得形成无 session 的孤儿记录。"""
        mgr = SessionManager(store, facts_path=facts_path)
        sid, old_memory = mgr.get_or_create(None, "分析平安银行")

        assert mgr.delete(sid)
        old_memory.add_message("tool", "删除后迟到工具结果")
        old_memory.add_message("assistant", "删除后迟到回答")

        assert store.get_messages(sid) == []
        assert old_memory.messages == []

    def test_delete_rejects_artifact_from_invalidated_memory(self, store, facts_path):
        """delete 后旧 Memory 不能通过管理器保存成果。"""
        mgr = SessionManager(store, facts_path=facts_path)
        sid, old_memory = mgr.get_or_create(None, "分析平安银行")

        assert mgr.delete(sid)
        with pytest.raises(RuntimeError, match="旧会话执行"):
            mgr.save_artifact(
                sid,
                kind="stock_report",
                symbol="000001",
                payload={"symbol": "000001"},
                memory=old_memory,
            )
        assert store.get_messages(sid) == []
        assert store.list_artifacts(sid) == []

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
        message_id = memory.messages[0]["message_id"]
        assert mgr.get_session_detail(sid) == {
            "messages": [{
                "message_id": message_id,
                "role": "user",
                "content": "继续分析",
            }],
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
        s1, memory1 = mgr.get_or_create(None, "会话一")
        memory1.add_message("user", "会话一消息")
        time.sleep(0.01)
        _s2, memory2 = mgr.get_or_create(None, "会话二")
        memory2.add_message("user", "会话二消息")
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
