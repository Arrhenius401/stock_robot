"""FastAPI HTTP API — REST + SSE 流式接口"""
import asyncio
import json
import logging
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from agent.chat import ChatResponder
from agent.executor import Executor
from agent.graph import DEFAULT_CHECKPOINT_DIR
from agent.memory import TaskStatus
from agent.planner import Planner

logger = logging.getLogger(__name__)


def _json_safe(payload) -> dict:
    """将 payload 中的非 JSON 类型（date 等）转为字符串后重新解析"""
    return json.loads(json.dumps(payload, ensure_ascii=False, default=str))


def _structured_tool_results(plan, memory) -> list[dict]:
    """从计划步骤与 memory 工具消息配对出结构化结果

    配对前提：Executor 只在步骤成功时写 role=="tool" 消息，且本轮消息
    必然位于 memory 消息列表尾部（无其他写入者），故取最后 done_count 条
    tool 消息按序配对——对 max_messages 截断免疫。
    """
    done_count = sum(1 for s in plan.steps
                     if s.tool_name and s.status == TaskStatus.DONE)
    tool_msgs = [m["content"] for m in memory.messages
                 if m["role"] == "tool"][-done_count:] if done_count else []
    results = []
    for step in plan.steps:
        if not step.tool_name:
            continue
        content = None
        if step.status == TaskStatus.DONE and tool_msgs:
            content = tool_msgs.pop(0)
        elif step.status == TaskStatus.DONE:
            # 配对不变量被破坏时（理论上不应发生），记录日志便于诊断
            logger.warning("结构化工具结果配对不完整: 步骤 %s 缺少对应 tool 消息", step.id)
        results.append({
            "tool": step.tool_name,
            "symbol": (step.tool_args or {}).get("symbol"),
            "status": step.status.value,
            "content": content,
        })
    return results


def _build_signal_payload(final_score: float) -> dict:
    """从配置加载信号映射并构造 API 信号字段（配置损坏时抛 ValueError 由边界兜底）"""
    from report.signal import SIGNAL_LABELS, derive_signal, load_signal_config
    from utils.config import Config

    cfg = load_signal_config(Config())
    level = derive_signal(final_score, cfg.thresholds)
    action = cfg.actions[level]
    return {"level": level, "label": SIGNAL_LABELS[level],
            "action": action.action, "position": action.position}


def create_app(core=None, sessions=None):
    app = FastAPI(title="Stock Robot API", version="0.1.0",
                  description="AI 驱动的股票分析研报助手 HTTP API")

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])

    if sessions is None and core is not None:
        from api.sessions import SessionManager, SessionStore
        from utils.config import Config
        sessions = SessionManager(SessionStore(Config().config_dir / "sessions.db"))

    def _build_agent(session_memory):
        """按会话现建轻量 Planner/Executor/ChatResponder（无状态，构造廉价）"""
        # 调用方（chat/run_agent）已校验 core 注入，复制到局部变量并断言收窄类型
        agent_core = core
        assert agent_core is not None
        planner = Planner(llm=agent_core.llm, registry=agent_core.registry,
                          memory=session_memory)
        executor = Executor(registry=agent_core.registry, memory=session_memory,
                            model=agent_core.model,
                            session_id=session_memory.session_id or "",
                            persist_dir=str(DEFAULT_CHECKPOINT_DIR))
        chat_responder = ChatResponder(model=agent_core.model)
        return planner, executor, chat_responder

    def _extract_session_id(body: dict, request: Request):
        return body.get("session_id") or request.headers.get("X-Session-Id")

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/v1/tools")
    async def list_tools():
        if core is None:
            return JSONResponse({"tools": []})
        return JSONResponse({"tools": core.registry.list_all_summary(),
                             "total": len(core.registry.list_all())})

    @app.post("/api/v1/chat")
    async def chat(request: Request):
        body = await request.json()
        message = str(body.get("message", "")).strip()
        session_id = _extract_session_id(body, request)
        if not message:
            raise HTTPException(status_code=422, detail="message 不能为空")
        if core is None or sessions is None:
            return JSONResponse({"response": f"[API 模式] 收到消息: {message}（Agent 核心未注入）",
                                 "session_id": session_id or ""})

        try:
            sid, memory = sessions.get_or_create(session_id, message)
            memory.add_message("user", message)
            planner, executor, chat_responder = _build_agent(memory)
            plan = await asyncio.to_thread(planner.plan, message)
            if plan.mode == "chat":
                # 闲聊：ChatResponder 普通会话回复，跳过执行器并写入会话消息
                reply = await chat_responder.reply(message, memory)
                memory.add_message("assistant", reply)
                return JSONResponse({
                    "response": reply,
                    "plan": {"goal": plan.goal, "mode": "chat", "steps": []},
                    "tool_results": [],
                    "session_id": sid,
                })
            plan = await executor.execute(plan)
            done = sum(1 for s in plan.steps if s.status == TaskStatus.DONE)
            total = len(plan.steps)
            tool_results = _structured_tool_results(plan, memory)
            return JSONResponse({
                "response": f"目标: {plan.goal}\n完成: {done}/{total} 步骤",
                "plan": {"goal": plan.goal, "steps": [
                    {"id": s.id, "description": s.description, "status": s.status.value}
                    for s in plan.steps
                ]},
                "tool_results": tool_results,
                "session_id": sid,
            })
        except Exception as e:  # noqa: BLE001 — HTTP 边界兜底，返回 500 而非崩溃
            logger.error("Agent 对话失败: %s", e)
            return JSONResponse({"response": f"处理请求时出错: {e}",
                                 "session_id": session_id or ""}, status_code=500)

    @app.post("/api/v1/chat/stream")
    async def chat_stream(request: Request):
        body = await request.json()
        message = str(body.get("message", "")).strip()
        if not message:
            raise HTTPException(status_code=422, detail="message 不能为空")

        async def event_stream():
            yield f"data: {json.dumps({'type': 'start', 'message': message}, ensure_ascii=False)}\n\n"
            if core is None or sessions is None:
                yield f"data: {json.dumps({'type': 'text', 'content': '[API 模式] Agent 核心未注入'}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return

            session_id = _extract_session_id(body, request)
            queue: asyncio.Queue = asyncio.Queue()

            def on_progress(stage, current, total, label):
                queue.put_nowait({"type": "progress", "stage": stage,
                                  "current": current, "total": total, "label": label})

            async def run_agent():
                try:
                    # 事件流入口已校验 sessions 注入，复制到局部变量并断言收窄类型
                    manager = sessions
                    assert manager is not None
                    sid, memory = manager.get_or_create(session_id, message)
                    memory.add_message("user", message)
                    planner, executor, chat_responder = _build_agent(memory)
                    plan = await asyncio.to_thread(planner.plan, message)
                    if plan.mode == "chat":
                        # 闲聊：直接发 text 事件后结束，不再走执行器与 result 事件
                        reply = await chat_responder.reply(message, memory)
                        memory.add_message("assistant", reply)
                        await queue.put({"type": "text", "content": reply})
                        await queue.put({"type": "done"})
                        return
                    await queue.put({"type": "plan", "goal": plan.goal,
                                     "steps": [s.description for s in plan.steps],
                                     "session_id": sid})
                    plan = await executor.execute(plan, on_progress=on_progress)
                    done = sum(1 for s in plan.steps if s.status == TaskStatus.DONE)
                    total = len(plan.steps)
                    await queue.put({"type": "result",
                                     "summary": f"目标: {plan.goal}\n完成: {done}/{total} 步骤",
                                     "tool_results": _structured_tool_results(plan, memory)})
                except Exception as e:  # noqa: BLE001 — SSE 流内兜底，错误以事件返回
                    logger.error("Agent 流式对话失败: %s", e)
                    await queue.put({"type": "error", "message": str(e)})
                await queue.put({"type": "done"})

            task = asyncio.create_task(run_agent())
            try:
                while True:
                    event = await queue.get()
                    yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                    if event["type"] == "done":
                        break
                await task
            finally:
                # 客户端断连时取消后台任务，避免 run_agent 泄漏继续执行
                if not task.done():
                    task.cancel()

        return StreamingResponse(event_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

    @app.post("/api/v1/analyze")
    async def analyze(request: Request):
        body = await request.json()
        symbol = str(body.get("symbol", "")).strip()
        if not symbol:
            raise HTTPException(status_code=422, detail="symbol 不能为空")
        if core is None:
            raise HTTPException(status_code=503, detail="Agent 核心未注入")

        from utils.symbols import normalize_symbol, resolve_name, validate_symbol
        if not validate_symbol(symbol):
            raise HTTPException(status_code=422, detail=f"无效的股票代码: {symbol}")
        symbol = normalize_symbol(symbol)
        name = await asyncio.to_thread(resolve_name, symbol) or symbol

        try:
            results, commentary, ctx = await asyncio.to_thread(
                core.pipeline.run, symbol, name
            )
            from report.scoring import compute_price_info, compute_score_summary

            summary = compute_score_summary(results)
            price_info = compute_price_info(ctx)
            dimensions = {}
            for r in results:
                dimensions[r.dimension] = {
                    "status": r.status, "summary": r.summary, "score": r.score,
                    "score_detail": r.score_detail, "metrics": r.metrics,
                    "risk_flags": r.risk_flags,
                }
            payload = {
                "symbol": symbol,
                "name": name,
                "overview": {
                    "latest_close": price_info["latest_price"],
                    "year_high": price_info["year_high"],
                    "year_low": price_info["year_low"],
                    "price_position": price_info["price_position"],
                    "change_pct": price_info["change_pct"],
                    "industry": ctx.industry_data.industry if ctx.industry_data else "未知",
                },
                "score": {"base": summary.base_score, "final": summary.final_score,
                          "risk_deduction": summary.risk_deduction},
                "score_rows": summary.score_rows,
                "dimensions": dimensions,
                "commentary": commentary.get("bulk", ""),
                "signal": _build_signal_payload(summary.final_score),
                "generated_at": datetime.now().astimezone().isoformat(),
            }
            return JSONResponse(_json_safe(payload))
        except Exception as e:  # noqa: BLE001 — HTTP 边界兜底
            logger.error("个股分析失败: %s", e)
            return JSONResponse({"symbol": symbol, "error": str(e)}, status_code=500)

    @app.post("/api/v1/index")
    async def index(request: Request):
        body = await request.json()
        raw_symbols = body.get("symbols")
        if raw_symbols is None:
            raw_symbols = [str(body.get("symbol", "")).strip()]
        elif isinstance(raw_symbols, str):
            raw_symbols = raw_symbols.replace(",", " ").split()
        elif isinstance(raw_symbols, (list, tuple)):
            # null 项直接跳过，避免 str(None) 变成 "None" 干扰后续校验
            raw_symbols = [str(s).strip() for s in raw_symbols if s is not None]
        else:
            raise HTTPException(status_code=422,
                                detail="symbols 格式无效：应为数组或字符串")
        symbols = [s for s in raw_symbols if s]
        if not symbols:
            raise HTTPException(status_code=422, detail="symbol 不能为空")
        if core is None:
            raise HTTPException(status_code=503, detail="Agent 核心未注入")

        from data.index_mapping import IndexMapping
        from data.schemas import AnalysisTarget
        from utils.symbols import normalize_index_symbol, validate_index_symbol

        index_style = body.get("index_style")
        mapping = IndexMapping()
        targets: list[AnalysisTarget] = []
        errors: list[str] = []
        for sym in symbols:
            if not validate_index_symbol(sym):
                errors.append(f"无效的指数代码: {sym}")
                continue
            normalized = normalize_index_symbol(sym)
            entry = mapping.lookup(normalized)
            if entry is None:
                if index_style not in ("broad", "sector", "overseas"):
                    errors.append(
                        f"无法识别指数 {normalized}，请指定 index_style (broad/sector/overseas)")
                    continue
                targets.append(AnalysisTarget(
                    target_type="index", symbol=normalized, name=normalized,
                    market="a-shares", index_style=index_style))
            else:
                targets.append(AnalysisTarget(
                    target_type="index", symbol=normalized, name=entry.name,
                    market=entry.market, index_style=entry.index_style))
        if not targets:
            raise HTTPException(status_code=422, detail="；".join(errors))

        try:
            result = await asyncio.to_thread(core.index_pipeline.run, targets)
            compare = None
            if result.compare is not None:
                compare = {"headers": result.compare.headers,
                           "rows": result.compare.rows}
            payload = {
                "reports": [r.model_dump(mode="json") for r in result.reports],
                "compare": compare,
                "errors": result.errors + errors,
            }
            return JSONResponse(_json_safe(payload))
        except Exception as e:  # noqa: BLE001 — HTTP 边界兜底
            logger.error("指数分析失败: %s", e)
            return JSONResponse({"symbol": symbols[0], "error": str(e)}, status_code=500)

    @app.get("/api/v1/sessions")
    async def list_sessions():
        if sessions is None:
            return JSONResponse({"sessions": []})
        return JSONResponse({"sessions": sessions.list_sessions()})

    @app.get("/api/v1/sessions/{session_id}/messages")
    async def get_session_messages(session_id: str):
        if sessions is None:
            raise HTTPException(status_code=503, detail="会话管理未初始化")
        messages = sessions.get_messages(session_id)
        if messages is None:
            raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
        return JSONResponse({"messages": messages})

    @app.post("/api/v1/sessions")
    async def create_session():
        if sessions is None:
            raise HTTPException(status_code=503, detail="会话管理未初始化")
        sid, _ = sessions.get_or_create(None)
        return JSONResponse({"session_id": sid})

    @app.delete("/api/v1/sessions/{session_id}")
    async def delete_session(session_id: str):
        if sessions is None:
            raise HTTPException(status_code=503, detail="会话管理未初始化")
        if not sessions.delete(session_id):
            raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
        return JSONResponse({"status": "ok"})

    @app.post("/api/v1/sessions/{session_id}/clear")
    async def clear_session(session_id: str):
        if sessions is None:
            raise HTTPException(status_code=503, detail="会话管理未初始化")
        if not sessions.clear(session_id):
            raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
        return JSONResponse({"status": "ok"})

    # 挂载 Web UI 静态文件（必须放在所有 API 路由之后，"/" 挂载会兜底捕获其余路径，
    # 按注册顺序匹配，API 路由优先）
    import os

    from fastapi.staticfiles import StaticFiles

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app


app = create_app()
