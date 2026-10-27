"""黄金直达会话展示采集进度，不能冒充模型推理。"""
import json

from tests.api.test_static import _DOM_STUB, _module_url, _run_node


def test_gold_loading_and_completed_body(tmp_path):
    script = _DOM_STUB + r'''
const chatScroll = makeElement("chatScroll");
makeElement("chatInput", "textarea");
makeElement("sendBtn", "button");
const { api } = await import(__API__);
const { store } = await import(__STATE__);
const { sendMessage } = await import(__CHAT__);
store.currentSessionId = "gold-session";
api.chatStream = async (_message, _sessionId, handlers) => {
  handlers.plan({session_id:"gold-session", mode:"gold", steps:[]});
  handlers.progress({label:"正在采集黄金数据"});
  if (!chatScroll.textContent.includes("正在采集黄金数据")) throw new Error("缺少采集状态");
  if (byClass(chatScroll,"message-thinking").length) throw new Error("采集被伪装成思考");
  handlers.text({content:"长期：看多；国内短期：偏多"});
  handlers.done({});
};
await sendMessage("黄金");
if (byClass(chatScroll,"message-thinking").length) throw new Error("完成后残留思考占位");
const html = descendants(chatScroll).map(item => item.innerHTML).join("\n");
if (!html.includes("长期：看多")) throw new Error("正文丢失");
if (store.sessionMessages["gold-session"].at(-1).thinking) throw new Error("缓存伪造推理");
'''
    for name, path in {"API": "api.js", "STATE": "state.js", "CHAT": "chat.js"}.items():
        script = script.replace(f"__{name}__", json.dumps(_module_url("src/api/static/js/" + path)))
    _run_node(tmp_path, script)
