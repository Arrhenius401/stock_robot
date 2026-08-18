# API 端口配置化与信号系统实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `api` 命令默认端口改为 25618 且支持配置文件自定义；分析报告新增进攻/观望/防御信号与结构化动作映射。

**Architecture:** 配置默认值集中在 `utils/config.py` 的 `DEFAULT_CONFIG`（Config 深合并天然兜底）；信号推导与映射为独立纯函数模块 `report/signal.py`（回测契约），CLI 报告与 API 响应两处集成；api 命令端口解析提取为纯函数便于测试。

**Tech Stack:** Python 3.11+、click、FastAPI、Jinja2、pytest、pytest-mock（mocker）、pyright、ruff

**已探针的 API 事实（勿重复探测）：**

- `build_report`（src/report/scoring.py:82）唯一调用者是 `src/stock_robot/cli.py:182`（report 命令）；API 的 `/api/v1/analyze` 不走 build_report，直接构造 payload（src/api/app.py:206-257）
- cli.py 的 `api` 命令（src/stock_robot/cli.py:423-437）：`--host` 默认 `"127.0.0.1"`，`--port` 默认 `8000`，函数内 `from utils.config import Config` 等局部导入
- `Config._merge`（src/utils/config.py:89）深合并 dict：用户配置只覆盖部分键时，其余键保留默认值
- 报告模板 `src/report/templates/report_v2.jinja2`：头部是标题 + 生成时间 blockquote + `---`，横幅插在 `{% endif %}`（no_llm）之后、`---` 之前（第 7-9 行之间）
- `tests/api/test_app.py` 已有 `make_core()`（FakePipeline 返回 financial score=8.0 → final_score=8.0）；`/api/v1/analyze` 端点目前**无测试覆盖**，本次补上
- 测试命令必须用 `.venv/Scripts/python -m pytest <文件> -q`；ruff 用 `~/.vscode/extensions/charliermarsh.ruff-*/bundled/libs/bin/ruff.exe check <文件>`
- 中文提交信息格式：`feat(信号系统): ...` / `fix(...)` / `test(...)` / `docs(...)`

---

### Task 1: 配置默认值 — api 与 signal 节

**Files:**
- Modify: `src/utils/config.py:7-27`（DEFAULT_CONFIG）
- Test: `tests/utils/test_config.py`（TestConfig 类内追加测试）

- [ ] **Step 1: 写失败测试**

在 `tests/utils/test_config.py` 的 `TestConfig` 类中追加：

```python
    def test_default_config_has_api_and_signal_sections(self):
        cfg = Config(config_dir=Path("/nonexistent"))
        assert cfg.data["api"] == {"host": "127.0.0.1", "port": 25618}
        assert cfg.data["signal"]["thresholds"] == {"attack": 7, "watch": 4}
        assert set(cfg.data["signal"]["actions"]) == {"attack", "watch", "defend"}
        assert cfg.data["signal"]["actions"]["attack"] == {"action": "可考虑建仓/加仓", "position": "60%-80%"}
        assert cfg.data["signal"]["actions"]["watch"] == {"action": "持有观察，等待明确方向", "position": "30%-50%"}
        assert cfg.data["signal"]["actions"]["defend"] == {"action": "减仓或回避", "position": "0%-20%"}
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/utils/test_config.py::TestConfig::test_default_config_has_api_and_signal_sections -q`
Expected: FAIL（KeyError: 'api'）

- [ ] **Step 3: 实现**

在 `src/utils/config.py` 的 `DEFAULT_CONFIG`（第 7-27 行）中追加两个节：

```python
DEFAULT_CONFIG = {
    "llm": {
        "provider": "openai",
        "model": "gpt-4o",
        "enabled": True,
        "api_key": "",
        "base_url": "",
        "temperature": 0.3,
        "max_tokens": 2000,
        "retry_times": 2,
        "timeout_seconds": 60,
    },
    "data": {
        "cache_ttl": {
            "daily": 86400,
            "quarterly": 604800,
            "news": 21600,
        },
        "disclaimer_accepted": False,
    },
    "api": {
        "host": "127.0.0.1",
        "port": 25618,
    },
    "signal": {
        "thresholds": {
            "attack": 7,
            "watch": 4,
        },
        "actions": {
            "attack": {"action": "可考虑建仓/加仓", "position": "60%-80%"},
            "watch": {"action": "持有观察，等待明确方向", "position": "30%-50%"},
            "defend": {"action": "减仓或回避", "position": "0%-20%"},
        },
    },
}
```

- [ ] **Step 4: 运行确认通过 + 全量 config 测试**

Run: `.venv/Scripts/python -m pytest tests/utils/test_config.py -q`
Expected: PASS（8 个测试全绿）

- [ ] **Step 5: 提交**

```bash
git add src/utils/config.py tests/utils/test_config.py
git commit -m "feat(配置): 新增 api 与 signal 配置节默认值"
```

---

### Task 2: 信号模块 src/report/signal.py

**Files:**
- Create: `src/report/signal.py`
- Test: `tests/report/test_signal.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/report/test_signal.py`：

```python
"""信号推导与配置加载测试"""
import pytest

from report.signal import (
    SIGNAL_LABELS,
    derive_signal,
    load_signal_config,
)
from utils.config import Config


class TestDeriveSignal:
    def test_threshold_boundaries(self):
        # 默认阈值：attack=7, watch=4
        thresholds = {"attack": 7.0, "watch": 4.0}
        assert derive_signal(7.0, thresholds) == "attack"
        assert derive_signal(6.9, thresholds) == "watch"
        assert derive_signal(4.0, thresholds) == "watch"
        assert derive_signal(3.9, thresholds) == "defend"
        assert derive_signal(0.0, thresholds) == "defend"
        assert derive_signal(10.0, thresholds) == "attack"

    def test_custom_thresholds(self):
        thresholds = {"attack": 8.0, "watch": 5.0}
        assert derive_signal(7.5, thresholds) == "watch"
        assert derive_signal(8.0, thresholds) == "attack"
        assert derive_signal(4.9, thresholds) == "defend"


class TestLoadSignalConfig:
    def test_default_config_loads(self, tmp_path):
        cfg = Config(config_dir=tmp_path)
        sc = load_signal_config(cfg)
        assert sc.thresholds == {"attack": 7.0, "watch": 4.0}
        assert set(sc.actions) == {"attack", "watch", "defend"}
        assert sc.actions["attack"].action == "可考虑建仓/加仓"
        assert sc.actions["attack"].position == "60%-80%"

    def test_custom_thresholds_and_actions(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "signal:\n"
            "  thresholds: {attack: 8, watch: 5}\n"
            "  actions:\n"
            "    attack: {action: 强势加仓, position: 70%-90%}\n",
            encoding="utf-8",
        )
        sc = load_signal_config(Config(config_dir=tmp_path))
        assert sc.thresholds == {"attack": 8.0, "watch": 5.0}
        # 部分覆盖：attack 用自定义，watch/defend 保留默认（Config 深合并兜底）
        assert sc.actions["attack"].action == "强势加仓"
        assert sc.actions["watch"].action == "持有观察，等待明确方向"
        assert sc.actions["defend"].position == "0%-20%"

    def test_partial_action_fields_fallback_to_default(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "signal:\n"
            "  actions:\n"
            "    attack: {action: 强势加仓}\n",  # 缺 position → 默认兜底
            encoding="utf-8",
        )
        sc = load_signal_config(Config(config_dir=tmp_path))
        assert sc.actions["attack"].action == "强势加仓"
        assert sc.actions["attack"].position == "60%-80%"

    @pytest.mark.parametrize("yaml_text", [
        "signal: null\n",
        "signal: {thresholds: null}\n",
        "signal: {actions: null}\n",
        "signal: {thresholds: {attack: 7}}\n",          # 缺 watch
        "signal: {thresholds: {attack: 4, watch: 7}}\n",  # watch >= attack
        "signal: {thresholds: {attack: 11, watch: 4}}\n",  # attack > 10
        "signal: {thresholds: {attack: 7, watch: 0}}\n",   # watch <= 0
        "signal: {actions: {attack: {action: x}}}\n",      # 缺 watch/defend
    ])
    def test_invalid_config_raises(self, tmp_path, yaml_text):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_text, encoding="utf-8")
        with pytest.raises(ValueError):
            load_signal_config(Config(config_dir=tmp_path))

    def test_labels_fixed(self):
        assert SIGNAL_LABELS == {"attack": "进攻", "watch": "观望", "defend": "防御"}
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/report/test_signal.py -q`
Expected: FAIL（ModuleNotFoundError: report.signal）

- [ ] **Step 3: 实现**

创建 `src/report/signal.py`：

```python
"""信号推导与动作映射 — 回测契约（信号枚举固定，阈值/动作配置化）"""
from dataclasses import dataclass, field
from typing import Literal

from utils.config import Config

Signal = Literal["attack", "watch", "defend"]

SIGNAL_ORDER: tuple[Signal, ...] = ("attack", "watch", "defend")

SIGNAL_LABELS: dict[Signal, str] = {
    "attack": "进攻",
    "watch": "观望",
    "defend": "防御",
}


@dataclass(frozen=True)
class SignalAction:
    action: str
    position: str


DEFAULT_SIGNAL_ACTIONS: dict[Signal, SignalAction] = {
    "attack": SignalAction(action="可考虑建仓/加仓", position="60%-80%"),
    "watch": SignalAction(action="持有观察，等待明确方向", position="30%-50%"),
    "defend": SignalAction(action="减仓或回避", position="0%-20%"),
}


@dataclass(frozen=True)
class SignalConfig:
    thresholds: dict[Signal, float] = field(
        default_factory=lambda: {"attack": 7.0, "watch": 4.0}
    )
    actions: dict[Signal, SignalAction] = field(
        default_factory=lambda: dict(DEFAULT_SIGNAL_ACTIONS)
    )


def derive_signal(final_score: float, thresholds: dict[Signal, float]) -> Signal:
    """由综合得分推导操作信号（确定性规则，可回测）"""
    if final_score >= thresholds["attack"]:
        return "attack"
    if final_score >= thresholds["watch"]:
        return "watch"
    return "defend"


def load_signal_config(config: Config) -> SignalConfig:
    """从配置加载信号阈值与动作映射，校验失败抛 ValueError（回测契约完整性）"""
    raw = config.get("signal")
    if not isinstance(raw, dict):
        raise ValueError("signal 配置缺失或不是字典")
    thresholds_raw = raw.get("thresholds")
    if not isinstance(thresholds_raw, dict):
        raise ValueError("signal.thresholds 缺失或不是字典")
    actions_raw = raw.get("actions")
    if not isinstance(actions_raw, dict):
        raise ValueError("signal.actions 缺失或不是字典")

    thresholds: dict[Signal, float] = {}
    for key in SIGNAL_ORDER:
        val = thresholds_raw.get(key)
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise ValueError(f"signal.thresholds.{key} 缺失或不是数字")
        thresholds[key] = float(val)
    if not (0 < thresholds["watch"] < thresholds["attack"] <= 10):
        raise ValueError("signal.thresholds 非法：需满足 0 < watch < attack <= 10")

    actions: dict[Signal, SignalAction] = {}
    for key in SIGNAL_ORDER:
        item = actions_raw.get(key)
        if not isinstance(item, dict):
            raise ValueError(f"signal.actions.{key} 缺失或不是字典")
        default = DEFAULT_SIGNAL_ACTIONS[key]
        actions[key] = SignalAction(
            action=str(item.get("action") or default.action),
            position=str(item.get("position") or default.position),
        )
    return SignalConfig(thresholds=thresholds, actions=actions)
```

- [ ] **Step 4: 运行确认通过 + 静态检查**

Run: `.venv/Scripts/python -m pytest tests/report/test_signal.py -q`
Expected: PASS（10 个测试全绿）

Run: `pyright src/report/signal.py` 和 `~/.vscode/extensions/charliermarsh.ruff-*/bundled/libs/bin/ruff.exe check src/report/signal.py tests/report/test_signal.py`
Expected: 0 errors

- [ ] **Step 5: 提交**

```bash
git add src/report/signal.py tests/report/test_signal.py
git commit -m "feat(信号系统): 信号推导纯函数与配置化动作映射"
```

---

### Task 3: api 命令端口配置化

**Files:**
- Modify: `src/stock_robot/cli.py:423-437`（api 命令）
- Test: `tests/test_cli.py`（文件末尾追加函数）

- [ ] **Step 1: 写失败测试**

在 `tests/test_cli.py` 末尾追加：

```python
def test_api_bind_resolution_uses_config_defaults(tmp_path):
    """未传 host/port 时读配置 api.host/api.port（默认 127.0.0.1:25618）"""
    from stock_robot.cli import _resolve_api_bind
    from utils.config import Config

    cfg = Config(config_dir=tmp_path)
    assert _resolve_api_bind(None, None, cfg) == ("127.0.0.1", 25618)
    assert _resolve_api_bind("0.0.0.0", None, cfg) == ("0.0.0.0", 25618)
    assert _resolve_api_bind(None, 9000, cfg) == ("127.0.0.1", 9000)


def test_api_bind_resolution_uses_configured_values(tmp_path):
    """配置自定义 api.port 后未传参时生效"""
    from stock_robot.cli import _resolve_api_bind
    from utils.config import Config

    cfg = Config(config_dir=tmp_path)
    cfg.set("api.port", 9000)
    cfg.set("api.host", "0.0.0.0")
    assert _resolve_api_bind(None, None, cfg) == ("0.0.0.0", 9000)
    assert _resolve_api_bind(None, 8000, cfg) == ("0.0.0.0", 8000)  # CLI 优先
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -q`
Expected: FAIL（ImportError: cannot import name '_resolve_api_bind'）

**3a. `src/stock_robot/cli.py` 顶部 import 区**（第 16 行 `logger = logging.getLogger(__name__)` 后）追加：

```python
from utils.config import Config
```

（Config 为轻量模块，顶部导入与 cli.py 的延迟导入风格不冲突；`_resolve_api_bind` 的类型注解需要它在模块加载时可见。）

**3b. `src/stock_robot/cli.py`** — 将 `api` 命令（第 423-437 行）替换为：

```python
def _resolve_api_bind(host: str | None, port: int | None, config: Config) -> tuple[str, int]:
    """解析 API 监听地址：CLI 显式参数 > 配置文件 > 默认值"""
    resolved_host = host or config.get("api.host", "127.0.0.1")
    resolved_port = port if port is not None else config.get("api.port", 25618)
    return resolved_host, resolved_port


@main.command()
@click.option("--host", default=None, help="监听地址（默认读配置 api.host，缺省 127.0.0.1）")
@click.option("--port", default=None, type=int, help="监听端口（默认读配置 api.port，缺省 25618）")
def api(host, port):
    """启动 Web API 服务（含 Web UI）"""
    from api.app import create_app
    from api.bootstrap import build_agent_core

    config = Config()
    bind_host, bind_port = _resolve_api_bind(host, port, config)
    core = build_agent_core(config)
    app = create_app(core=core)
    logger.info("Stock Robot API 启动于 http://%s:%d", bind_host, bind_port)
    import uvicorn
    uvicorn.run(app, host=bind_host, port=bind_port)
```

（api 命令函数内的 `from utils.config import Config` 局部导入删除，改用顶部导入。）

- [ ] **Step 4: 运行确认通过 + 静态检查**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -q`
Expected: PASS

Run: `pyright src/stock_robot/cli.py` 和 `~/.vscode/extensions/charliermarsh.ruff-*/bundled/libs/bin/ruff.exe check src/stock_robot/cli.py tests/test_cli.py`
Expected: 0 errors（若顶部未导入 Config 会报 F821/未定义，按 Step 3 注意修正）

- [ ] **Step 5: 提交**

```bash
git add src/stock_robot/cli.py tests/test_cli.py
git commit -m "feat(API): api 命令默认端口 25618，支持配置文件自定义"
```

---

### Task 4: CLI 报告信号横幅

**Files:**
- Modify: `src/report/scoring.py:82-109`（build_report 加 signal_cfg 参数）
- Modify: `src/report/builder.py:100-127`（build() 加 signal 参数）
- Modify: `src/report/templates/report_v2.jinja2:7-9`（横幅）
- Modify: `src/stock_robot/cli.py:181-183`（report 命令传入 signal_cfg）
- Test: `tests/report/test_signal.py`（追加集成测试）

- [ ] **Step 1: 写失败测试**

在 `tests/report/test_signal.py` 末尾追加：

```python
class TestBuildReportSignal:
    def _build(self, tmp_path, final_score):
        """构造单维度结果，控制 final_score"""
        from data.schemas import AnalysisContext, AnalysisResult
        from report.scoring import build_report

        results = [AnalysisResult(
            dimension="financial", status="ok", summary="财务健康",
            score=final_score, metrics={"roe": 0.12},
        )]
        ctx = AnalysisContext(symbol="000001", name="平安银行")
        return build_report("000001", "平安银行", results, {"bulk": "解读"},
                            ctx, no_llm=True, signal_cfg=load_signal_config(Config(config_dir=tmp_path)))

    def test_report_contains_attack_banner(self, tmp_path):
        report = self._build(tmp_path, 8.0)  # final_score=8.0 → 进攻
        assert "信号：进攻" in report
        assert "可考虑建仓/加仓" in report
        assert "60%-80%" in report

    def test_report_contains_defend_banner(self, tmp_path):
        report = self._build(tmp_path, 2.0)  # final_score=2.0 → 防御
        assert "信号：防御" in report
        assert "减仓或回避" in report

    def test_report_without_signal_cfg_has_no_banner(self, tmp_path):
        """signal_cfg=None 时不渲染横幅（兼容现有调用）"""
        from data.schemas import AnalysisContext, AnalysisResult
        from report.scoring import build_report

        results = [AnalysisResult(dimension="financial", status="ok",
                                  summary="财务健康", score=8.0)]
        ctx = AnalysisContext(symbol="000001", name="平安银行")
        report = build_report("000001", "平安银行", results, {"bulk": "解读"},
                              ctx, no_llm=True)
        assert "信号：" not in report
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/report/test_signal.py::TestBuildReportSignal -q`
Expected: FAIL（TypeError: build_report() got an unexpected keyword argument 'signal_cfg'）

- [ ] **Step 3: 实现**

**3a. `src/report/scoring.py`** — `build_report` 签名与内部（第 82-109 行）改为：

```python
def build_report(symbol: str, name: str, results: list[AnalysisResult],
                 commentary: dict[str, str], ctx: AnalysisContext,
                 no_llm: bool = False, market_env: dict | None = None,
                 signal_cfg: SignalConfig | None = None) -> str:
    """组装完整报告文本（ReportBuilder 渲染）"""
    from report.builder import ReportBuilder

    summary = compute_score_summary(results)
    price_info = compute_price_info(ctx)
    industry = ctx.industry_data.industry if ctx.industry_data else "未知"

    signal = None
    if signal_cfg is not None:
        level = derive_signal(summary.final_score, signal_cfg.thresholds)
        action = signal_cfg.actions[level]
        signal = {"level": level, "label": SIGNAL_LABELS[level],
                  "action": action.action, "position": action.position}

    builder = ReportBuilder()
    return builder.build(
        symbol=symbol,
        name=name,
        results=results,
        commentary=commentary,
        no_llm=no_llm,
        industry=industry,
        year_high=f"{price_info['year_high']:.2f}" if price_info['year_high'] else "暂无",
        year_low=f"{price_info['year_low']:.2f}" if price_info['year_low'] else "暂无",
        price_position=price_info['price_position'],
        score_rows=summary.score_rows,
        base_score=summary.base_score,
        risk_deduction=summary.risk_deduction,
        final_score=summary.final_score,
        risk_flags=summary.risk_flags,
        market_env=market_env,
        signal=signal,
    )
```

同时在 `src/report/scoring.py` 顶部 import 区追加：

```python
from report.signal import SIGNAL_LABELS, SignalConfig, derive_signal
```

**3b. `src/report/builder.py`** — `build()` 签名加参数（第 106 行 `market_env: dict | None = None` 后追加），并在 `template.render(...)`（第 127 行 `risk_flags=risk_flags or [],` 后）追加 `signal=signal,`：

```python
    def build(self, symbol: str, name: str, results: list[AnalysisResult],
              commentary: dict[str, str], no_llm: bool = False,
              industry: str = "未知", year_high: str = "暂无",
              year_low: str = "暂无", price_position: str = "暂无",
              score_rows: list[dict] | None = None,
              base_score: float = 0, risk_deduction: float = 0,
              final_score: float = 0, risk_flags: list[str] | None = None,
              market_env: dict | None = None,
              signal: dict | None = None) -> str:
        ...
        return template.render(
            ...
            risk_flags=risk_flags or [],
            signal=signal,
        )
```

**3c. `src/report/templates/report_v2.jinja2`** — 第 7-9 行之间插入横幅：

```jinja2
{% if no_llm %}
> **[纯量化模式，无 AI 解读]**
{% endif %}

{% if signal %}
> **操作信号：{{ signal.label }}** — {{ signal.action }}（建议仓位 {{ signal.position }}）
{% endif %}

---
```

**3d. `src/stock_robot/cli.py`** — report 命令调用处（第 181-183 行）改为：

```python
    from report.scoring import build_report
    from report.signal import load_signal_config
    report = build_report(symbol, name, results, commentary, ctx,
                          no_llm=no_llm, market_env=market_env,
                          signal_cfg=load_signal_config(config))
```

（report 命令函数内 `config` 变量已存在，见第 131 行 `config.get("llm.enabled", True)`。）

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `.venv/Scripts/python -m pytest tests/report/test_signal.py tests/report/test_builder.py -q`
Expected: PASS（新测试 + 既有 builder 测试不破）

Run: `pyright src/report/scoring.py src/report/builder.py src/stock_robot/cli.py` 和 `~/.vscode/extensions/charliermarsh.ruff-*/bundled/libs/bin/ruff.exe check src/report/scoring.py src/report/builder.py src/stock_robot/cli.py tests/report/test_signal.py`
Expected: 0 errors

- [ ] **Step 5: 提交**

```bash
git add src/report/scoring.py src/report/builder.py src/report/templates/report_v2.jinja2 src/stock_robot/cli.py tests/report/test_signal.py
git commit -m "feat(报告): CLI 报告头部新增操作信号横幅"
```

---

### Task 5: API analyze 响应信号字段

**Files:**
- Modify: `src/api/app.py:206-257`（analyze 端点）
- Test: `tests/api/test_app.py`（追加 analyze 测试）

- [ ] **Step 1: 写失败测试**

在 `tests/api/test_app.py` 追加（放在 `make_core()` 定义之后）：

```python
class TestAnalyzeSignal:
    def test_analyze_response_contains_signal(self):
        """/api/v1/analyze 响应含结构化 signal 字段（FakePipeline 得分为 8 → 进攻）"""
        from fastapi.testclient import TestClient

        from api.app import create_app

        app = create_app(core=make_core(), sessions=None)
        client = TestClient(app)
        resp = client.post("/api/v1/analyze", json={"symbol": "000001"})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["signal"] == {
            "level": "attack",
            "label": "进攻",
            "action": "可考虑建仓/加仓",
            "position": "60%-80%",
        }
```

注意：`create_app(core=..., sessions=None)` 时 `_build_agent` 不会被调用（analyze 端点只走 `core.pipeline.run` + `load_signal_config`），无需会话管理。`FakePipeline.run` 返回的 financial score=8.0，`compute_score_summary` 得 final_score=8.0 → attack。

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/api/test_app.py::TestAnalyzeSignal -q`
Expected: FAIL（KeyError: 'signal'）

- [ ] **Step 3: 实现**

在 `src/api/app.py` 的 analyze 端点（第 225 行处）改造：

在 payload 构造处（`"commentary": ...` 行之后、`"generated_at"` 行之前）追加：

```python
                "signal": _build_signal_payload(summary.final_score),
```

并在模块级（`create_app` 之前、`_structured_tool_results` 附近）新增辅助函数：

```python
def _build_signal_payload(final_score: float) -> dict:
    """从配置加载信号映射并构造 API 信号字段（配置损坏时抛 ValueError 由边界兜底）"""
    from report.signal import SIGNAL_LABELS, derive_signal, load_signal_config
    from utils.config import Config

    cfg = load_signal_config(Config())
    level = derive_signal(final_score, cfg.thresholds)
    action = cfg.actions[level]
    return {"level": level, "label": SIGNAL_LABELS[level],
            "action": action.action, "position": action.position}
```

（`_build_signal_payload` 的调用点位于 analyze 端点的 `try` 块内，配置损坏时抛出的 `ValueError` 走既有 500 边界兜底；函数内延迟导入与 app.py 现有模式一致。）

- [ ] **Step 4: 运行确认通过 + 静态检查**

Run: `.venv/Scripts/python -m pytest tests/api/test_app.py -q`
Expected: PASS（既有测试 + 新测试全绿）

Run: `pyright src/api/app.py` 和 `~/.vscode/extensions/charliermarsh.ruff-*/bundled/libs/bin/ruff.exe check src/api/app.py tests/api/test_app.py`
Expected: 0 errors

- [ ] **Step 5: 提交**

```bash
git add src/api/app.py tests/api/test_app.py
git commit -m "feat(API): analyze 响应新增结构化 signal 字段"
```

---

### Task 6: README 更新

**Files:**
- Modify: `README.md:235-243`（Web UI 启动说明与端口配置）

- [ ] **Step 1: 更新启动说明**

将 README.md 第 239-243 行替换为：

```markdown
一键启动（自动注入 Agent 核心）：

```bash
stock-robot api
```

默认监听 `127.0.0.1:25618`（端口可在配置文件 `~/.stock_robot/config.yaml` 的 `api.port` 中自定义，CLI 参数 `--host`/`--port` 优先于配置）：

```bash
stock-robot api --host 127.0.0.1 --port 8000
```

浏览器打开 http://127.0.0.1:25618 使用 Web 聊天界面。页面功能：
```

- [ ] **Step 2: 更新 analyze 端点说明**

将 README.md 第 254 行替换为：

```markdown
- `POST /api/v1/analyze` — 个股分析（body: `{"symbol": "600519"}`），返回完整报告 JSON（含 `signal` 操作信号字段：`level` 为 `attack`/`watch`/`defend`，`label`/`action`/`position` 为中文展示与动作建议；阈值与动作文案可在配置 `signal` 节自定义）
```

- [ ] **Step 3: 验证渲染**

Run: 打开 README.md 检查代码块嵌套与列表格式（bash 代码块内嵌 ``` 需缩进 4 空格，避免破坏外层代码块）
Expected: Markdown 渲染正常

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs(README): api 默认端口 25618 与信号字段说明"
```

---

### Task 7: 全量回归验证

- [ ] **Step 1: 运行全量检查**

Run: `~/.vscode/extensions/charliermarsh.ruff-*/bundled/libs/bin/ruff.exe check .`
Expected: 0 errors

Run: `pyright`
Expected: 0 errors

Run: `.venv/Scripts/python -m pytest -q`
Expected: 全绿（无失败/错误）

- [ ] **Step 2: 手动冒烟（可选，需联网）**

Run: `.venv/Scripts/python -m stock_robot.cli analyze 000001 --no-llm` 或 `stock-robot analyze 000001 --no-llm`
Expected: 报告头部出现 `> **操作信号：...** — ...（建议仓位 ...）` 横幅

- [ ] **Step 3: 提交收尾（如有遗漏文件）**

```bash
git status
git add -A
git commit -m "chore(项目): 信号系统回归收尾"  # 仅当有未提交改动时执行
```
