"""同步 SDK 的可取消 HTTP 截止机制。"""
import asyncio
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar

import httpx

request_deadline: ContextVar[float | None] = ContextVar("llm_request_deadline", default=None)


class DeadlineTransport(httpx.BaseTransport):
    """异步 I/O 在截止时间取消，避免逐块读取不断重置超时。"""

    def __init__(self) -> None:
        self._ssl_context = ssl.create_default_context()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        deadline = request_deadline.get()
        body = request.read()

        async def send() -> httpx.Response:
            remaining = None if deadline is None else max(0, deadline - time.monotonic())
            async with asyncio.timeout(remaining):
                async with httpx.AsyncClient(verify=self._ssl_context) as client:
                    response = await client.request(
                        request.method, request.url, headers=request.headers,
                        content=body, extensions=request.extensions,
                    )
                    # 异步客户端已解压正文，删除编码头以免同步 SDK 再次解压。
                    headers = [(key, value) for key, value in response.headers.multi_items()
                               if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}]
                    return httpx.Response(response.status_code, headers=headers, content=response.content)

        def run() -> httpx.Response:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(send())
            finally:
                # 系统DNS无法取消；关闭循环但不等待解析线程，取消后不会继续发送HTTP。
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()

        try:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return run()
            # 同步 SDK 也可能在事件循环线程调用；独立循环仍由 timeout 主动取消 I/O。
            with ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(run).result()
        except TimeoutError as exc:
            raise httpx.ReadTimeout("模型请求总时间预算已耗尽", request=request) from exc


def deadline_http_client() -> httpx.Client:
    return httpx.Client(transport=DeadlineTransport(), follow_redirects=True)
