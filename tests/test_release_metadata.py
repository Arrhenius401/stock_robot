"""发布元信息与各运行入口保持一致。"""
import json
import tomllib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner


def test_build_metadata_uses_single_version_source():
    from stock_robot.metadata import DISTRIBUTION_NAME

    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in project["project"]
    assert project["project"]["name"] == DISTRIBUTION_NAME
    assert project["tool"]["setuptools"]["dynamic"]["version"]["attr"] == "stock_robot.metadata.VERSION"


def test_cli_and_artifacts_use_declared_version():
    from backtest.artifacts import _app_version
    from stock_robot.cli import main
    from stock_robot.metadata import VERSION

    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip().endswith(VERSION)
    assert _app_version() == VERSION


def test_mcp_default_and_custom_metadata():
    from mcp.gateway import MCPGateway
    from mcp.schemas import JSONRPCRequest
    from mcp.server import InternalMCPServer
    from stock_robot.metadata import DISTRIBUTION_NAME, VERSION

    server = MCPGateway().get_server()
    result = server.handle_request(JSONRPCRequest(method="initialize", id=1)).result
    assert result is not None
    assert result["serverInfo"] == {"name": DISTRIBUTION_NAME, "version": VERSION}
    assert result["protocolVersion"] == InternalMCPServer.PROTOCOL_VERSION
    custom = MCPGateway(server_name="custom", server_version="9.9").get_server()
    assert custom._name == "custom"
    assert custom._version == "9.9"


def test_mcp_client_initializes_with_declared_version(monkeypatch):
    from mcp.client import ExternalMCPClient
    from stock_robot.metadata import DISTRIBUTION_NAME, VERSION

    process = MagicMock()
    process.stdout.readline.return_value = "{}"
    monkeypatch.setattr("mcp.client.subprocess.Popen", lambda *args, **kwargs: process)
    ExternalMCPClient("fake").connect()
    request = json.loads(process.stdin.write.call_args.args[0])
    assert request["params"]["clientInfo"] == {"name": DISTRIBUTION_NAME, "version": VERSION}
    assert request["params"]["protocolVersion"] == "2024-11-05"


@pytest.mark.asyncio
async def test_api_metadata_and_health(tmp_path, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from api.app import create_app
    from stock_robot.metadata import APP_NAME, VERSION

    monkeypatch.chdir(tmp_path)
    app = create_app(core=None)
    assert app.version == VERSION
    assert app.title == f"{APP_NAME} API"
    assert app.openapi()["info"]["version"] == VERSION
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/health")).json()["version"] == VERSION


def test_api_bind_fallback_reads_shared_defaults(monkeypatch):
    from stock_robot.cli import _resolve_api_bind
    from utils.config import DEFAULT_CONFIG, Config

    monkeypatch.setitem(DEFAULT_CONFIG["api"], "host", "192.0.2.10")
    monkeypatch.setitem(DEFAULT_CONFIG["api"], "port", 54321)
    empty_config = MagicMock(spec=Config)
    empty_config.get.side_effect = lambda key, default=None: default
    assert _resolve_api_bind(None, None, empty_config) == ("192.0.2.10", 54321)
    assert _resolve_api_bind("0.0.0.0", 12345, empty_config) == ("0.0.0.0", 12345)
