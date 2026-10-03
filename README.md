# Stock Robot

<img src="assets/logo-reimu-piggy-bank-v2.png" alt="Stock Robot Logo" width="96">

AI 驱动的股票与指数分析研报助手，提供 Web 界面、CLI 和 HTTP API。数据采集与指标计算由代码完成，LLM 负责生成自然语言解读。

## 核心能力

- **个股与指数分析**：A 股多维度研报，宽基、行业、海外及红利低波、自由现金流等策略指数分析。
- **对话式投研**：Agent 拆解问题、调用工具并汇总结果，支持知识库检索增强。
- **配置雷达**：ETF 标的池研究评分、历史表现比较与池级策略回测。
- **报告库与回测**：浏览和下载研报，保存单股信号及 ETF 策略回测产物。
- **订阅推送**：配置分析任务与推送渠道。
- **多端接入**：CLI、Web、HTTP API，以及 MCP 工具接入。

## 快速开始

需要 Python 3.11 及以上。进入仓库根目录后，使用独立 `.venv` 安装并运行，避免与其他 Python 环境混用。

### 1. 安装

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Linux：

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"
```

### 2. 配置并启动 Web

以下示例使用 Windows 启动器；Linux 将 `.\scripts\stock-robot.ps1` 替换为 `./scripts/stock-robot.sh`。无需手动激活虚拟环境。

```powershell
.\scripts\stock-robot.ps1 config set data.disclaimer_accepted true
.\scripts\stock-robot.ps1 config set llm.api_key "你的 API Key"
.\scripts\stock-robot.ps1 run
```

默认使用 OpenAI 后端。启动后打开 <http://127.0.0.1:25618>；端口可通过 `run --port` 指定。其他 LLM 配置见[使用指南](docs/使用指南.md#配置-llm)。

### 3. 生成第一份报告

不配置 LLM 也可以先查看个股数据与指标：

```powershell
.\scripts\stock-robot.ps1 analyze 000001 --no-llm
```

股票报告保存到 `reports/stock/`，指数报告保存到 `reports/index/`，回测产物保存到 `reports/backtests/`。配置与缓存位于当前工作目录的 `.stock_robot/`。

## 常用命令

下面的 `stock-robot` 假定项目虚拟环境已加入 PATH；也可继续使用上述平台启动器。环境设置见[运行环境说明](docs/运行环境.md)。

```sh
stock-robot help                          # 查看命令列表
stock-robot help index                    # 查看具体命令的参数
stock-robot help config set               # 查看多级命令帮助
stock-robot analyze 000001                # 个股研报
stock-robot index 000300 000905            # 指数分析与对比
stock-robot index 515300                   # 分析 ETF 跟踪的指数 930740
stock-robot index "国证自由现金流"          # 按名称分析策略指数
stock-robot chat                          # 交互式投研，输入 /help 查看聊天命令
stock-robot radar universe list           # 查看配置雷达标的池
```

直接运行 `stock-robot` 也会显示帮助；各级 `--help` 保持可用。完整命令示例、参数说明和配置方法见[使用指南](docs/使用指南.md)。

## 数据与使用边界

- 个股分析支持 A 股；指数支持范围可在 Web“股指分析 → 指数”目录中搜索，目录数据维护于 `data/index_mapping.csv`。
- 已收录的 ETF 代码会解析为其跟踪指数。ETF 自身表现与指数表现分别展示，例如 515300 跟踪 930740。
- 目录收录不代表所有指标均可用。公开数据缺失、历史不足或数据源失败时，报告会说明覆盖范围与缺失原因；公开官方 PB 日频历史尚未接入，PB 历史分位不可用。
- 指数价格收益不含分红再投资；当前成分特征与历史回测有不同口径。历史表现不代表未来收益。

详细来源、估值口径和策略限制见[指数操作与数据说明](docs/使用指南.md#策略指数与-etf-跟踪分析)。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [运行环境](docs/运行环境.md) | Windows/Linux 安装、启动器与虚拟环境 |
| [使用指南](docs/使用指南.md) | CLI、配置、Web/API、RAG、MCP、回测及常见问题 |
| [配置雷达自动采集部署](docs/配置雷达自动采集部署.md) | 数据采集与部署操作 |

## 开发

技术栈：Python、Pydantic、Click/Rich、AkShare、FastAPI、SQLite；分析管道按数据、分析、LLM 与报告层组织。开发规范见 [AGENTS.md](AGENTS.md)。

知识库（RAG）为可选能力：普通安装不包含 chromadb、sentence-transformers，需要时执行 `python -m pip install -e ".[rag]"`（使用当前独立环境的解释器）。不安装 RAG 时分析、报告、指数、回测与订阅仍可使用，Web 对话不注册知识库检索工具。运行包含 RAG 的开发测试使用 `.[dev,rag]`；`scripts/bootstrap-dev.ps1` 会同步这两个 extra。

自举部署使用从 `uv.lock` 导出的 `requirements-core.lock.txt` 或 `requirements-rag.lock.txt`：先以 `--require-hashes -r <清单>` 安装依赖，再以 `--no-deps -e .` 安装项目。清单需在依赖或锁文件变化后重新导出，不包含当前项目和开发 extra；目标平台实际可安装性仍须验证。

```powershell
.\scripts\bootstrap-dev.ps1
.\scripts\verify.ps1 -Scope Changed                 # 当前改动
.\scripts\verify.ps1 -Scope Changed -Source Staged  # 暂存改动
```

全量检查需显式使用 `-Scope Full`。测试临时文件位于 `tmp/pytest/`，成功后自动删除，失败时保留用于排查。官方数据探针与工作树验证见[指数数据接入提效操作说明](docs/superpowers/plans/2026-10/2026-10-02-index-data-efficiency-plan.md#操作说明)。

## 免责声明

本工具仅用于个人学习与研究。数据和观点不构成投资建议，数据准确性与时效性无法保证；使用者需自行判断并承担投资风险。
