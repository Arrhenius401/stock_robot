"""消息内容规范化器测试。"""

from api.message_content import encode_message_content, normalize_message_content


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


def test_encode_message_content_round_trips_body_and_thinking():
    """持久化后的助手消息重新加载时仍须能拆出正文与思考。"""
    encoded = encode_message_content("最终结论", "先核对估值，再给出建议")

    assert normalize_message_content(encoded) == {
        "text": "最终结论",
        "thinking": "先核对估值，再给出建议",
    }


def test_encode_message_content_preserves_thinking_duration():
    """历史思考区应保留生成耗时，以便前端显示「用时 N 秒」。"""
    encoded = encode_message_content("最终结论", "推理过程", thinking_duration_seconds=1.4)

    assert normalize_message_content(encoded) == {
        "text": "最终结论",
        "thinking": "推理过程",
        "thinking_duration_seconds": 1.4,
    }


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
