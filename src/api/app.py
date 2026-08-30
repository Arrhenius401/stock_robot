"""FastAPI HTTP API — REST + SSE 流式接口"""
import ast
import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import ValidationError

from agent.chat import ChatResponder
from agent.executor import Executor
from agent.graph import DEFAULT_CHECKPOINT_DIR
from agent.memory import TaskStatus
from agent.planner import Planner
from agent.react import ReActExecutor
from api.configuration import create_configuration_router
from api.message_content import encode_message_content, normalize_message_content
from api.report_library import (
    ReportLibraryError,
    get_report_detail,
    list_reports,
    resolve_download_path,
)
from api.runtime import RuntimeManager, RuntimeSnapshot
from api.session_titles import SessionTitleRefiner, derive_session_title
from api.sessions import is_draft_session_id
from push.models import Channel, Subscription

logger = logging.getLogger(__name__)
_TITLE_REFINE_TIMEOUT_SECONDS = 0.25


def _json_safe(payload) -> dict:
    """将 payload 中的非 JSON 类型（date 等）转为字符串后重新解析"""
    return json.loads(json.dumps(payload, ensure_ascii=False, default=str))


def _structured_tool_results(plan, memory) -> list[dict]:
    """从计划步骤与 memory 工具消息配对出结构化结果

    配对前提：Executor 只在步骤成功时写 role=="tool" 消息，且本轮消息
    必然位于 memory 消息列表尾部（无其他写入者），故取最后 done_count 条
    tool 消息按序配对——对 max_messages 截断免疫。
    agent 降级路径先截断 react 孤立消息再走 Executor，该前提仍然成立。
    """
    done_count = sum(1 for s in plan.steps
                     if s.tool_name and s.status == TaskStatus.DONE)
    tool_msgs = [m for m in memory.messages
                 if m["role"] == "tool"][-done_count:] if done_count else []
    results = []
    for step in plan.steps:
        if not step.tool_name:
            continue
        content = None
        message_id = None
        if step.status == TaskStatus.DONE and tool_msgs:
            tool_message = tool_msgs.pop(0)
            content = tool_message["content"]
            message_id = tool_message.get("message_id")
        elif step.status == TaskStatus.DONE:
            # 配对不变量被破坏时（理论上不应发生），记录日志便于诊断
            logger.warning("结构化工具结果配对不完整: 步骤 %s 缺少对应 tool 消息", step.id)
        results.append({
            "tool": step.tool_name,
            "symbol": (step.tool_args or {}).get("symbol"),
            "status": step.status.value,
            "content": content,
            "message_id": message_id,
        })
    return results


def _extract_stock_reports(
    tool_results: list[dict],
) -> list[tuple[str, dict, int | None]]:
    """提取全部成功的 analyze_stock 工具结果并规范化结构化报告。"""
    prefix = "[analyze_stock] success:"
    reports = []
    for result in tool_results:
        if result.get("tool") != "analyze_stock":
            continue
        if result.get("status") not in ("done", "success"):
            continue
        content = result.get("content")
        report: dict | None = content if isinstance(content, dict) else None
        if isinstance(content, str):
            candidate = content.removeprefix(prefix).strip() \
                if content.startswith(prefix) else content.strip()
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                try:
                    parsed = ast.literal_eval(candidate)
                except (SyntaxError, ValueError):
                    continue
            if isinstance(parsed, dict):
                report = parsed
        if report is None:
            continue

        symbol = report.get("symbol") or report.get("code")
        if not isinstance(symbol, str) or not symbol.strip():
            continue
        comments = report.get("comments")
        commentary = report.get("commentary", "")
        if not isinstance(commentary, str):
            commentary = ""
        if not commentary and isinstance(comments, list):
            commentary = "\n\n".join(
                str(comment) for comment in comments if comment is not None)
        payload = {
            "symbol": symbol.strip(),
            "name": report.get("name", ""),
            "overview": report.get("overview")
            if isinstance(report.get("overview"), dict) else {},
            "score": report.get("score")
            if isinstance(report.get("score"), dict) else {},
            "score_rows": report.get("score_rows")
            if isinstance(report.get("score_rows"), list) else [],
            "dimensions": report.get("dimensions")
            if isinstance(report.get("dimensions"), dict) else {},
            "commentary": commentary,
            "generated_at": report.get("generated_at"),
        }
        message_id = result.get("message_id")
        reports.append((
            payload["symbol"],
            payload,
            message_id if isinstance(message_id, int) else None,
        ))
    return reports


def _persist_artifacts_if_present(
        manager, session_id: str, tool_results: list[dict], *, memory) -> list[dict]:
    """逐项保存工具结果中的报告；单项故障不影响其他成果与聊天。"""
    events = []
    for symbol, payload, message_id in _extract_stock_reports(tool_results):
        try:
            artifact = manager.save_artifact(
                session_id,
                kind="stock_report",
                symbol=symbol,
                payload=payload,
                message_id=message_id,
                memory=memory,
            )
            events.append({"artifact": artifact, "persisted": True})
        except Exception as exc:  # noqa: BLE001 — 持久化边界失败不能中断聊天
            logger.error("报告成果持久化失败，会话 %s: %s", session_id, exc)
            now = datetime.now().astimezone().timestamp()
            events.append({
                "artifact": {
                    "artifact_id": None,
                    "session_id": session_id,
                    "message_id": message_id,
                    "kind": "stock_report",
                    "symbol": symbol,
                    "payload": _json_safe(payload),
                    "created_at": now,
                    "updated_at": now,
                },
                "persisted": False,
            })
    return events


def _session_metadata(manager, session_id: str | None) -> dict | None:
    """读取单个会话的标题元数据。"""
    if not session_id:
        return None
    return next(
        (item for item in manager.list_sessions()
         if item.get("session_id") == session_id),
        None,
    )


async def _refine_session_title(
        manager, session_id: str, message: str, fallback: str, model,
        queue: asyncio.Queue) -> None:
    """润色并持久化首轮自动标题，仅在标题真实变化时发送事件。"""
    title = await SessionTitleRefiner(model).refine(message, fallback)
    if title == fallback:
        return
    if manager.maybe_update_title(session_id, title, source="llm"):
        await queue.put({
            "type": "session_title",
            "session_id": session_id,
            "title": title,
        })


async def _finish_title_refinement(
        manager, session_id: str, message: str, fallback: str, model,
        queue: asyncio.Queue) -> None:
    """在请求生命周期内等待短时标题润色并完整回收任务。"""
    task = asyncio.create_task(_refine_session_title(
        manager, session_id, message, fallback, model, queue))
    try:
        done, _ = await asyncio.wait(
            {task}, timeout=_TITLE_REFINE_TIMEOUT_SECONDS)
        if not done:
            logger.warning("会话标题润色超时，会话 %s 保留本地标题", session_id)
            return
        await task
    except Exception as exc:  # noqa: BLE001 — 标题失败不能中断聊天
        logger.warning("会话标题润色任务失败，会话 %s: %s", session_id, exc)
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.debug("已取消并回收会话 %s 的标题润色任务", session_id)


async def _agent_fallback(executor, memory, goal: str) -> tuple[str, dict, list[dict]]:
    """agent 模式降级：以 plan 单步执行目标，返回与 plan 模式一致的响应结构"""
    from agent.memory import Plan, TaskStep

    plan = Plan(goal=goal, steps=[TaskStep(id="step-1", description=goal)])
    plan = await executor.execute(plan)
    done = sum(1 for s in plan.steps if s.status == TaskStatus.DONE)
    return (
        f"目标: {goal}\n完成: {done}/1 步骤",
        {"goal": goal, "mode": "plan", "steps": [
            {"id": s.id, "description": s.description, "status": s.status.value}
            for s in plan.steps]},
        _structured_tool_results(plan, memory),
    )


def _build_signal_payload(final_score: float, config: Any | None = None) -> dict:
    """从配置加载信号映射并构造 API 信号字段（配置损坏时抛 ValueError 由边界兜底）"""
    from report.signal import SIGNAL_LABELS, derive_signal, load_signal_config
    from utils.config import Config

    cfg = load_signal_config(config or Config())
    level = derive_signal(final_score, cfg.thresholds)
    action = cfg.actions[level]
    return {"level": level, "label": SIGNAL_LABELS[level],
            "action": action.action, "position": action.position}


def _reload_push_if_active(push: Any) -> None:
    """仅对已启用的推送调度器触发订阅重载。"""
    if push is not None and push is not False:
        push.reload()


def _initial_core_factory(core: Any):
    """首个快照复用 CLI 已构建 core，后续热重载再构建新 core。"""
    initial = True

    def build(config):
        nonlocal initial
        if initial:
            initial = False
            return core
        from api.bootstrap import build_agent_core

        # 热重载必须暴露 LLM 初始化失败，避免静默降级后误报 applied=true
        return build_agent_core(config, strict_llm=True)

    return build


def create_app(
    core=None,
    sessions=None,
    push: Any = None,
    runtime: RuntimeManager | None = None,
):
    # 生产路径由 RuntimeManager 独占 core、执行器和推送调度器的初始所有权。
    # 测试传入 push=False、推送桩或 runtime 桩时保持既有注入行为，不创建真实依赖。
    push_store: Any = None
    push_executor: Any = None
    if runtime is None and core is not None and push is None:
        from utils.config import Config

        runtime = RuntimeManager(Config(), core_factory=_initial_core_factory(core))
        snapshot = runtime.snapshot()
        push_store = snapshot.push_store
        push_executor = snapshot.push_executor
        push = snapshot.push_scheduler

    app = FastAPI(title="Stock Robot API", version="0.1.0",
                  description="AI 驱动的股票分析研报助手 HTTP API")

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])
    app.include_router(create_configuration_router(runtime=runtime))

    if sessions is None and core is not None:
        from api.sessions import SessionManager, SessionStore
        from utils.config import Config
        sessions = SessionManager(SessionStore(Config().config_dir / "sessions.db"))

    def _runtime_snapshot() -> RuntimeSnapshot | None:
        """在入口处读取一次运行时快照，后续链路只持有该对象。"""
        if runtime is None:
            return None
        return runtime.snapshot()

    def _snapshot_core(snapshot: RuntimeSnapshot | None):
        if snapshot is not None:
            return snapshot.core
        return core

    def _build_agent(session_memory, agent_core):
        """按会话现建轻量 Planner/Executor/ChatResponder（无状态，构造廉价）"""
        # 调用方已在请求入口冻结快照并校验 core 注入，复制到局部变量并断言收窄类型
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
        snapshot = _runtime_snapshot()
        agent_core = _snapshot_core(snapshot)
        if agent_core is None:
            return JSONResponse({"tools": []})
        return JSONResponse({"tools": agent_core.registry.list_all_summary(),
                             "total": len(agent_core.registry.list_all())})

    def _reports_root() -> Path:
        return Path.cwd() / "reports"

    def _raise_report_error(error: ReportLibraryError) -> None:
        raise HTTPException(status_code=error.status_code, detail=str(error))

    @app.get("/api/v1/reports")
    async def reports(
            report_type: str | None = Query(default=None, alias="type"),
            query: str | None = None):
        if report_type not in (None, "stock", "index", "backtest"):
            raise HTTPException(status_code=422, detail="报告类型无效")
        items = list_reports(_reports_root(), report_type=report_type, query=query)
        return JSONResponse({"reports": [item.to_dict() for item in items],
                             "total": len(items)})

    @app.get("/api/v1/reports/{report_id}")
    async def report_detail(report_id: str):
        try:
            detail = get_report_detail(_reports_root(), report_id)
        except ReportLibraryError as error:
            _raise_report_error(error)
        return JSONResponse(detail.to_dict())

    @app.get("/api/v1/reports/{report_id}/download")
    async def report_download(report_id: str):
        try:
            path = resolve_download_path(_reports_root(), report_id)
        except ReportLibraryError as error:
            _raise_report_error(error)
        return FileResponse(path, media_type="text/markdown", filename=path.name)

    @app.post("/api/v1/chat")
    async def chat(request: Request):
        body = await request.json()
        message = str(body.get("message", "")).strip()
        session_id = _extract_session_id(body, request)
        if not message:
            raise HTTPException(status_code=422, detail="message 不能为空")
        snapshot = _runtime_snapshot()
        agent_core = _snapshot_core(snapshot)
        if agent_core is None or sessions is None:
            return JSONResponse({"response": f"[API 模式] 收到消息: {message}（Agent 核心未注入）",
                                 "session_id": session_id or ""})

        try:
            sid, memory = sessions.get_or_create_for_message(session_id, message)
            planner, executor, chat_responder = _build_agent(memory, agent_core)
            plan = await asyncio.to_thread(planner.plan, message)
            if plan.mode == "chat":
                # 闲聊：ChatResponder 普通会话回复，跳过执行器并写入会话消息
                started_at = time.monotonic()
                normalized = await chat_responder.reply_content(message, memory)
                reply = normalized["text"]
                memory.add_message(
                    "assistant", encode_message_content(
                        reply,
                        normalized.get("thinking", ""),
                        thinking_duration_seconds=(
                            time.monotonic() - started_at
                            if normalized.get("thinking") else None
                        )))
                return JSONResponse({
                    "response": reply,
                    "plan": {"goal": plan.goal, "mode": "chat", "steps": []},
                    "tool_results": [],
                    "session_id": sid,
                })
            if plan.mode == "agent":
                # agent 模式：自主循环执行；无模型或异常降级 plan 单步
                react = ReActExecutor(registry=agent_core.registry, memory=memory,
                                      model=agent_core.model, session_id=sid,
                                      persist_dir=str(DEFAULT_CHECKPOINT_DIR))
                msg_snapshot = len(memory.messages)
                try:
                    outcome = await react.run(message)
                    tool_results = [{
                        "tool": tc["tool"],
                        "symbol": tc["args"].get("symbol"),
                        "status": "done" if tc["status"] == "success" else "error",
                        "content": tc.get("summary", ""),
                        "message_id": tc.get("message_id"),
                    } for tc in outcome.tool_calls]
                    artifact_results = [
                        {**result, "content": tc.get("raw_output", result["content"])}
                        for result, tc in zip(tool_results, outcome.tool_calls, strict=True)
                    ]
                    _persist_artifacts_if_present(
                        sessions, sid, artifact_results, memory=memory)
                    return JSONResponse({
                        "response": outcome.final_reply,
                        **({"thinking": outcome.thinking} if outcome.thinking else {}),
                        "plan": {"goal": plan.goal, "mode": "agent", "steps": []},
                        "tool_results": tool_results,
                        "session_id": sid,
                    })
                except Exception as e:  # noqa: BLE001 — agent 失败降级 plan 单步
                    logger.warning("agent 模式失败，降级 plan 单步: %s", e)
                    # 截断 react 中途写入的孤立 tool 消息，避免污染下一轮上下文
                    # （add_message 超限时会重绑定列表对象，须用重绑定而非 del 切片）
                    memory.messages = memory.messages[:msg_snapshot]
                    response, plan_payload, tool_results = await _agent_fallback(
                        executor, memory, message)
                    memory.add_message(
                        "assistant", encode_message_content(response, "思考中..."))
                    _persist_artifacts_if_present(
                        sessions, sid, tool_results, memory=memory)
                    return JSONResponse({
                        "response": response,
                        "plan": plan_payload,
                        "tool_results": tool_results,
                        "session_id": sid,
                    })
            plan = await executor.execute(plan)
            done = sum(1 for s in plan.steps if s.status == TaskStatus.DONE)
            total = len(plan.steps)
            tool_results = _structured_tool_results(plan, memory)
            _persist_artifacts_if_present(
                sessions, sid, tool_results, memory=memory)
            return JSONResponse({
                "response": f"目标: {plan.goal}\n完成: {done}/{total} 步骤",
                "plan": {"goal": plan.goal, "mode": "plan", "steps": [
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
        snapshot = _runtime_snapshot()
        agent_core = _snapshot_core(snapshot)
        stream_core = agent_core

        async def event_stream():
            yield f"data: {json.dumps({'type': 'start', 'message': message}, ensure_ascii=False)}\n\n"
            if stream_core is None or sessions is None:
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
                    current_core = stream_core
                    assert current_core is not None
                    sid, memory = manager.get_or_create_for_message(session_id, message)
                    local_title = derive_session_title(message)
                    should_refine_title = False
                    try:
                        previous_meta = _session_metadata(manager, sid)
                        is_first_turn = (
                            previous_meta is not None
                            and previous_meta.get("message_count") == 1
                        )
                        if (is_first_turn and previous_meta is not None
                                and previous_meta.get("title_source") != "manual"):
                            manager.maybe_update_title(
                                sid, local_title, source="local")
                        current_meta = _session_metadata(manager, sid) or {}
                        local_title = str(current_meta.get("title") or local_title)
                        should_refine_title = (
                            is_first_turn
                            and current_meta.get("title_source") != "manual"
                        )
                    except Exception as exc:  # noqa: BLE001 — 标题元数据失败不影响聊天
                        local_title = derive_session_title(message)
                        is_first_turn = True
                        should_refine_title = False
                        logger.warning(
                            "会话标题初始化失败，会话 %s 保留本地标题: %s", sid, exc)
                    if is_first_turn:
                        await queue.put({
                            "type": "session_title",
                            "session_id": sid,
                            "title": local_title,
                        })

                    async def finish_title() -> None:
                        if not should_refine_title:
                            return
                        await _finish_title_refinement(
                            manager, sid, message, local_title,
                            current_core.model, queue,
                        )

                    planner, executor, chat_responder = _build_agent(memory, current_core)
                    plan = await asyncio.to_thread(planner.plan, message)
                    if plan.mode == "chat":
                        # 闲聊：模型 token 到达即转成正文增量，避免用户等待整段回复。
                        reply_parts: list[str] = []
                        thinking_parts: list[str] = []
                        started_at = time.monotonic()
                        async for chunk in chat_responder.stream_reply_content(message, memory):
                            if chunk["text"]:
                                reply_parts.append(chunk["text"])
                                await queue.put({"type": "text_delta",
                                                 "content": chunk["text"]})
                            if chunk.get("thinking"):
                                thinking_parts.append(chunk["thinking"])
                                await queue.put({"type": "thinking",
                                                 "content": chunk["thinking"]})
                        reply = "".join(reply_parts)
                        normalized = {"text": reply}
                        if thinking_parts:
                            normalized["thinking"] = "".join(thinking_parts)
                        memory.add_message(
                            "assistant", encode_message_content(
                                reply,
                                normalized.get("thinking", ""),
                                thinking_duration_seconds=(
                                    time.monotonic() - started_at
                                    if normalized.get("thinking") else None
                                )))
                        event = {"type": "text", "content": reply}
                        if normalized.get("thinking"):
                            event["thinking"] = normalized["thinking"]
                        await queue.put(event)
                        await finish_title()
                        await queue.put({"type": "done"})
                        return
                    if plan.mode == "agent":
                        # agent 模式：plan 事件空步骤（携带 session_id 供前端接管会话），
                        # 实时 thinking/tool_call/tool_result 事件，最终 text 事件收尾
                        await queue.put({"type": "plan", "goal": plan.goal,
                                         "steps": [], "session_id": sid})
                        react = ReActExecutor(
                            registry=current_core.registry, memory=memory,
                            model=current_core.model,
                            session_id=sid, persist_dir=str(DEFAULT_CHECKPOINT_DIR))
                        msg_snapshot = len(memory.messages)
                        try:
                            outcome = await react.run(message,
                                                      on_event=queue.put_nowait)
                            tool_results = [{
                                "tool": tc["tool"],
                                "symbol": tc["args"].get("symbol"),
                                "status": "done"
                                if tc["status"] == "success" else "error",
                                "content": tc.get("summary", ""),
                                "message_id": tc.get("message_id"),
                            } for tc in outcome.tool_calls]
                            event = {"type": "text", "content": outcome.final_reply}
                            if outcome.thinking:
                                event["thinking"] = outcome.thinking
                            await queue.put(event)
                            artifact_results = [
                                {**result,
                                 "content": tc.get("raw_output", result["content"])}
                                for result, tc in zip(
                                    tool_results, outcome.tool_calls, strict=True)
                            ]
                            for artifact_event in _persist_artifacts_if_present(
                                    manager, sid, artifact_results, memory=memory):
                                await queue.put({"type": "artifact", **artifact_event})
                        except Exception as e:  # noqa: BLE001 — agent 失败降级 plan 单步
                            logger.warning("agent 模式失败，降级 plan 单步: %s", e)
                            # 截断 react 中途写入的孤立 tool 消息，避免污染下一轮上下文
                            # （add_message 超限时会重绑定列表对象，须用重绑定而非 del 切片）
                            memory.messages = memory.messages[:msg_snapshot]
                            summary, _, tool_results = await _agent_fallback(
                                executor, memory, message)
                            # 降级结果也写入会话，确保切换会话后正文与思考占位仍可恢复。
                            memory.add_message(
                                "assistant",
                                encode_message_content(summary, "思考中..."),
                            )
                            await queue.put({"type": "result",
                                             "summary": summary,
                                             "tool_results": tool_results})
                            for artifact_event in _persist_artifacts_if_present(
                                    manager, sid, tool_results, memory=memory):
                                await queue.put({"type": "artifact", **artifact_event})
                        await finish_title()
                        await queue.put({"type": "done"})
                        return
                    await queue.put({"type": "plan", "goal": plan.goal,
                                     "mode": "plan",
                                     "steps": [s.description for s in plan.steps],
                                     "session_id": sid})
                    plan = await executor.execute(plan, on_progress=on_progress)
                    done = sum(1 for s in plan.steps if s.status == TaskStatus.DONE)
                    total = len(plan.steps)
                    tool_results = _structured_tool_results(plan, memory)
                    await queue.put({"type": "result",
                                     "summary": f"目标: {plan.goal}\n完成: {done}/{total} 步骤",
                                     "tool_results": tool_results})
                    for artifact_event in _persist_artifacts_if_present(
                            manager, sid, tool_results, memory=memory):
                        await queue.put({"type": "artifact", **artifact_event})
                    await finish_title()
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
                    try:
                        await task
                    except asyncio.CancelledError:
                        logger.debug("客户端断连，已取消并回收流式对话任务")

        return StreamingResponse(event_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

    @app.post("/api/v1/analyze")
    async def analyze(request: Request):
        body = await request.json()
        symbol = str(body.get("symbol", "")).strip()
        if not symbol:
            raise HTTPException(status_code=422, detail="symbol 不能为空")
        snapshot = _runtime_snapshot()
        agent_core = _snapshot_core(snapshot)
        if agent_core is None:
            raise HTTPException(status_code=503, detail="Agent 核心未注入")

        from utils.symbols import normalize_symbol, resolve_name, validate_symbol
        if not validate_symbol(symbol):
            raise HTTPException(status_code=422, detail=f"无效的股票代码: {symbol}")
        symbol = normalize_symbol(symbol)
        name = await asyncio.to_thread(resolve_name, symbol) or symbol

        try:
            results, commentary, ctx = await asyncio.to_thread(
                agent_core.pipeline.run, symbol, name
            )
            from report.scoring import (
                compute_price_info,
                compute_score_summary,
                with_commentary_fallback,
            )

            summary = compute_score_summary(results)
            commentary = with_commentary_fallback(
                commentary, summary, no_llm=agent_core.llm is None,
            )
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
                "signal": _build_signal_payload(
                    summary.final_score,
                    snapshot.config if snapshot is not None else None,
                ),
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
        snapshot = _runtime_snapshot()
        agent_core = _snapshot_core(snapshot)
        if agent_core is None:
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
            result = await asyncio.to_thread(agent_core.index_pipeline.run, targets)
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

    @app.post("/api/v1/industry-mapping/update")
    async def industry_mapping_update(request: Request):
        body = await request.json()
        symbol = str(body.get("symbol", "")).strip()
        if not symbol:
            raise HTTPException(status_code=422, detail="symbol 不能为空")

        from data.industry_mapping_builder import IndustryMappingError, update_symbol
        from utils.symbols import normalize_symbol, validate_symbol
        if not validate_symbol(symbol):
            raise HTTPException(status_code=422, detail=f"无效的股票代码: {symbol}")
        symbol = normalize_symbol(symbol)

        try:
            result = await asyncio.to_thread(update_symbol, symbol)
            return JSONResponse(_json_safe(result))
        except IndustryMappingError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.get("/api/v1/industry-mapping/{symbol}")
    async def industry_mapping_get(symbol: str):
        from data.industry_classifier import IndustryClassifier

        classification = await asyncio.to_thread(IndustryClassifier().lookup, symbol)
        return JSONResponse({
            "symbol": symbol,
            "sw_level1": classification.sw_level1,
            "sw_level2": classification.sw_level2,
            "style_category": classification.style_category,
        })

    @app.get("/api/v1/sessions")
    async def list_sessions():
        if sessions is None:
            return JSONResponse({"sessions": []})
        return JSONResponse({"sessions": sessions.list_sessions()})

    @app.get("/api/v1/sessions/{session_id}/messages")
    async def get_session_messages(session_id: str):
        if is_draft_session_id(session_id):
            raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
        if sessions is None:
            raise HTTPException(status_code=503, detail="会话管理未初始化")
        detail = sessions.get_session_detail(session_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
        for message in detail.get("messages", []):
            if message.get("role") == "assistant":
                normalized = normalize_message_content(message.get("content", ""))
                message["content"] = normalized["text"]
                if normalized.get("thinking"):
                    message["thinking"] = normalized["thinking"]
                if normalized.get("thinking_duration_seconds") is not None:
                    message["thinking_duration_seconds"] = (
                        normalized["thinking_duration_seconds"]
                    )
        return JSONResponse(detail)

    @app.get("/api/v1/sessions/{session_id}/artifacts/{artifact_id}")
    async def get_session_artifact(session_id: str, artifact_id: str):
        if is_draft_session_id(session_id):
            raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
        if sessions is None:
            raise HTTPException(status_code=503, detail="会话管理未初始化")
        if sessions.get_session_detail(session_id) is None:
            raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
        artifact = sessions.get_artifact(artifact_id)
        if artifact is None or artifact.get("session_id") != session_id:
            raise HTTPException(status_code=404, detail=f"成果不存在: {artifact_id}")
        return JSONResponse({"artifact": artifact})

    @app.patch("/api/v1/sessions/{session_id}")
    async def rename_session(session_id: str, request: Request):
        if is_draft_session_id(session_id):
            raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
        if sessions is None:
            raise HTTPException(status_code=503, detail="会话管理未初始化")
        body = await request.json()
        raw_title = body.get("title")
        if not isinstance(raw_title, str):
            raise HTTPException(status_code=422, detail="title 必须是字符串")
        title = raw_title.strip()
        if not 1 <= len(title) <= 20:
            raise HTTPException(status_code=422, detail="title 长度必须为 1–20 字")
        if not sessions.rename(session_id, title, manual=True):
            raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
        return JSONResponse({
            "session_id": session_id,
            "title": title,
            "title_source": "manual",
        })

    @app.delete("/api/v1/sessions/{session_id}")
    async def delete_session(session_id: str):
        if is_draft_session_id(session_id):
            raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
        if sessions is None:
            raise HTTPException(status_code=503, detail="会话管理未初始化")
        if not sessions.delete(session_id):
            raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
        return JSONResponse({"status": "ok"})

    # ---- 订阅推送管理 ----

    def _require_push(snapshot: RuntimeSnapshot | None = None):
        # store 优先取注入对象的公开属性（测试桩），否则取自动构建的闭包变量
        # （PushScheduler 的 _store 为私有属性不外露）
        store = (snapshot.push_store if snapshot is not None
                 else getattr(push, "store", None) or push_store)
        if store is None:
            raise HTTPException(status_code=503, detail="推送模块未初始化")
        return store

    def _parse_subscription(body: dict):
        # 返回类型不标注 Subscription（模型已模块级导入），校验失败统一转 422；
        # enabled 走全量替换语义（PUT 缺省视为启用，create 缺省默认 True）
        try:
            return Subscription(
                name=str(body.get("name", "")).strip(),
                symbols=list(body.get("symbols") or []),
                channel=cast(Channel, str(body.get("channel", ""))),
                time=str(body.get("time", "")),
                enabled=bool(body.get("enabled", True)),
            )
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=str(e.errors())) from e

    def _check_symbol_limit(sub: Subscription, snapshot: RuntimeSnapshot | None = None):
        from utils.config import Config
        config = snapshot.config if snapshot is not None else Config()
        limit = int(config.get("push.max_symbols_per_subscription", 20))
        if len(sub.symbols) > limit:
            raise HTTPException(
                status_code=422,
                detail=f"标的数量 {len(sub.symbols)} 超过上限 {limit}")

    @app.get("/api/v1/subscriptions")
    async def list_subscriptions():
        snapshot = _runtime_snapshot()
        store = _require_push(snapshot)
        items = []
        for sub in store.list():
            item = sub.model_dump(mode="json")
            item["last_run"] = store.last_run(sub.id) if sub.id else None
            items.append(item)
        return JSONResponse({"subscriptions": items})

    @app.post("/api/v1/subscriptions")
    async def create_subscription(request: Request):
        snapshot = _runtime_snapshot()
        store = _require_push(snapshot)
        body = await request.json()
        sub = _parse_subscription(body)
        _check_symbol_limit(sub, snapshot)
        sub.created_at = datetime.now().astimezone().isoformat()
        sub_id = store.create(sub)
        _reload_push_if_active(
            snapshot.push_scheduler if snapshot is not None else push)
        # 先展开 model_dump（id 为 None），再覆盖真实 id
        return JSONResponse({**sub.model_dump(mode="json"), "id": sub_id})

    @app.get("/api/v1/subscriptions/{subscription_id}")
    async def get_subscription(subscription_id: int):
        snapshot = _runtime_snapshot()
        store = _require_push(snapshot)
        sub = store.get(subscription_id)
        if sub is None:
            raise HTTPException(status_code=404,
                                detail=f"订阅不存在: {subscription_id}")
        item = sub.model_dump(mode="json")
        item["last_run"] = store.last_run(subscription_id)
        return JSONResponse(item)

    @app.put("/api/v1/subscriptions/{subscription_id}")
    async def update_subscription(subscription_id: int, request: Request):
        snapshot = _runtime_snapshot()
        store = _require_push(snapshot)
        if store.get(subscription_id) is None:
            raise HTTPException(status_code=404,
                                detail=f"订阅不存在: {subscription_id}")
        body = await request.json()
        sub = _parse_subscription(body)
        _check_symbol_limit(sub, snapshot)
        sub.id = subscription_id
        store.update(sub)
        _reload_push_if_active(
            snapshot.push_scheduler if snapshot is not None else push)
        return JSONResponse(sub.model_dump(mode="json"))

    @app.delete("/api/v1/subscriptions/{subscription_id}")
    async def delete_subscription(subscription_id: int):
        snapshot = _runtime_snapshot()
        store = _require_push(snapshot)
        if not store.delete(subscription_id):
            raise HTTPException(status_code=404,
                                detail=f"订阅不存在: {subscription_id}")
        _reload_push_if_active(
            snapshot.push_scheduler if snapshot is not None else push)
        return JSONResponse({"status": "ok"})

    @app.post("/api/v1/subscriptions/{subscription_id}/run")
    async def run_subscription(subscription_id: int):
        snapshot = _runtime_snapshot()
        store = _require_push(snapshot)
        sub = store.get(subscription_id)
        if sub is None:
            raise HTTPException(status_code=404,
                                detail=f"订阅不存在: {subscription_id}")
        executor = (snapshot.push_executor if snapshot is not None
                    else getattr(push, "executor", None) or push_executor)
        if executor is None:
            raise HTTPException(status_code=503, detail="推送执行器未初始化")
        # 后台线程触发推送，避免长耗时阻塞 HTTP 请求
        threading.Thread(target=executor.run_subscription, args=(sub,),
                         daemon=True).start()
        return JSONResponse({"status": "triggered"})

    # 挂载 Web UI 静态文件（必须放在所有 API 路由之后，"/" 挂载会兜底捕获其余路径，
    # 按注册顺序匹配，API 路由优先）
    import os

    from fastapi.staticfiles import StaticFiles

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app


app = create_app()
