from pathlib import Path

ROOT = Path(__file__).parents[2]
CHAT_JS = ROOT / "src" / "api" / "static" / "js" / "chat.js"


def test_chat_hides_suggestions_after_first_user_message():
    source = CHAT_JS.read_text(encoding="utf-8")
    assert "syncSuggestionVisibility" in source
    assert 'empty.hidden = true' in source
    assert "research-suggestion-grid" in source


def test_chat_renders_collapsed_thinking_details():
    source = CHAT_JS.read_text(encoding="utf-8")
    assert 'el("details", "message-thinking")' in source
    assert "查看分析过程" in source


def test_workspace_uses_light_research_workbench_surface():
    css = (ROOT / "src" / "api" / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert "--workspace-bg: #f6f8fb" in css
