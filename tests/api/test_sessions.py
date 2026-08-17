"""会话管理单元测试"""
import threading
import time

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
        assert sessions[0]["message_count"] == 1

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


class TestSessionManager:
    def test_get_or_create_new_session(self, store, facts_path):
        mgr = SessionManager(store, facts_path=facts_path)
        sid, memory = mgr.get_or_create(None, "帮我分析平安银行")
        assert sid
        assert memory.session_id == sid
        assert store.session_exists(sid)
        assert store.list_sessions()[0]["title"] == "帮我分析平安银行"

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

    def test_title_truncated_to_20_chars(self, store, facts_path):
        mgr = SessionManager(store, facts_path=facts_path)
        sid, _ = mgr.get_or_create(None, "x" * 30)
        sessions = store.list_sessions()
        assert sessions[0]["session_id"] == sid
        assert sessions[0]["title"] == "x" * 20

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
