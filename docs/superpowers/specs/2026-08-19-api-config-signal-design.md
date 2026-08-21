# API 端口配置化与信号系统设计

日期：2026-08-19

## 背景与目标

两个需求：

1. **API 端口配置化**：`stock-robot api` 命令默认监听 `127.0.0.1:25618`，端口可在配置文件中自定义。
2. **信号系统**：分析报告给出明确的操作信号（进攻/观望/防御），并保留信号与投资动作的结构化映射，为未来回测留出空间。

关键决策（与用户确认）：

- 复用现有 `api` 命令（FastAPI + Web UI），不新建服务；默认端口 25618，配置文件可覆盖，CLI 显式参数优先。
- 信号由纯规则推导（final_score 阈值映射），确定性、可回测；不做 LLM 判断。
- 回测空间只做到「结构化映射」：代码定义信号枚举与推导规则，映射数据可被回测代码直接消费；不做信号持久化。
- 信号枚举固定三个（attack/watch/defend），**阈值与动作映射放配置文件**——动作是策略参数，可能频繁调整，改配置即可，不动代码。
- 展示范围：CLI 生成的 Markdown 报告 + API `/analyze` 响应；Web UI 暂不渲染。

## 现状

- `cli.py` 已存在 `api` 命令（`--host` 默认 `127.0.0.1`，`--port` 默认 `8000`），用 uvicorn 启动 FastAPI 应用（含 Web UI 静态挂载）。
- `utils/config.py` 的 `Config` 支持 YAML 深合并（默认值 + 用户配置覆盖），`get("a.b")` 点分路径取值。
- `report/scoring.py` 的 `compute_score_summary` 产出 `final_score`（0-10 加权分减风险扣分）。
- `build_report`（scoring.py）组装 CLI 报告文本；`/api/v1/analyze`（api/app.py）返回 JSON，含 `score`、`dimensions`、`commentary` 等字段。

## 设计

### 一、API 端口配置化

`DEFAULT_CONFIG` 新增 `api` 节：

```yaml
api:
  host: "127.0.0.1"
  port: 25618
```

`cli.py` `api` 命令改造：

- `--host`/`--port` 参数默认值改为 `None`，help 注明「默认读配置 `api.host`/`api.port`」。
- 参数解析：显式传参 > 配置文件 > 默认值（`127.0.0.1:25618`，即 DEFAULT_CONFIG 兜底）。

`config set` 命令天然支持点分路径，无需改动即可写 `api.port`。

### 二、信号系统

#### 配置

`DEFAULT_CONFIG` 新增 `signal` 节：

```yaml
signal:
  thresholds:
    attack: 7   # final_score >= 7 → 进攻
    watch: 4    # final_score >= 4 → 观望；否则 → 防御
  actions:
    attack: {action: "可考虑建仓/加仓", position: "60%-80%"}
    watch:  {action: "持有观察，等待明确方向", position: "30%-50%"}
    defend: {action: "减仓或回避", position: "0%-20%"}
```

#### 新模块 `src/report/signal.py`

- `Signal = Literal["attack", "watch", "defend"]`——信号枚举固定，不可配置。
- `SIGNAL_LABELS: dict[Signal, str]`——`{attack: "进攻", watch: "观望", defend: "防御"}`，固定代码常量。
- `@dataclass(frozen=True) SignalAction`——`action: str`、`position: str` 两个字段。
- `DEFAULT_SIGNAL_ACTIONS: dict[Signal, SignalAction]`——代码兜底的动作表，内容与配置默认值一致。
- `derive_signal(final_score: float, thresholds: dict[str, float]) -> Signal`——纯函数：
  - `final_score >= attack 阈值` → `attack`
  - `final_score >= watch 阈值` → `watch`
  - 否则 → `defend`
- `load_signal_config(config: Config) -> SignalConfig`——从 `Config` 读取并校验：
  - 校验阈值键齐全（thresholds 须含 attack/watch 两键；defend 无独立阈值，得分低于 watch 即防御），缺失即抛 `ValueError`——回测契约完整性，宁可显式失败。
  - 校验 actions 三个信号键齐全（attack/watch/defend），缺失即抛 `ValueError`。
  - 校验阈值合法：`0 < watch < attack <= 10`，非法抛 `ValueError`。
  - 动作字段缺失时用默认值兜底。

返回值 `SignalConfig`（dataclass）：`thresholds: dict[Signal, float]`、`actions: dict[Signal, SignalAction]`。

#### 集成

1. **CLI 报告**：`build_report`（scoring.py）中计算 `signal = derive_signal(summary.final_score, cfg.thresholds)`，将信号名、动作建议、仓位区间传给 `ReportBuilder`，在报告头部新增信号横幅（如「信号：进攻 —— 可考虑建仓/加仓（仓位 60%-80%）」）。`build_report` 新增参数 `signal_cfg: SignalConfig`（由 pipeline 调用处从 `Config` 加载后传入）。
2. **API**：`/api/v1/analyze` 响应新增结构化字段：

   ```json
   "signal": {
     "level": "attack",
     "label": "进攻",
     "action": "可考虑建仓/加仓",
     "position": "60%-80%"
   }
   ```

   `level` 为枚举值（回测可消费），`label`/`action`/`position` 为展示与动作字段。

#### 错误处理

- 配置校验失败（键缺失/阈值非法）：在 `load_signal_config` 抛出带具体原因的 `ValueError`；调用方（CLI/API）按现有兜底模式处理（API 走 500 边界兜底，CLI 走异常日志）。
- 无 LLM 模式（`no_llm`）下信号照常推导——信号不依赖 LLM。

### 三、测试

- `tests/report/test_signal.py`（新增）：
  - 阈值边界：`final_score == 7` → attack；`== 6.9` → watch；`== 4` → watch；`== 3.9` → defend。
  - 自定义阈值生效（如 attack=8、watch=5）。
  - 配置合并：未配 actions 时用默认兜底；配了部分键时报错。
  - 键缺失（缺 defend）抛 `ValueError`；阈值非法（watch >= attack、watch < 0、attack > 10）抛 `ValueError`。
- `tests/utils/test_config.py`：`api` 与 `signal` 节默认值存在且正确。
- API 测试：`/api/v1/analyze` 响应含 `signal` 字段且字段完整。

## 不做的事（YAGNI）

- 风险旗标降档修正（如旗标数量影响信号档位）——需要时再加。
- 信号持久化（历史信号落库）——规则确定性，回测时可重算。
- Web UI 渲染信号——后端字段就绪后前端随时可加。
- CLI `--port` 与配置冲突告警——显式参数优先是清晰语义，无需告警。
- 阈值热加载/运行时改配置——配置文件改动需重启生效，保持简单。

## 影响面

- 新增：`src/report/signal.py`、`tests/report/test_signal.py`。
- 修改：`src/utils/config.py`（DEFAULT_CONFIG）、`src/stock_robot/cli.py`（api 命令参数解析）、`src/report/scoring.py`（build_report 集成）、`src/api/app.py`（analyze payload）、README（使用说明）。
- 不需要动：Web UI 静态文件、数据层、Agent 模块。
