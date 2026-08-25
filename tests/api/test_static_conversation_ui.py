from pathlib import Path

ROOT = Path(__file__).parents[2]
CHAT_JS = ROOT / "src" / "api" / "static" / "js" / "chat.js"


def test_chat_hides_suggestions_after_first_user_message():
    source = CHAT_JS.read_text(encoding="utf-8")
    assert "syncSuggestionVisibility" in source
    assert 'empty.hidden = true' in source


def test_chat_renders_collapsed_thinking_details():
    source = CHAT_JS.read_text(encoding="utf-8")
    assert 'el("details", "message-thinking")' in source
    assert "查看分析过程" in source
