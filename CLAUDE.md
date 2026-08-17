# CLAUDE.md

## 项目概述

Stock Robot — AI 驱动的股票分析研报助手。输入 A 股代码，输出多维度分析报告。

## 语言规范

- 代码注释使用中文
- 文档（README、spec、plan 等）使用中文
- Git 提交信息使用中文，格式如下：

```
feat(功能点): 功能简述

详细描述（可选）
```

示例：
```
feat(数据层): 添加 AkShare 数据源适配器
feat(分析模块): 实现财务指标分析器
fix(缓存): 修复 SQLite 缓存过期判断逻辑错误
test(集成): 添加管道端到端集成测试
chore(项目): 初始化项目脚手架
```

## 代码检查与编码规范

### 检查工具

- **Ruff**：项目级配置在 `pyproject.toml` 的 `[tool.ruff]`，规则集与扩展默认一致，勿在 `lint.select` 中随意添加规则组（会引入大量新告警）
- **Pylance**（basic 模式）：项目级配置在 `pyproject.toml` 的 `[tool.pyright]`；CLI 验证用 `pyright`（与 Pylance 同引擎）
- **pytest**：必须用 `.venv/Scripts/python -m pytest` 运行

### 检查时机（重要）

- 全量检查耗时约 4 分钟（ruff 数秒 + pyright ~1 分钟 + pytest ~3 分钟），**默认不要在每次任务中自动运行**
- 仅在以下情况运行全量检查：
  1. 用户明确要求（如"检查一下"、"验证一下"、"提交前检查"）
  2. 修改了 src/ 核心逻辑且用户要求确认无回归
- 日常编码依赖 IDE 实时诊断；**修复循环中不要反复跑全量 pyright**，优先秒级单文件检查：
  - `pyright <文件>` 或 `pyright <文件1> <文件2>`（单文件约 2 秒，只检查改动波及的文件）
  - `ruff check <文件>`
  - `.venv/Scripts/python -m pytest <测试文件> -q`
- pyright/ruff 输出量大时先聚合（`--outputjson | python 汇总为 行号+规则+摘要`），避免原始输出灌入上下文
- 涉及类型系统边界行为（Protocol/ClassVar 结构匹配、`cast` 到 Literal、`# pyright: ignore` 规则码）先用 10 行临时探针 + `pyright <探针>` 验证再大规模应用
- `mcp__ide__getDiagnostics` 依赖 IDE 连接，时有时无，**不可作为复查依赖**

### 环境事实（勿重复探测）

- `python`/`pip` 裸命令指向 Anaconda（`D:\Private File\Anaconda`）——**所有 Python 命令显式用 `.venv/Scripts/python`**；Anaconda 缺 pytest-asyncio，用它跑 async 测试会误报失败
- `pyright` 在 `C:\Users\25618\AppData\Roaming\Python\Python312\Scripts\pyright`，直接命令可用；`.venv` 中未安装
- `ruff` 不在 PATH，用 VS Code 扩展 bundled 二进制（**版本号会随扩展升级变化，必须用通配符**）：
  `~/.vscode/extensions/charliermarsh.ruff-*/bundled/libs/bin/ruff.exe`
- 环境探测合并为一条命令（`which python ruff pyright`），不要逐个探测

### 异常处理

- 禁止裸 `except Exception`，以下两类隔离边界除外，且必须记录日志并加 `# noqa: BLE001` 注明理由：
  1. 第三方 SDK/网络边界（akshare、requests、LLM SDK、chromadb、子进程）——异常类型不可预测，兜底降级
  2. 管道/工具执行隔离——单个数据源、分析模块、目标失败不影响整体
- `src/data/akshare.py` 整文件为数据源适配层，通过 `[tool.ruff.lint.per-file-ignores]` 豁免 BLE001
- `try/except ... pass` 禁止静默吞异常（S110）：pass 前加 `logger.debug` 说明降级原因
- 解析类异常（日期、数字转换）收窄为具体类型（`ValueError`/`TypeError`/`KeyError`）
- raise 必须抛具体异常实例（TRY002），禁止 `raise "字符串"`

### 日期时间（DTZ 系列）

- 禁止 naive datetime：`date.today()` → `datetime.now().astimezone().date()`；`datetime.now()` → `datetime.now().astimezone()`；`strptime(...)` 后接 `.astimezone()`

### 类属性

- 可变类属性（RUF012）默认禁止；**工具定义类**的 `name`/`description`/`parameters`/`tags`/`source` 为类级常量元数据（只读约定），与 `tests/**` 一起通过 per-file-ignores 豁免
- 协议（Protocol）成员声明为**普通属性**，不要用 ClassVar——动态工具（实例属性实现，如测试 FakeTool）无法满足 ClassVar 协议成员的结构匹配

### 类型标注

- 动态配置/第三方数据返回值诚实标注 `Any`：`Config.get()` 返回 `Any`；akshare/chromadb 等无类型标注 SDK 的返回值显式 `df: Any = ak.xxx()`
- akshare 版本间接口漂移：可选接口用 `getattr(ak, "接口名")(...)` 包裹在 `except AttributeError` 回退中
- 枚举字符串字段（`AnalysisResult.dimension`、`AnalysisTarget.index_style` 等 Literal 类型）构造时用 Literal 标注或 `cast(...)`，禁止裸 str
- 闭包捕获的变量不做流收窄：在闭包内复制到局部变量再判空
- `subprocess.Popen.stdin/stdout` 类型为 `IO | None`，使用前显式判空
- Pydantic 模型私有扩展字段用 `PrivateAttr` 声明（如 `IndustryData._target_mcap`）
- 字段名遮蔽类型名时（如 `date: date`），后续同名字段用字符串标注 `"date | None"`
- 测试假实现（FakeEmbeddingProvider 等）应名义继承被替换的基类，避免结构匹配失败

### 其他约定

- 导入按 ruff 规则排序（I001），未使用导入/变量直接删除或改 `_`（F401/F841/RUF059）
- 嵌套 if 能合并就合并（SIM102）；遍历只取值的字典用 `.values()`（PERF102）
- 类级常量列表/字典用 ClassVar 标注

### 提交前检查（用户要求提交时执行）

```
ruff check .                # 0 错误
pyright                     # 0 错误
.venv/Scripts/python -m pytest -q   # 全绿
```

## 技术栈

- Python 3.11+
- Pydantic v2, click, rich, AkShare, pandas, Jinja2, pytest
- openai SDK + anthropic SDK
- SQLite 缓存

## 架构

模块化管道：`CLI → 数据层 → 分析层 → LLM 层 → 报告构建 → 输出`

各层通过抽象基类解耦，数据采集和指标计算由确定性代码完成，LLM 仅在最后一步生成自然语言解读。

## 项目结构

```
stock_robot/
├── cli.py                     # CLI 入口
├── src/
│   ├── core/                  # 管道调度器、注册机制
│   ├── data/                  # 数据源抽象、Schema、缓存
│   ├── analysis/              # 分析模块（财务、技术、估值、行业、舆情）
│   ├── llm/                   # LLM 后端适配器、提示词模板
│   ├── report/                # 报告构建器
│   └── utils/                 # 配置管理、股票代码工具
├── tests/
├── docs/superpowers/          # 设计文档和实现计划
├── pyproject.toml
└── README.md
```
