# CLI、本地状态与报告输出优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 用 stock-robot run 取代 api，将运行状态定位到当前项目，并以中文指标和分层路径持久化报告。

**Architecture:** 在 utils.paths 集中提供当前工作目录的 .stock_robot 路径，所有原先直接使用 Path.home() 的状态消费者改用它。报告格式化器集中处理指标显示名与文件路径；CLI 的 run 用受测的启动函数协调 Rich 进度和 Uvicorn 生命周期。

**Tech Stack:** Python 3.11、Click、Rich、Uvicorn、pytest。

**Spec:** docs/superpowers/specs/2026-08-26-cli-local-state-reporting-design.md

## Global Constraints

- stock-robot api 必须不可用；只暴露 stock-robot run。
- 本地状态目录固定为执行时 Path.cwd() / ".stock_robot"，并由 /.stock_robot/ 忽略。
- 仅翻译已收录的指标键；PE、PB、ROE 等通用缩写保留；未知键保留英文。
- 报告路径必须为 reports/<代码>/<YYYY-MM>/<代码>_<YYYYMMDD>_<HHMMSS>.md。
- 代码注释、文档、提交信息使用中文；Python 验证命令使用 .venv/Scripts/python -m pytest。

---

### Task 1: 集中项目本地状态目录

**Files:**
- Create: src/utils/paths.py
- Modify: src/utils/config.py:61-66、src/rag/engine.py:44,53-57、src/agent/memory.py:89、src/agent/graph.py:15、src/llm/openai.py:_log_usage、src/llm/claude.py:_log_usage、.gitignore
- Test: tests/utils/test_paths.py、tests/utils/test_config.py

**Interfaces:**
- Produces: utils.paths.project_state_dir() -> Path，返回并创建 Path.cwd() / ".stock_robot"。
- Consumes: 现有 Config(config_dir: Path | None = None) 的显式 config_dir 仍具有最高优先级。

- [ ] **Step 1: 写出失败的当前目录状态测试**

~~~
from utils.paths import project_state_dir

def test_project_state_dir_is_created_under_current_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert project_state_dir() == tmp_path / ".stock_robot"
    assert project_state_dir().is_dir()

def test_config_uses_project_state_dir_by_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert Config().config_dir == tmp_path / ".stock_robot"
~~~

- [ ] **Step 2: 运行测试，确认因缺少模块或旧 home 路径失败**

Run: .venv/Scripts/python -m pytest tests/utils/test_paths.py tests/utils/test_config.py -q

Expected: FAIL，指出 utils.paths 不存在或 Config().config_dir 不是当前目录下的 .stock_robot。

- [ ] **Step 3: 实现路径工具并迁移全部默认状态消费者**

~~~
# src/utils/paths.py
from pathlib import Path

def project_state_dir() -> Path:
    """返回并创建当前项目的本地状态目录。"""
    path = Path.cwd() / ".stock_robot"
    path.mkdir(parents=True, exist_ok=True)
    return path
~~~

将 Config 的默认目录改为 project_state_dir()；RAG 在 RAGEngine.__init__ 中用 project_state_dir() / "chroma" 计算默认目录（不能保留导入时冻结的 DEFAULT_PERSIST_DIR）；Memory、Graph 与两个 LLM adapter 的用量日志也改用该函数。.gitignore 加入 /.stock_robot/。

- [ ] **Step 4: 运行定向测试，确认状态目录与显式注入目录均正常**

Run: .venv/Scripts/python -m pytest tests/utils/test_paths.py tests/utils/test_config.py tests/agent/test_memory.py tests/rag/test_engine.py -q

Expected: PASS。

- [ ] **Step 5: 提交此任务**

~~~
git add src/utils/paths.py src/utils/config.py src/rag/engine.py src/agent/memory.py src/agent/graph.py src/llm/openai.py src/llm/claude.py .gitignore tests/utils/test_paths.py tests/utils/test_config.py
git commit -m "feat(本地状态): 固定状态目录到当前项目"
~~~

### Task 2: 中文指标显示与分层报告保存

**Files:**
- Modify: src/report/formatter.py:1-31、src/report/builder.py:_md_table、src/rag/sync.py:59-86
- Test: tests/report/test_formatter.py、tests/report/test_builder.py、tests/rag/test_sync.py

**Interfaces:**
- Produces: report.formatter.metric_display_name(key: object) -> str，已映射键输出中文，未知键输出 str(key)。
- Produces: ReportFormatter.save(report: str, symbol: str, output_dir: Path | None = None) -> Path，在输出根目录下创建代码和月份目录。
- Consumes: report.builder._md_table() 调用 metric_display_name() 处理字典型指标的第一列。

- [ ] **Step 1: 写出失败的显示名与路径测试**

~~~
def test_metric_display_name_translates_known_key_and_keeps_unknown_key():
    assert metric_display_name("latest_quarter") == "最新财报季度"
    assert metric_display_name("pe_ttm") == "PE(TTM)"
    assert metric_display_name("new_vendor_metric") == "new_vendor_metric"

def test_save_groups_reports_by_symbol_and_year_month(tmp_path, monkeypatch):
    monkeypatch.setattr("report.formatter.datetime", FixedDatetime)
    saved = ReportFormatter.save("# 报告", "000001", output_dir=tmp_path)
    assert saved == tmp_path / "000001" / "2026-08" / "000001_20260826_093000.md"
    assert saved.read_text(encoding="utf-8") == "# 报告"
~~~

在 test_builder.py 增加断言：latest_quarter 不出现在 Markdown 表格、最新财报季度出现；PE(TTM) 和未知键仍按上述规则输出。在 test_sync.py 创建 reports/000001/2026-08/000001_20260826_093000.md 并断言 scan_new_files() 发现它，日期为 2026-08-26、代码为 000001。

- [ ] **Step 2: 运行测试，确认现有扁平保存与英文键导致失败**

Run: .venv/Scripts/python -m pytest tests/report/test_formatter.py tests/report/test_builder.py tests/rag/test_sync.py -q

Expected: FAIL，报告路径仍位于根目录、latest_quarter 仍直接输出、嵌套文件未被同步器发现。

- [ ] **Step 3: 实现显式映射、保存层级与递归同步**

在 formatter.py 定义只读 METRIC_DISPLAY_NAMES，至少覆盖现有分析器产生的键：latest_quarter、revenue、net_profit、total_assets、total_equity、operating_cash_flow、revenue_growth_yoy、profit_growth_yoy、latest_close、industry、sector、headline_count、date、pe_ttm、pb、ps_ttm、pe_percentile、pb_percentile、dividend_yield、ma_5、ma_20、ma_60、year_high、year_low、north_bound、main_net_inflow、margin_balance、pmi、shibor_3m、cpi_yoy、usd_cny、shibor_percentile、pmi_percentile、valuation_valid、percentile_lookback_years、sample_start、sample_end 和 tag。将 _md_table 的字典键从 str(k) 改为 metric_display_name(k)。

保存时计算本地时区日期，创建 output_dir / symbol / "%Y-%m"；文件名保持代码和精确时间。同步器把 glob("*.md") 改为 rglob("*.md")，并继续对文件名调用 extract_report_info。

- [ ] **Step 4: 运行定向测试，确认显示、路径和同步均通过**

Run: .venv/Scripts/python -m pytest tests/report/test_formatter.py tests/report/test_builder.py tests/rag/test_sync.py -q

Expected: PASS。

- [ ] **Step 5: 提交此任务**

~~~
git add src/report/formatter.py src/report/builder.py src/rag/sync.py tests/report/test_formatter.py tests/report/test_builder.py tests/rag/test_sync.py
git commit -m "feat(报告): 中文化指标并分层保存报告"
~~~

### Task 3: 用 run 替换 api 并显示启动进度

**Files:**
- Modify: src/stock_robot/cli.py:499-520、tests/test_cli.py:70-103、README.md:240-246,323,348,379-392

**Interfaces:**
- Produces: stock_robot.cli.run(host: str | None, port: int | None) -> None，Click 命令名为 run。
- Produces: stock_robot.cli._run_web_server(app: Any, host: str, port: int) -> None，负责 Uvicorn 线程、Rich 进度与退出码转换。
- Consumes: _resolve_api_bind(host, port, config)，保持 CLI 参数优先于配置。

- [ ] **Step 1: 写出失败的命令接口与启动反馈测试**

~~~
def test_run_command_help_is_available_and_api_is_unknown():
    runner = CliRunner()
    assert runner.invoke(main, ["run", "--help"]).exit_code == 0
    assert runner.invoke(main, ["api", "--help"]).exit_code != 0

def test_run_builds_server_and_reports_ready_url(mocker):
    mocker.patch("stock_robot.cli._run_web_server")
    mocker.patch("api.bootstrap.build_agent_core", return_value=object())
    mocker.patch("api.app.create_app", return_value=object())
    result = CliRunner().invoke(main, ["run", "--port", "8000"])
    assert result.exit_code == 0
    assert "正在启动 HTTP 服务" in result.output
    assert "http://127.0.0.1:8000" in result.output
~~~

测试同时断言 _run_web_server 接收 create_app 返回的 app 和解析后的地址；不在单元测试中启动实际端口。

- [ ] **Step 2: 运行测试，确认 run 命令不存在、api 仍存在而失败**

Run: .venv/Scripts/python -m pytest tests/test_cli.py -q

Expected: FAIL，run 是未知命令且 api 仍可调用。

- [ ] **Step 3: 以受控线程实现 run 启动阶段进度**

将 @main.command() 的 api 函数改为 @main.command("run") def run(...)。在命令内用 Progress(SpinnerColumn(), TextColumn(...), BarColumn(), console=console, transient=False) 更新“构建 Agent 核心”“创建 Web 应用”“正在启动 HTTP 服务”三个阶段。

_run_web_server 创建 uvicorn.Config 与 uvicorn.Server，以 threading.Thread 调用 server.run()；轮询 server.started，服务线程异常或超时则抛出 RuntimeError。run 捕获该具体运行异常，打印红色错误后 raise click.ClickException(...)。监听成功时标记进度完成并打印 URL，随后 thread.join()；KeyboardInterrupt 调用 server.should_exit = True 后等待线程结束。

- [ ] **Step 4: 运行 CLI 定向测试，确认命令替换和启动反馈通过**

Run: .venv/Scripts/python -m pytest tests/test_cli.py -q

Expected: PASS。

- [ ] **Step 5: 更新用户文档并提交此任务**

将 README 中的 stock-robot api 全部替换为 stock-robot run，将 ~/.stock_robot 替换为 ./.stock_robot，报告树和 RAG 摄入样例更新到新路径。然后执行：

~~~
git add src/stock_robot/cli.py tests/test_cli.py README.md
git commit -m "feat(命令行): 使用 run 启动 Web 服务并显示进度"
~~~

### Task 4: 集成验收

**Files:**
- Modify: README.md（只在前一任务遗漏报告或状态路径时补齐）
- Test: tests/test_cli.py、tests/report/test_formatter.py、tests/rag/test_sync.py

**Interfaces:**
- Consumes: Tasks 1–3 的 CLI、状态目录、报告与同步接口。
- Produces: 真实 stock-robot run 启动、浏览器发送与响应的验收证据。

- [ ] **Step 1: 运行受影响单元测试组合**

Run: .venv/Scripts/python -m pytest tests/test_cli.py tests/utils/test_paths.py tests/utils/test_config.py tests/report/test_formatter.py tests/report/test_builder.py tests/rag/test_sync.py tests/agent/test_memory.py tests/rag/test_engine.py -q

Expected: PASS。

- [ ] **Step 2: 运行静态质量检查**

Run: pyright src/stock_robot/cli.py src/utils/paths.py src/utils/config.py src/report/formatter.py src/report/builder.py src/rag/sync.py src/rag/engine.py src/agent/memory.py src/agent/graph.py src/llm/openai.py src/llm/claude.py

Expected: 0 errors。

- [ ] **Step 3: 按真实路径启动 Web 服务并验证首条会话**

Run: ./.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 25618

Expected: 终端先显示三阶段启动进度，随后显示 http://127.0.0.1:25618；浏览器打开该地址，发送一条真实消息并观察到 SSE 的加载状态和正文或可重试错误。

- [ ] **Step 4: 记录验收结果并提交收尾变更（若有）**

若步骤 1–3 没有产生文件修改，不创建空提交。若 README 或测试有验收修正，执行：

~~~
git add README.md tests/test_cli.py tests/report/test_formatter.py tests/rag/test_sync.py
git commit -m "test(验收): 覆盖本地状态与报告输出流程"
~~~

## Plan Self-Review

- 规格覆盖：Task 1 覆盖当前目录状态与忽略规则；Task 2 覆盖翻译、层次化保存和 RAG 递归扫描；Task 3 覆盖 run 命令替换与进度；Task 4 覆盖真实 Web UI 路径验收与受影响检查。
- 占位符检查：计划中没有待定实现项；每项都定义了文件、接口、失败测试、命令与预期结果。
- 类型一致性：project_state_dir() 返回 Path；ReportFormatter.save() 返回 Path；_run_web_server() 仅被 run() 调用并接收实际 ASGI app 与解析后的地址。

