"""会话管理单元测试"""
import pytest

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
        sid2, m2 = mgr2.get_or_create(sid)
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
