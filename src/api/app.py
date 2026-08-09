"""FastAPI HTTP API — REST + SSE 流式接口"""
import json
import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)


def create_app(registry=None, planner=None, executor=None, memory=None):
    app = FastAPI(title="Stock Robot API", version="0.1.0",
                  description="AI 驱动的股票分析研报助手 HTTP API")

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/v1/tools")
    async def list_tools():
        if registry is None: return JSONResponse({"tools": []})
        return JSONResponse({"tools": registry.list_all_summary(), "total": len(registry.list_all())})

    @app.post("/api/v1/chat")
    async def chat(request: Request):
        body = await request.json()
        message = body.get("message", "").strip()
        session_id = request.headers.get("X-Session-Id", "default")
        if not message: raise HTTPException(status_code=422, detail="message 不能为空")
        if planner is None or executor is None:
            return JSONResponse({"response": f"[API 模式] 收到消息: {message}（Agent 核心未注入）",
                                "session_id": session_id})

        try:
            plan = await planner.plan(message)
            exec_result = await executor.execute(plan, session_id=session_id)
            return JSONResponse({
                "response": exec_result.get("summary", ""),
                "plan": {"goal": plan.goal, "steps": [
                    {"id": s.id, "description": s.description, "status": s.status.value}
                    for s in plan.steps
                ]},
                "session_id": session_id,
            })
        except Exception as e:
            logger.error("Agent 对话失败: %s", e)
            return JSONResponse({"response": f"处理请求时出错: {e}", "session_id": session_id},
                               status_code=500)

    @app.post("/api/v1/chat/stream")
    async def chat_stream(request: Request):
        body = await request.json()
        message = body.get("message", "").strip()
        if not message: raise HTTPException(status_code=422, detail="message 不能为空")

        async def event_stream():
            yield f"data: {json.dumps({'type': 'start', 'message': message})}\n\n"
            if planner is None or executor is None:
                yield f"data: {json.dumps({'type': 'text', 'content': '[API 模式] Agent 核心未注入'})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return
            try:
                plan = await planner.plan(message)
                yield f"data: {json.dumps({'type': 'plan', 'goal': plan.goal, 'steps': [s.description for s in plan.steps]})}\n\n"
                exec_result = await executor.execute(plan, session_id=request.headers.get("X-Session-Id", "default"))
                yield f"data: {json.dumps({'type': 'result', 'summary': exec_result.get('summary', '')})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

    @app.post("/api/v1/analyze")
    async def analyze(request: Request):
        body = await request.json()
        symbol = body.get("symbol", "").strip()
        if not symbol: raise HTTPException(status_code=422, detail="symbol 不能为空")
        return JSONResponse({"status": "not_implemented", "symbol": symbol,
                            "message": "analyze 端点将在后续版本中实现完整的 Pipeline 调用"}, status_code=501)

    @app.post("/api/v1/index")
    async def index(request: Request):
        body = await request.json()
        symbol = body.get("symbol", "").strip()
        if not symbol: raise HTTPException(status_code=422, detail="symbol 不能为空")
        return JSONResponse({"status": "not_implemented", "symbol": symbol,
                            "message": "index 端点将在后续版本中实现完整的 IndexPipeline 调用"}, status_code=501)

    # 挂载 Web UI 静态文件（必须放在所有 API 路由之后，"/" 挂载会兜底捕获其余路径，
    # 按注册顺序匹配，API 路由优先）
    from fastapi.staticfiles import StaticFiles
    import os

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app


app = create_app()
