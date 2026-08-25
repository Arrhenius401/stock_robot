from api.session_titles import derive_session_title_from_messages


def test_second_user_message_improves_generic_first_title():
    assert derive_session_title_from_messages(["你好", "比较平安银行和招商银行"]) == "平安银行与招商银行比较"


def test_empty_messages_keep_new_session_title():
    assert derive_session_title_from_messages([]) == "新会话"
