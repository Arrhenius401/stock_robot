"""消息内容规范化器测试。"""

from api.message_content import normalize_message_content


def test_normalize_plain_string():
    assert normalize_message_content("正文") == {"text": "正文"}


def test_normalize_content_blocks_separates_text_and_thinking():
    raw = [
        {"thinking": "推理", "type": "thinking"},
        {"text": "# 正文", "type": "text"},
    ]
    assert normalize_message_content(raw) == {"text": "# 正文", "thinking": "推理"}


def test_normalize_json_content_blocks():
    raw = '[{"type":"thinking","thinking":"思考"},{"type":"text","text":"答案"}]'
    assert normalize_message_content(raw) == {"text": "答案", "thinking": "思考"}


def test_normalize_old_python_literal_content_blocks():
    raw = "[{'thinking': '推理', 'type': 'thinking'}, {'text': '# 正文', 'type': 'text'}]"
    assert normalize_message_content(raw) == {"text": "# 正文", "thinking": "推理"}


def test_normalize_old_python_literal_with_html_space_suffix():
    """浏览器历史存储追加的 HTML 空格实体不能阻止正文与推理拆分。"""
    raw = (
        "[{'thinking': '推理', 'type': 'thinking'}, "
        "{'text': '# 正文', 'type': 'text'}] &#x20;"
    )
    assert normalize_message_content(raw) == {"text": "# 正文", "thinking": "推理"}


def test_invalid_or_unsafe_legacy_string_is_plain_text():
    raw = "__import__('os').system('echo unsafe')"
    assert normalize_message_content(raw) == {"text": raw}


def test_only_list_of_dicts_is_treated_as_blocks():
    assert normalize_message_content(["正文"]) == {"text": "['正文']"}
    assert normalize_message_content({"type": "text", "text": "正文"}) == {"text": "正文"}
