# Python 编码约定

由根目录 `AGENTS.md` 按任务触发读取；适用内容具有同等约束力，不需在每次任务中加载其他主题。

## 检查工具

- **Ruff**：项目级配置在 `pyproject.toml` 的 `[tool.ruff]`，规则集与扩展默认一致，勿在 `lint.select` 中随意添加规则组（会引入大量新告警）
- **Pylance**（basic 模式）：项目级配置在 `pyproject.toml` 的 `[tool.pyright]`；CLI 验证用 `pyright`（与 Pylance 同引擎）
- **pytest**：必须用 `.venv/Scripts/python -m pytest` 运行

## 编码规范

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
