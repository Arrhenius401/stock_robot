"""外部 MCP Client — 通过 subprocess stdio 连接第三方 MCP Server"""
import json
import logging
import subprocess
from mcp.schemas import MCPToolDefinition

logger = logging.getLogger(__name__)


class ExternalMCPClient:
    def __init__(self, command: str, args: list[str] | None = None, env: dict | None = None):
        self._command = command
        self._args = args or []
        self._env = env
        self._process: subprocess.Popen | None = None
        self._request_id = 0

    def connect(self):
        cmd = [self._command] + self._args
        try:
            self._process = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", env=self._env,
            )
        except FileNotFoundError:
            raise RuntimeError(f"无法启动 MCP Server: 命令不存在 — {' '.join(cmd)}")
        except Exception as e:
            raise RuntimeError(f"启动 MCP Server 失败: {e}")

        init_req = json.dumps({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {"protocolVersion": "2024-11-05",
                       "clientInfo": {"name": "stock-robot", "version": "0.1.0"}},
        }, ensure_ascii=False)
        self._process.stdin.write(init_req + "\n")
        self._process.stdin.flush()
        init_resp = self._process.stdout.readline()
        logger.debug("MCP Server 初始化响应: %s", init_resp.strip())

    def disconnect(self):
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                self._process.kill()
            finally:
                self._process = None

    @property
    def is_connected(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def list_tools(self) -> list[MCPToolDefinition]:
        resp = self._send_request_sync("tools/list")
        tools_data = resp.get("result", {}).get("tools", [])
        return [MCPToolDefinition.from_dict(t) for t in tools_data]

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send_request_sync(self, method: str, params: dict | None = None) -> dict:
        if not self._process or self._process.poll() is not None:
            raise RuntimeError("MCP Server 未连接或已退出")
        req = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
        if params:
            req["params"] = params
        self._process.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self._process.stdin.flush()
        raw = self._process.stdout.readline()
        if not raw:
            raise RuntimeError(f"MCP Server 无响应 (method={method})")
        return json.loads(raw)
