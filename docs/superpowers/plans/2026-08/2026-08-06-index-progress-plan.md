# 指数分析进度条 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `stock-robot index` 命令添加 Rich 进度条，覆盖数据采集和分析两个阶段

**Architecture:** 沿用现有个股管道的 `on_progress` 回调模式 — `IndexDataCollector.collect()` 和 `IndexPipeline.run()` 各新增 `on_progress` 参数，CLI 层创建 `Progress` 上下文并定义回调。分析阶段按 `index_style` 过滤不适用模块，使进度条步数反映实际工作量。

**Tech Stack:** Python 3.11+, Rich (Progress/SpinnerColumn/TextColumn/BarColumn)

---

## 文件结构

| 文件 | 职责 | 变更类型 |
|---|---|---|
| `src/index/collector.py` | 指数数据采集，新增进度回调支持 | 修改 |
| `src/index/pipeline.py` | 指数管道编排，新增进度回调 + 按 index_style 过滤分析模块 | 修改 |
| `src/stock_robot/cli.py` | CLI `index` 命令，包裹 Progress 上下文 | 修改 |
| `tests/index/test_collector.py` | 采集器测试，补充 on_progress 回调验证 | 修改 |
| `tests/index/test_pipeline.py` | 管道测试，补充 on_progress 和模块过滤验证 | 修改 |

---

### Task 1: IndexDataCollector.collect() 添加 on_progress 回调

**Files:**
- Modify: `src/index/collector.py`

- [ ] **Step 1: 添加 import 和标签常量**

在文件顶部添加 `ProgressCallback` 导入，在类定义前添加标签映射常量。

```python
"""IndexDataCollector — 按 index_style 编排指数数据采集，只拉取原始数据"""
import logging
from core.registry import Registry
from core.pipeline import ProgressCallback
from data.schemas import AnalysisTarget, IndexAnalysisContext
from data.akshare import AkShareAdapter

logger = logging.getLogger(__name__)

INDEX_COLLECT_LABELS = {
    "index_price": "采集行情数据",
    "index_valuation": "采集估值数据",
    "index_capital_flow": "采集资金流向数据",
    "index_macro": "采集宏观数据",
    "index_sentiment": "采集舆情数据",
}
```

- [ ] **Step 2: 修改 collect() 方法签名和主体**

将 `collect()` 方法改为接受 `on_progress` 参数，在每个数据源 fetch 后汇报进度。核心思路：先构建采集步骤列表（按 index_style 条件填充），再遍历执行。

```python
    def collect(self, target: AnalysisTarget, on_progress: ProgressCallback = None) -> IndexAnalysisContext:
        ctx = IndexAnalysisContext(target=target)

        # 预计算采集步骤列表
        steps: list[tuple[str, str, callable]] = []

        def fetch_price():
            result = self._adapter.fetch(
                target.symbol, data_type="index_price",
                index_style=target.index_style
            )
            if result:
                ctx.price_data = result

        def fetch_valuation():
            result = self._adapter.fetch(
                target.symbol, data_type="index_valuation"
            )
            if result:
                ctx.valuation_data = result[0]

        def fetch_capital_flow():
            result = self._adapter.fetch(
                target.symbol, data_type="index_capital_flow",
                index_style=target.index_style
            )
            if result:
                ctx.capital_flow = result[0]

        def fetch_macro():
            result = self._adapter.fetch(
                target.symbol, data_type="index_macro",
                index_style=target.index_style
            )
            if result:
                ctx.macro = result[0]

        def fetch_sentiment():
            result = self._adapter.fetch(
                target.symbol, data_type="index_sentiment"
            )
            if result and len(result) > 0:
                from data.schemas import RawSentimentData, RawSentimentItem
                news = result[0]
                ctx.raw_sentiment = RawSentimentData(
                    symbol=target.symbol,
                    fetch_date=news.date,
                    items=[RawSentimentItem(
                        title=h, source="market_news",
                        publish_date=news.date
                    ) for h in (news.headlines or [])]
                )

        # 所有类别都采集行情和估值
        steps.append(("index_price", "采集行情数据", fetch_price))
        steps.append(("index_valuation", "采集估值数据", fetch_valuation))

        # 按类别采集资金流向
        if target.index_style in ("broad", "sector"):
            steps.append(("index_capital_flow", "采集资金流向数据", fetch_capital_flow))

        # 按类别采集宏观数据
        if target.index_style in ("broad", "overseas"):
            steps.append(("index_macro", "采集宏观数据", fetch_macro))
        elif target.index_style == "sector":
            from data.schemas import MacroContext
            from datetime import date
            ctx.macro = MacroContext(symbol=target.symbol, fetch_date=date.today())

        # 舆情
        steps.append(("index_sentiment", "采集舆情数据", fetch_sentiment))

        total = len(steps)
        for i, (data_type, label, fetch_fn) in enumerate(steps):
            fetch_fn()
            if on_progress:
                on_progress("collect", i + 1, total, label)

        return ctx
```

- [ ] **Step 3: 提交**

```bash
git add src/index/collector.py
git commit -m "feat(指数): IndexDataCollector 添加 on_progress 进度回调支持"
```

---

### Task 2: IndexPipeline.run() 添加 on_progress + 按 index_style 过滤模块

**Files:**
- Modify: `src/index/pipeline.py`

- [ ] **Step 1: 添加 import 和常量**

```python
"""IndexPipeline — 指数分析管道编排"""
import logging
from dataclasses import dataclass, field
from core.registry import Registry
from core.pipeline import ProgressCallback
from data.schemas import AnalysisTarget, IndexAnalysisContext, AnalysisResult, IndexReport
from index.collector import IndexDataCollector
from index.enricher import IndexValuationEnricher
from index.build_single import IndexReportBuilder
from index.build_compare import IndexCompareReportBuilder, CompareTable

logger = logging.getLogger(__name__)

INDEX_DIMENSION_LABELS = {
    "index_technical": "技术面分析",
    "index_valuation": "估值分析",
    "index_capital_flow": "资金面分析",
    "index_macro": "宏观分析",
    "index_sentiment": "舆情分析",
}

# dimension → 适用的 index_style
INDEX_DIMENSION_STYLES = {
    "index_technical": ("broad", "sector", "overseas"),
    "index_valuation": ("broad", "sector", "overseas"),
    "index_capital_flow": ("broad", "sector"),
    "index_macro": ("broad", "overseas"),
    "index_sentiment": ("broad", "sector", "overseas"),
}
```

- [ ] **Step 2: 修改 run() 方法签名和分析循环**

```python
    def run(self, targets: list[AnalysisTarget],
            on_progress: ProgressCallback = None) -> IndexPipelineResult:
        reports: list[IndexReport] = []
        contexts: list[IndexAnalysisContext] = []
        errors: list[str] = []

        for target in targets:
            try:
                ctx = self._collector.collect(target, on_progress=on_progress)

                # 按 index_style 过滤适用的分析模块
                applicable_modules = [
                    m for m in self._analysis_modules
                    if target.index_style in INDEX_DIMENSION_STYLES.get(m.dimension, ())
                ]
                total = len(applicable_modules)

                results: list[AnalysisResult] = []
                for i, module in enumerate(applicable_modules):
                    try:
                        result = module.analyze(ctx)
                        results.append(result)
                    except Exception as e:
                        logger.error(f"分析模块 {module.dimension} 失败: {e}")
                        results.append(AnalysisResult(
                            dimension=module.dimension, status="unavailable",
                            summary=f"分析模块异常: {e}", metrics={}
                        ))
                    if on_progress:
                        on_progress("analyze", i + 1, total,
                                   INDEX_DIMENSION_LABELS.get(module.dimension, module.dimension))

                report = self._report_builder.build(ctx, results)
                reports.append(report)
                contexts.append(ctx)

            except Exception as e:
                logger.error(f"指数 {target.symbol} 分析失败: {e}")
                errors.append(f"{target.symbol}: {e}")

        compare = None
        if len(reports) >= 2:
            compare = self._compare_builder.build(contexts, reports)

        return IndexPipelineResult(reports=reports, compare=compare, errors=errors)
```

- [ ] **Step 3: 提交**

```bash
git add src/index/pipeline.py
git commit -m "feat(指数): IndexPipeline 添加 on_progress 回调并支持按 index_style 过滤分析模块"
```

---

### Task 3: CLI index 命令添加 Progress 上下文

**Files:**
- Modify: `src/stock_robot/cli.py` (around line 411)

- [ ] **Step 1: 在 index() 函数中包裹 Progress 上下文**

将 `pipeline = IndexPipeline()` 和 `result = pipeline.run(targets)` 包裹在 Rich Progress 上下文中。

替换以下代码（`src/stock_robot/cli.py:411-412`）：

```python
    pipeline = IndexPipeline()
    result = pipeline.run(targets)
```

为：

```python
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

    pipeline = IndexPipeline()
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

- [ ] **Step 2: 提交**

```bash
git add src/stock_robot/cli.py
git commit -m "feat(CLI): index 命令添加 Rich 进度条"
```

---

### Task 4: 运行现有测试确认无回归

- [ ] **Step 1: 运行指数相关测试**

```bash
cd "D:/code/stock_robot" && python -m pytest tests/index/ -v
```

预期：所有已有测试通过（`on_progress` 默认为 `None`，不影响现有调用路径）。

- [ ] **Step 2: 运行完整的快速测试套件**

```bash
cd "D:/code/stock_robot" && python -m pytest tests/ -x --timeout=60 -q
```

预期：全部通过。

---

### Task 5: 手动验证进度条效果

- [ ] **Step 1: 单指数分析**

```bash
python -m stock_robot.cli index 000300
```

预期：看到带有 spinner 的进度条，依次显示采集（行情→估值→资金流向→宏观→舆情）和分析（5个模块）进度。

- [ ] **Step 2: 行业指数（sector，跳过宏观）**

```bash
python -m stock_robot.cli index 801080
```

预期：采集 4 步（无宏观），分析 4 步（无宏观分析模块）。

- [ ] **Step 3: 多指数横向对比**

```bash
python -m stock_robot.cli index 000300 000905
```

预期：每个目标独立推进进度条，最后输出对比表格。

- [ ] **Step 4: 无 TTY 输出（重定向到文件）**

```bash
python -m stock_robot.cli index 000300 > /tmp/test_output.txt 2>&1
cat /tmp/test_output.txt
```

预期：文件内容为正常报告输出，无 ANSI 转义序列残留。
