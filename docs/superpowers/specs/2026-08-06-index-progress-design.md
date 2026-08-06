# 指数分析进度条设计

## 目标

为 `stock-robot index` 命令添加进度条，与 `stock-robot analyze` 保持一致的交互体验。

## 现状

- **个股分析** (`analyze` 命令)：已有 Rich 进度条，覆盖采集、分析、LLM 三个阶段，通过 `on_progress` 回调解耦
- **指数分析** (`index` 命令)：无任何进度反馈，`IndexPipeline.run()` 不支持 `on_progress` 回调
- 指数管道流程：`IndexDataCollector.collect()` 同步采集 4~5 类数据 → 5 个分析模块顺序执行 → 报告构建 → 横向对比

## 设计决策

### 逐目标进度（与个股管道一致）

每个指数目标独立推进进度条。采集阶段按实际拉取的数据源数量报步数，分析阶段按适用的模块数量报步数。多目标时进度条依次重置。

### 跳过不适用模块

分析阶段的进度步数根据 `index_style` 动态确定，不适用的模块不执行也不计入步数：

| 类别 | 适用模块 | 步数 |
|---|---|---|
| broad | 技术面、估值、资金面、宏观、舆情 | 5 |
| sector | 技术面、估值、资金面、舆情 | 4 |
| overseas | 技术面、估值、宏观、舆情 | 4 |

### 回调解耦

IndexPipeline 不直接依赖 Rich，通过回调函数让 CLI 层注入渲染逻辑。`on_progress` 默认为 `None`，保持向后兼容。

## 详细设计

### 1. 进度回调类型

复用 `src/core/pipeline.py` 中已有的 `ProgressCallback` 类型：

```python
ProgressCallback = Callable[[str, int, int, str], None] | None
# stage: "collect" | "analyze"
# current: 当前步骤序号（从 1 开始）
# total: 总步骤数
# label: 当前步骤描述文字
```

### 2. IndexDataCollector 修改

`collect()` 新增 `on_progress: ProgressCallback = None` 参数。

采集标签常量：

```python
INDEX_COLLECT_LABELS = {
    "index_price": "采集行情数据",
    "index_valuation": "采集估值数据",
    "index_capital_flow": "采集资金流向数据",
    "index_macro": "采集宏观数据",
    "index_sentiment": "采集舆情数据",
}
```

采集顺序和条件（与现有逻辑一致）：

```
所有类别: 行情(1) → 估值(2)
broad/sector: +资金流向
broad/overseas: +宏观
所有类别: +舆情(末位)
```

实现方式：在开始采集前预计算总步数（统计有多少个数据源会被实际调用），每完成一个 fetch 后 `completed += 1` 并回调。

### 3. IndexPipeline 修改

`run()` 新增 `on_progress: ProgressCallback = None` 参数。

- 透传 `on_progress` 给 `_collector.collect(target, on_progress=on_progress)`
- `get_snapshot()` 调用 `collect()` 时不传回调，不受影响

分析阶段标签常量：

```python
INDEX_DIMENSION_LABELS = {
    "index_technical": "技术面分析",
    "index_valuation": "估值分析",
    "index_capital_flow": "资金面分析",
    "index_macro": "宏观分析",
    "index_sentiment": "舆情分析",
}
```

分析阶段新增 `index_style` → 适用维度列表的映射，用于过滤模块和计算步数：

```python
INDEX_STYLE_DIMENSIONS = {
    "broad": ["index_technical", "index_valuation", "index_capital_flow", "index_macro", "index_sentiment"],
    "sector": ["index_technical", "index_valuation", "index_capital_flow", "index_sentiment"],
    "overseas": ["index_technical", "index_valuation", "index_macro", "index_sentiment"],
}
```

分析循环改为只执行适用的模块，并在每个模块完成后回调。

### 4. CLI 层实现

`index` 命令中创建 `Progress` 上下文，与 `analyze` 命令模式一致：

```python
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

with Progress(
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TextColumn("{task.completed}/{task.total}"),
    console=console,
    transient=True,
) as progress:
    task_id = progress.add_task("正在分析指数...", total=None)

    def on_progress(stage, current, total, label):
        progress.update(task_id, completed=current, total=total,
                       description=f"[{stage}] {label}")

    result = pipeline.run(targets, on_progress=on_progress)
    progress.update(task_id, visible=False)
```

与 `analyze` 的区别：无需"正在查询股票名称..."的初始 spinner 步骤。

### 5. 与 verbose 模式的交互

- 进度条始终显示（非 TTY 时 Rich 自动降级）
- `--verbose` 时进度条在上方，日志在下方，Rich 自动处理布局

## 影响范围

### 修改文件

| 文件 | 改动 |
|---|---|
| `src/index/collector.py` | `collect()` 新增 `on_progress` 参数，采集过程中回调 |
| `src/index/pipeline.py` | `run()` 新增 `on_progress` 参数；分析阶段按 `index_style` 过滤模块并回调 |
| `src/stock_robot/cli.py` | `index` 命令创建 `Progress` 上下文，定义回调传入 pipeline |

### 不修改

- 分析模块、数据源适配器、报告构建器 — 不感知进度
- `get_snapshot()` — 轻量接口不涉及进度展示
- 个 stock 分析管道 — 已有独立进度条实现
- 现有测试 — 进度条不影响功能断言
