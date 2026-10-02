# CLI 进度指示实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 CLI 耗时操作（数据采集、分析、LLM 解读）时显示 Rich 进度条/动画

**Architecture:** Pipeline 层新增 `on_progress` 回调参数（接收 stage/current/total/label），在数据采集和分析的 `as_completed` 循环以及 LLM 解读的 for 循环中调用。CLI 层创建 Rich `Progress` 实例并注入回调，实现 UI 与业务逻辑解耦。

**Tech Stack:** Rich（已在依赖中），无需新增包

---

### 修改文件一览

| 文件 | 改动 |
|---|---|
| `src/core/pipeline.py` | 新增 `ProgressCallback` 类型、标签映射、`collect`/`run`/`_generate_commentary` 各加 `on_progress` 参数 |
| `src/stock_robot/cli.py` | `analyze` 命令创建 Rich Progress 并传入回调 |
| `tests/core/test_pipeline.py` | 新增 `on_progress` 回调行为的测试 |

---

### Task 1: 编写 pipeline on_progress 回调测试

**Files:**
- Modify: `tests/core/test_pipeline.py`

- [ ] **Step 1: 添加回调测试代码**

在 `tests/core/test_pipeline.py` 末尾追加以下测试：

```python
class TestPipelineProgress:
    def test_collect_calls_on_progress_for_each_data_type(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        calls = []

        def on_progress(stage, current, total, label):
            calls.append((stage, current, total, label))

        pipeline.collect("000001", "平安银行", "a-shares", on_progress=on_progress)

        assert len(calls) == 5
        stages = {c[0] for c in calls}
        assert stages == {"collect"}
        assert calls[0] == ("collect", 1, 5, "采集财务数据")
        assert calls[-1] == ("collect", 5, 5, "采集舆情数据")

    def test_run_calls_on_progress_for_collect_and_analyze(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        calls = []

        def on_progress(stage, current, total, label):
            calls.append((stage, current, total, label))

        pipeline.run("000001", "平安银行", on_progress=on_progress)

        collect_calls = [c for c in calls if c[0] == "collect"]
        analyze_calls = [c for c in calls if c[0] == "analyze"]
        assert len(collect_calls) == 5
        assert len(analyze_calls) == 5

    def test_on_progress_none_does_not_break(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)

        ctx = pipeline.collect("000001", "平安银行", on_progress=None)
        assert ctx.price_data is not None

        results, _ = pipeline.run("000001", "平安银行", on_progress=None)
        assert len(results) == 5

    def test_run_single_dimension_reports_correct_totals(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        calls = []

        def on_progress(stage, current, total, label):
            calls.append((stage, current, total, label))

        pipeline.run("000001", "平安银行", dimension="financial", on_progress=on_progress)

        collect_calls = [c for c in calls if c[0] == "collect"]
        analyze_calls = [c for c in calls if c[0] == "analyze"]
        assert len(collect_calls) == 1
        assert collect_calls[0][1:3] == (1, 1)
        assert len(analyze_calls) == 1
        assert analyze_calls[0][1:3] == (1, 1)
```

- [ ] **Step 2: 运行测试验证失败**

```bash
python -m pytest tests/core/test_pipeline.py::TestPipelineProgress -v
```

预期：全部 FAIL（`on_progress` 参数尚未添加）

---

### Task 2: 添加 ProgressCallback 类型和标签映射

**Files:**
- Modify: `src/core/pipeline.py`

- [ ] **Step 1: 在 pipeline.py 顶部添加类型和映射**

在 `src/core/pipeline.py` 的 import 块之后、`DATA_TYPES` 之前插入：

```python
from collections.abc import Callable

ProgressCallback = Callable[[str, int, int, str], None] | None

DATA_TYPE_LABELS = {
    "financial": "采集财务数据",
    "price": "采集价格数据",
    "valuation": "采集估值数据",
    "industry": "采集行业数据",
    "news": "采集舆情数据",
}

DIMENSION_LABELS = {
    "financial": "财务分析",
    "technical": "技术面分析",
    "valuation": "估值分析",
    "industry": "行业分析",
    "sentiment": "舆情分析",
}

DIMENSION_LLM_LABELS = {
    "financial": "生成财务解读",
    "technical": "生成技术面解读",
    "valuation": "生成估值解读",
    "industry": "生成行业解读",
    "sentiment": "生成舆情解读",
}
```

- [ ] **Step 2: 运行测试确认无破坏**

```bash
python -m pytest tests/core/test_pipeline.py::TestPipeline -v
```

预期：全部 PASS（新增常量不影响现有测试）

---

### Task 3: 修改 collect() 支持 on_progress

**Files:**
- Modify: `src/core/pipeline.py`

- [ ] **Step 1: 修改 collect 方法签名和实现**

将 `collect` 方法替换为：

```python
    def collect(self, symbol: str, name: str, market: str = "a-shares",
                refresh_cache: bool = False, data_types: list[str] | None = None,
                on_progress: ProgressCallback = None) -> AnalysisContext:
        ctx = AnalysisContext(symbol=symbol, name=name, market=market)
        types_to_fetch = data_types or DATA_TYPES
        total = len(types_to_fetch)
        completed = 0

        def fetch_one(data_type: str):
            if not refresh_cache:
                cached = self._get_cached(symbol, data_type)
                if cached is not None:
                    return data_type, cached

            sources = self._registry.get_data_sources(market, data_type)
            for source in sources:
                try:
                    result = source.fetch(symbol, data_type=data_type)
                    if result:
                        self._set_cache(symbol, data_type, result)
                        return data_type, result
                except Exception as e:
                    logger.warning(f"数据源 {source.__class__.__name__} 获取 {data_type} 失败: {e}")
            return data_type, None

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(fetch_one, dt): dt for dt in types_to_fetch}
            for future in as_completed(futures):
                data_type, result = future.result()
                if result is not None:
                    self._assign_to_context(ctx, data_type, result)
                completed += 1
                if on_progress:
                    on_progress("collect", completed, total,
                               DATA_TYPE_LABELS.get(data_type, data_type))

        return ctx
```

- [ ] **Step 2: 运行测试**

```bash
python -m pytest tests/core/test_pipeline.py::TestPipelineProgress::test_collect_calls_on_progress_for_each_data_type -v
```

预期：PASS

---

### Task 4: 修改 run() 支持 on_progress

**Files:**
- Modify: `src/core/pipeline.py`

- [ ] **Step 1: 修改 run 方法签名和实现**

将 `run` 方法替换为：

```python
    def run(self, symbol: str, name: str, market: str = "a-shares",
            dimension: str | None = None, refresh_cache: bool = False,
            on_progress: ProgressCallback = None
            ) -> tuple[list[AnalysisResult], dict[str, str]]:
        data_types = None
        analysis_modules = self._registry.get_analysis_modules()
        if dimension:
            data_types = DIMENSION_DATA_MAP.get(dimension, DATA_TYPES)
            analysis_modules = [m for m in analysis_modules if m.dimension == dimension]

        ctx = self.collect(symbol, name, market, refresh_cache=refresh_cache,
                           data_types=data_types, on_progress=on_progress)

        results = []
        total = len(analysis_modules)
        completed = 0
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_map = {executor.submit(m.analyze, ctx): m for m in analysis_modules}
            for future in as_completed(future_map):
                mod = future_map[future]
                try:
                    results.append(future.result())
                except Exception as e:
                    logger.error(f"分析模块 {mod.dimension} 执行失败: {e}")
                    results.append(AnalysisResult(
                        dimension=mod.dimension, status="unavailable",
                        summary=f"分析模块异常: {e}", metrics={}))
                completed += 1
                if on_progress:
                    on_progress("analyze", completed, total,
                               DIMENSION_LABELS.get(mod.dimension, mod.dimension))

        commentary = {}
        if self._llm_enabled:
            commentary = self._generate_commentary(symbol, name, results, on_progress=on_progress)

        return results, commentary
```

- [ ] **Step 2: 运行分析阶段测试**

```bash
python -m pytest tests/core/test_pipeline.py::TestPipelineProgress::test_run_calls_on_progress_for_collect_and_analyze -v
python -m pytest tests/core/test_pipeline.py::TestPipelineProgress::test_run_single_dimension_reports_correct_totals -v
```

预期：全部 PASS

---

### Task 5: 修改 _generate_commentary() 支持 on_progress

**Files:**
- Modify: `src/core/pipeline.py`

- [ ] **Step 1: 修改 _generate_commentary 方法**

将 `_generate_commentary` 方法替换为：

```python
    def _generate_commentary(self, symbol: str, name: str,
                             results: list[AnalysisResult],
                             on_progress: ProgressCallback = None) -> dict[str, str]:
        commentary = {}
        provider = self._config.get("llm.provider", "openai")
        llm = self._registry.get_llm_backend(provider)
        if llm is None:
            logger.warning(f"未找到 LLM 后端: provider={provider}")
            return commentary

        try:
            from jinja2 import Environment, FileSystemLoader
            template_dir = Path(__file__).parent.parent / "llm" / "prompt_templates"
            env = Environment(loader=FileSystemLoader(str(template_dir)))

            total = len(results) + 1  # +1 为综合总结
            completed = 0

            for result in results:
                template_name = f"{result.dimension}_{provider}.jinja2"
                try:
                    template = env.get_template(template_name)
                    prompt = template.render(name=name, symbol=symbol, **result.metrics)
                    commentary[result.dimension] = llm.generate(prompt)
                except Exception as e:
                    logger.warning(f"生成 {result.dimension} 解读失败: {e}")
                    commentary[result.dimension] = ""
                completed += 1
                if on_progress:
                    on_progress("llm", completed, total,
                               DIMENSION_LLM_LABELS.get(result.dimension, result.dimension))

            # 综合总结
            summary_template_name = f"summary_{provider}.jinja2"
            try:
                template = env.get_template(summary_template_name)
                prompt = template.render(name=name, symbol=symbol, commentary=commentary)
                commentary["summary"] = llm.generate(prompt)
            except Exception as e:
                logger.warning(f"生成综合总结失败: {e}")
                commentary["summary"] = ""
            completed += 1
            if on_progress:
                on_progress("llm", completed, total, "生成综合总结")
        except Exception as e:
            logger.error(f"LLM 解读生成过程失败: {e}")

        return commentary
```

- [ ] **Step 2: 运行完整测试**

```bash
python -m pytest tests/core/test_pipeline.py -v
```

预期：全部 8 个测试 PASS

---

### Task 6: CLI 集成 Rich 进度条

**Files:**
- Modify: `src/stock_robot/cli.py`

- [ ] **Step 1: 更新 analyze 命令**

将 `analyze` 函数中的管道运行部分（从 `llm_enabled = not no_llm...` 到 `except Exception`）替换为：

```python
    llm_enabled = not no_llm and config.get("llm.enabled", True)
    pipeline = _build_pipeline(llm_enabled=llm_enabled)

    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

    stage_order = ["collect", "analyze", "llm"] if llm_enabled else ["collect", "analyze"]
    task_id = None

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
            transient=True,
        ) as progress:
            task_id = progress.add_task("[collect] 准备中...", total=1, completed=0)

            def on_progress(stage, current, total, label):
                progress.update(task_id, completed=current, total=total,
                               description=f"[{stage}] {label}")

            results, commentary = pipeline.run(
                symbol, name,
                dimension=dimension,
                refresh_cache=refresh_cache,
                on_progress=on_progress,
            )

            if not verbose:
                progress.update(task_id, visible=False)
    except Exception as e:
        console.print(f"[red]分析失败: {e}[/red]")
        sys.exit(1)
```

- [ ] **Step 2: 运行 CLI 测试**

```bash
python -m pytest tests/test_cli.py -v
```

预期：全部 PASS（进度条不影响功能行为）

---

### Task 7: 运行全部测试

- [ ] **Step 1: 执行完整测试套件**

```bash
python -m pytest tests/ -v
```

预期：全部 102+ 个测试 PASS（新增 4 个 pipeline 测试，总共 106 个）

---

### Task 8: 核实端到端效果

- [ ] **Step 1: 运行 analyze 命令确认进度条显示**

```bash
stock-robot analyze 000001 --no-llm
```

预期：看到进度条在三个阶段（collect/analyze）中推进，最终输出报告

---

### Task 9: 提交

- [ ] **Step 1: 提交所有更改**

```bash
git add src/core/pipeline.py src/stock_robot/cli.py tests/core/test_pipeline.py
git commit -m "feat(CLI): 添加进度条和动画指示耗时操作"
```
