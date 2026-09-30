# CLI 进度指示设计

## 目标

在 CLI 执行耗时操作时显示进度条或动画，让用户明确感知软件正在运行而非卡住。

## 现状

`stock-robot analyze` 执行流程中有三段耗时操作：

1. **数据采集** — 5 个数据源并行拉取（AkShare 网络请求），耗时取决于网络和接口响应速度
2. **分析计算** — 5 个分析模块并行执行，通常很快但仍有等待
3. **LLM 解读** — 最多 6 次 LLM API 调用（5 个维度 + 综合总结），单次调用可达数秒

当前 `--verbose` 模式下仅打印静态提示文字（"正在采集数据..."），无进度反馈。

## 设计决策

### 混合模式

- 数据采集和分析阶段：确定进度条（Rich `BarColumn`），因为总步骤数明确
- LLM 解读阶段：旋转动画（Rich `SpinnerColumn`），因为 LLM 调用时长不可预测但步数明确

### 回调解耦

Pipeline 不直接依赖 Rich，通过回调函数让 CLI 层注入渲染逻辑。

## 详细设计

### 1. 回调类型定义

在 `Pipeline` 中：

```python
from collections.abc import Callable

ProgressCallback = Callable[[str, int, int, str], None] | None
# stage: "collect" | "analyze" | "llm"
# current: 当前步骤序号（从 1 开始）
# total: 总步骤数
# label: 当前步骤描述文字
```

### 2. Pipeline 修改

| 方法 | 修改点 |
|---|---|
| `collect()` | 新增 `on_progress` 参数，在 `fetch_one()` 完成后回调 |
| `run()` | 新增 `on_progress` 参数，透传给 `collect()`，在 `as_completed` 循环中回调 |
| `_generate_commentary()` | 新增 `on_progress` 参数，在 for 循环中每处理一个维度回调，遍历结束后回调 summary |

`on_progress` 默认为 `None`，为 `None` 时不调用，保持向后兼容。所有现有调用方无需改动。

### 3. 进度上报节点

**collect 阶段（5 步）：**

| 步骤 | label |
|---|---|
| 1/5 | "采集财务数据" |
| 2/5 | "采集价格数据" |
| 3/5 | "采集估值数据" |
| 4/5 | "采集行业数据" |
| 5/5 | "采集舆情数据" |

**analyze 阶段（1~5 步，取决于 --dimension 参数）：**

| 步骤 | label |
|---|---|
| 1/N | "财务分析" |
| 2/N | "技术面分析" |
| 3/N | "估值分析" |
| 4/N | "行业分析" |
| 5/N | "舆情分析" |

**llm 阶段（1~6 步，跳过失败的维度，summary 末尾追加）：**

| 步骤 | label |
|---|---|
| 1/N | "生成财务解读" |
| ... | ... |
| N/N | "生成综合总结" |

### 4. CLI 层实现

```python
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

def _make_progress():
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.completed}/{task.total}"),
        console=console,
    )
```

`analyze` 命令中：
1. 创建 `Progress` 实例
2. 定义 `on_progress` 回调，内部调用 `progress.update(task_id, ...)`
3. 将回调传入 `pipeline.run(symbol, name, ..., on_progress=on_progress)`
4. 用 `with progress:` 上下文管理器确保异常时自动清理

### 5. 与 verbose 模式的交互

- 进度条始终显示（只要不是被重定向的非 TTY）
- `--verbose` 时进度条在上方，日志文字在下方，Rich 会自动处理布局
- 非 TTY 输出（如管道到文件）时 Rich 自动降级为无进度条，不影响脚本化使用

### 6. 无 LLM 模式

`--no-llm` 模式下 pipeline 设置 `llm_enabled=False`，`_generate_commentary` 不会被调用，对应阶段进度条不显示。

## 影响范围

### 修改文件

| 文件 | 改动 |
|---|---|
| `src/core/pipeline.py` | `collect`、`run`、`_generate_commentary` 加 `on_progress` 参数和回调调用 |
| `src/stock_robot/cli.py` | `analyze` 命令创建 Progress 并传入回调 |
| `tests/core/test_pipeline.py` | 补充 `on_progress` 回调的单元测试 |
| `tests/test_cli.py` | 不需要修改（进度条不影响 CLI 命令的功能行为） |

### 不修改

- 分析模块、数据源、LLM 适配器 — 不感知进度
- 配置管理、缓存 — 无关系
- 测试现有的 6 个 CLI 测试 — 进度条不影响断言
