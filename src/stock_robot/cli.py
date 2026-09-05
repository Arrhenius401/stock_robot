"""Stock Robot CLI — AI 驱动的股票分析研报助手"""
import logging
import os

os.environ["TQDM_DISABLE"] = "1"

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
logger = logging.getLogger(__name__)

from utils.config import Config


def _create_cli_progress():
    """创建三条 CLI 命令共用的进度条。"""
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        console=console,
        transient=True,
    )


def _get_registry():
    """构建默认注册表"""
    from analysis.financial import FinancialAnalyzer
    from analysis.industry import IndustryAnalyzer
    from analysis.sentiment import SentimentAnalyzer
    from analysis.technical import TechnicalAnalyzer
    from analysis.valuation import ValuationAnalyzer
    from core.registry import Registry
    from data.akshare import AkShareAdapter

    reg = Registry()
    reg.register_data_source(AkShareAdapter())
    reg.register_analysis_module(FinancialAnalyzer())
    reg.register_analysis_module(TechnicalAnalyzer())
    reg.register_analysis_module(ValuationAnalyzer())
    reg.register_analysis_module(IndustryAnalyzer())
    reg.register_analysis_module(SentimentAnalyzer())
    return reg


def _register_llm(reg, config):
    """注册 LLM 后端（复用 bootstrap 构建逻辑，含重试/超时参数）"""
    from api.bootstrap import build_llm

    llm = build_llm(config)
    if llm is not None:
        reg.register_llm_backend(llm, provider=config.get("llm.provider", "openai"))


def _build_pipeline(llm_enabled=True):
    """构建管道"""
    from core.pipeline import Pipeline
    from utils.config import Config

    config = Config()
    reg = _get_registry()
    _register_llm(reg, config)
    return Pipeline(registry=reg, config=config, llm_enabled=llm_enabled)


def _get_cache():
    """获取缓存管理器"""
    from data.cache import CacheManager
    from utils.config import Config
    config = Config()
    return CacheManager(db_path=config.config_dir / "cache.db")


def _check_disclaimer(config):
    """检查免责声明是否已接受"""
    if not config.get("data.disclaimer_accepted", False):
        console.print(Panel.fit(
            "[bold yellow]免责声明[/bold yellow]\n\n"
            "本工具仅用于个人学习和研究目的。所有分析数据和观点不构成任何投资建议。\n"
            "股票投资有风险，入市需谨慎。数据来源的准确性和时效性无法保证，使用者需自行判断。\n\n"
            '输入 [bold]stock-robot config set data.disclaimer_accepted true[/bold] 确认已阅读。',
            title="首次使用"
        ))
        return False
    return True


def _convert_value(value: str):
    """将字符串转换为合适类型"""
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


@click.group()
@click.version_option(version="0.1.0")
def main():
    """Stock Robot — AI 驱动的股票分析研报助手"""


@main.command()
@click.argument("symbol")
@click.option("--dimension", "-d", help="指定分析维度 (financial/technical/valuation/industry/sentiment)")
@click.option("--refresh-cache", is_flag=True, help="强制刷新缓存")
@click.option("--no-llm", is_flag=True, help="仅输出数据，跳过 LLM 解读")
@click.option("--verbose", "-v", is_flag=True, help="显示采集和分析过程")
@click.option("--with-market", is_flag=True, help="在报告中嵌入大盘环境分析")
def analyze(symbol, dimension, refresh_cache, no_llm, verbose, with_market):
    """分析股票并生成研报"""
    from report.formatter import ReportFormatter
    from utils.config import Config
    from utils.symbols import normalize_symbol, resolve_name, validate_symbol

    config = Config()
    if not _check_disclaimer(config):
        return

    if not validate_symbol(symbol):
        console.print(f"[red]无效的股票代码: {symbol}[/red]")
        console.print(
            "请输入 6 位数字代码（如 000001、600036），"
            "可选前缀 [bold]sh[/bold]（沪市）或 [bold]sz[/bold]（深市）"
        )
        sys.exit(1)

    symbol = normalize_symbol(symbol)

    llm_enabled = not no_llm and config.get("llm.enabled", True)
    pipeline = _build_pipeline(llm_enabled=llm_enabled)

    try:
        with _create_cli_progress() as progress:
            task_id = progress.add_task("正在查询股票名称...", total=None)

            name = resolve_name(symbol) or symbol

            if verbose:
                console.print(f"[dim]正在分析: {name} ({symbol})[/dim]")

            def on_progress(stage, current, total, label):
                progress.update(task_id, completed=current, total=total,
                               description=f"[{stage}] {label}")

            results, commentary, ctx = pipeline.run(
                symbol, name,
                dimension=dimension,
                refresh_cache=refresh_cache,
                on_progress=on_progress,
            )

            if not verbose:
                progress.update(task_id, visible=False)
    except Exception as e:  # noqa: BLE001 — CLI 顶层兜底，打印错误并退出
        console.print(f"[red]分析失败: {e}[/red]")
        sys.exit(1)

    # 大盘环境快照（可选，供报告中嵌入指数环境摘要）
    market_env = None
    if with_market:
        try:
            from index.pipeline import IndexPipeline
            index_pipeline = IndexPipeline()
            snapshot = index_pipeline.get_snapshot("000300")
            if snapshot:
                market_env = snapshot
        except Exception:  # noqa: BLE001 — 大盘快照为可选信息，失败静默跳过
            logger.debug("大盘快照获取失败，跳过")

    from report.scoring import build_report
    from report.signal import load_signal_config
    report = build_report(symbol, name, results, commentary, ctx,
                          no_llm=no_llm, market_env=market_env,
                          signal_cfg=load_signal_config(config))

    saved_path = ReportFormatter.save(report, symbol, category="stock")
    console.print(ReportFormatter.to_rich_markdown(report))
    console.print(f"\n[dim]报告已保存至: {saved_path}[/dim]")


def _render_index_report(report):
    """将 IndexReport 渲染为终端可读的 Rich Markdown"""
    from datetime import datetime

    from jinja2 import Environment, FileSystemLoader

    from report.builder import _md_table

    template_dir = Path(__file__).parent.parent / "report" / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["md_table"] = _md_table
    template = env.get_template("index_report.jinja2")
    md = template.render(
        code=report.code,
        name=report.name,
        generated_at=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        overview=report.overview,
        section_technical=report.section_technical,
        section_valuation=report.section_valuation,
        section_capital=report.section_capital,
        section_macro=report.section_macro,
        section_sentiment=report.section_sentiment,
        tag_technical=report.tag_technical,
        tag_valuation=report.tag_valuation,
        tag_capital=report.tag_capital,
        tag_macro=report.tag_macro,
        tag_sentiment=report.tag_sentiment,
        composite_comment=report.composite_comment,
        position_coeff=report.position_coeff,
        visible_sections=report.visible_sections,
    )
    from report.formatter import ReportFormatter
    return ReportFormatter.to_rich_markdown(md)


def _render_index_report_md(report) -> str:
    """将 IndexReport 渲染为纯 Markdown 文本"""
    from datetime import datetime

    from jinja2 import Environment, FileSystemLoader

    from report.builder import _md_table

    template_dir = Path(__file__).parent.parent / "report" / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["md_table"] = _md_table
    template = env.get_template("index_report.jinja2")
    return template.render(
        code=report.code,
        name=report.name,
        generated_at=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        overview=report.overview,
        section_technical=report.section_technical,
        section_valuation=report.section_valuation,
        section_capital=report.section_capital,
        section_macro=report.section_macro,
        section_sentiment=report.section_sentiment,
        tag_technical=report.tag_technical,
        tag_valuation=report.tag_valuation,
        tag_capital=report.tag_capital,
        tag_macro=report.tag_macro,
        tag_sentiment=report.tag_sentiment,
        composite_comment=report.composite_comment,
        position_coeff=report.position_coeff,
        visible_sections=report.visible_sections,
    )


def _render_compare_table(compare) -> Table | str:
    """渲染横向对比表格"""
    if not compare or not compare.rows:
        return ""
    from rich.table import Table
    # CompareTable 表头为中文、行内 key 为英文，需映射后再取值
    header_key_map = {
        "指数名称": "name", "最新点位": "latest", "涨跌幅": "change",
        "PE 分位": "pe_pct", "PB 分位": "pb_pct", "趋势": "trend",
        "估值": "valuation", "资金": "capital", "综合评级": "composite",
    }
    table = Table(title="指数横向对比")
    for h in compare.headers:
        table.add_column(h)
    for row in compare.rows:
        table.add_row(*[str(row.get(header_key_map.get(h, h), "")) for h in compare.headers])
    return table


@main.command()
@click.argument("symbols", nargs=-1, required=True)
@click.option("--style", "-s", type=click.Choice(["broad", "sector", "overseas"]),
              help="指数类别（默认自动检测）")
@click.option("--output", "-o", type=click.Choice(["terminal", "markdown"]),
              default="terminal", help="输出格式")
@click.option("--compare-only", is_flag=True, help="仅输出横向对比表格")
def index(symbols, style, output, compare_only):
    """分析指数并生成报告"""
    from data.index_mapping import IndexMapping
    from data.schemas import AnalysisTarget
    from index.pipeline import IndexPipeline
    from utils.config import Config
    from utils.symbols import normalize_index_symbol, validate_index_symbol

    config = Config()
    if not _check_disclaimer(config):
        return

    mapping = IndexMapping()
    targets = []

    for raw in symbols:
        if not validate_index_symbol(raw):
            console.print(f"[red]无效的指数代码: {raw}[/red]")
            sys.exit(1)

        normalized = normalize_index_symbol(raw)
        entry = mapping.lookup(normalized)

        if entry is None:
            if style is None:
                console.print(
                    f"[red]无法识别指数 {normalized}，"
                    f"请用 --style 指定类别 (broad/sector/overseas)[/red]"
                )
                sys.exit(1)
            index_style = style
            name = raw
            market = "a-shares"
        else:
            index_style = entry.index_style
            name = entry.name
            market = entry.market

        targets.append(AnalysisTarget(
            target_type="index", symbol=normalized,
            name=name, market=market, index_style=index_style,
        ))

    pipeline = IndexPipeline()
    with _create_cli_progress() as progress:
        task_id = progress.add_task("正在分析指数...", total=None)

        def on_progress(stage, current, total, label):
            progress.update(task_id, completed=current, total=total,
                           description=f"[{stage}] {label}")

        result = pipeline.run(targets, on_progress=on_progress)
        progress.update(task_id, visible=False)

    from report.formatter import ReportFormatter

    if not compare_only:
        for report in result.reports:
            if output == "terminal":
                console.print(_render_index_report(report))
            elif output == "markdown":
                saved = ReportFormatter.save(
                    _render_index_report_md(report),
                    report.code,
                    category="index",
                )
                console.print(f"[green]报告已保存: {saved}[/green]")

    if result.compare is not None:
        console.print(_render_compare_table(result.compare))

    if result.errors:
        for err in result.errors:
            console.print(f"[yellow]警告: {err}[/yellow]")


@main.command()
@click.argument("symbol")
@click.option("--strategy", default=None, help="策略 ID（默认读配置 backtest.default_strategy）")
@click.option("--start", required=True, type=click.DateTime(formats=["%Y-%m-%d"]),
              help="回测开始日期（YYYY-MM-DD）")
@click.option("--end", required=True, type=click.DateTime(formats=["%Y-%m-%d"]),
              help="回测结束日期（YYYY-MM-DD）")
@click.option("--benchmark", default=None, help="基准 ID（默认读配置 backtest.default_benchmark）")
def backtest(symbol, strategy, start, end, benchmark):
    """对单只股票执行技术信号回测并输出可复现产物"""
    from backtest.artifacts import write_backtest_artifacts
    from backtest.data import BacktestDataError, HistoricalPriceProvider
    from backtest.models import BacktestRequest
    from backtest.runner import BacktestRunner
    from backtest.strategy import StrategyConfigError, StrategyRepository
    from data.akshare import AkShareAdapter
    from utils.symbols import normalize_symbol, validate_symbol

    config = Config()
    if not _check_disclaimer(config):
        return

    if not validate_symbol(symbol):
        console.print(f"[red]✗ 无效的股票代码: {symbol}[/red]")
        console.print(
            "请输入 6 位数字代码（如 000001、600036），"
            "可选前缀 [bold]sh[/bold]（沪市）或 [bold]sz[/bold]（深市）"
        )
        sys.exit(1)
    symbol = normalize_symbol(symbol)

    strategy_id = strategy or config.get("backtest.default_strategy")
    benchmark_id = benchmark or config.get("backtest.default_benchmark")

    # 基准 ID 必须存在于配置；不存在时提前给出可选列表，避免空跑取数
    benchmarks = config.get("backtest.benchmarks", {})
    if not isinstance(benchmarks, dict) or benchmark_id not in benchmarks:
        available = ", ".join(benchmarks.keys()) if isinstance(benchmarks, dict) else ""
        console.print(f"[red]✗ 未配置基准: {benchmark_id}（可选: {available}）[/red]")
        sys.exit(1)

    try:
        strategies_dir = Path(__file__).parents[2] / "config" / "strategies"
        strategy_repo = StrategyRepository(strategies_dir)
        strategy_repo.get(strategy_id)  # 提前校验策略存在，失败时快速红字退出

        request = BacktestRequest(
            symbol=symbol,
            start_date=start.date(),
            end_date=end.date(),
            strategy_id=strategy_id,
            benchmark_id=benchmark_id,
            initial_cash=float(config.get("backtest.initial_cash", 100000.0)),
        )
        runner = BacktestRunner(
            provider=HistoricalPriceProvider(AkShareAdapter()),
            strategies=strategy_repo,
            config=config,
        )
        result = runner.run(request)
        output = write_backtest_artifacts(result)
    except (BacktestDataError, StrategyConfigError, ValueError) as e:
        console.print(f"[red]✗ 回测失败: {e}[/red]")
        sys.exit(1)
    except Exception as e:  # CLI 顶层兜底，数据源/仿真异常类型不可预测（logger.exception 豁免 BLE001）
        logger.exception("回测失败")
        console.print(f"[red]✗ 回测失败: {e}[/red]")
        sys.exit(1)

    table = Table(title=f"回测指标：{symbol}")
    table.add_column("指标", style="cyan")
    table.add_column("数值", justify="right", style="green")
    for key, value in result.metrics.items():
        table.add_row(key, f"{value:.4f}")
    console.print(table)
    for warning in result.warnings:
        console.print(f"[yellow]⚠ {warning}[/yellow]")
    console.print(f"[green]报告已保存: {output}[/green]")


@main.command("industry-mapping")
@click.argument("action_or_symbol", required=False)
@click.option("--resume", is_flag=True, help="从候选文件的断点继续全量构建")
@click.option("--delay", type=click.FloatRange(min=0.5, max=30.0), default=1.5,
              show_default=True, help="行业请求基础间隔（秒）；限流时建议 6 秒以上")
@click.option("--verbose", "-v", is_flag=True, help="显示抓取明细")
def industry_mapping(action_or_symbol, resume, delay, verbose):
    """重建/更新行业映射表。

    validate → 抽样校验上游页面与解析规则；
    rebuild → 构建候选文件（可配合 --resume 续跑）；
    publish → 校验后原子发布候选文件；
    migrate-placeholders → 将旧占位分类迁移为显式缺失；
    股票代码 → 单只秒级更新。
    """
    from data.industry_mapping_builder import (
        IndustryMappingError,
        migrate_placeholder_rows,
        publish_candidate,
        rebuild_all,
        update_symbol,
        validate_sample,
    )
    from utils.config import Config
    from utils.symbols import normalize_symbol, validate_symbol

    config = Config()
    if not _check_disclaimer(config):
        return

    try:
        action = action_or_symbol or "rebuild"
        if action == "validate":
            result = validate_sample()
            console.print(
                f"[green]✓ 抽样校验通过：{result['stock_count']} 只股票，"
                f"有效分类率 {result['valid_classification_rate']}%[/green]"
            )
        elif action == "publish":
            result = publish_candidate()
            console.print(
                f"[green]✓ 候选映射已发布：{result['stock_count']} 只股票，"
                f"有效分类率 {result['valid_classification_rate']}%[/green]"
            )
        elif action == "migrate-placeholders":
            result = migrate_placeholder_rows()
            console.print(
                f"[green]✓ 已迁移 {result['migrated_count']} 条占位记录，"
                f"映射表共 {result['stock_count']} 条[/green]"
            )
        elif action != "rebuild":
            if not validate_symbol(action):
                console.print(f"[red]无效的股票代码或操作: {action}[/red]")
                sys.exit(1)
            symbol = normalize_symbol(action)
            result = update_symbol(symbol)
            console.print(
                f"[green]✓ {result['symbol']} → {result['sw_level1']}"
                f"/{result['sw_level2']}（{result['style_category']}）"
                f"[/green] [dim]({'更新' if result['action'] == 'updated' else '新增'})[/dim]"
            )
        else:
            from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                console=console,
                transient=True,
            ) as progress:
                task_id = progress.add_task("正在重建行业映射表", total=335)

                def on_progress(current, total, label):
                    progress.update(task_id, completed=current, total=total,
                                    description=f"[{current}/{total}] {label}")

                result = rebuild_all(on_progress=on_progress, resume=resume, delay=delay)
                progress.update(task_id, visible=False)

            coverage = result["coverage_pct"]
            console.print(
                f"[green]✓ 行业映射候选构建完成：{result['stock_count']} 只股票，"
                f"覆盖率 {coverage}%，有效分类率 {result['valid_classification_rate']}%[/green]"
            )
            console.print(f"[dim]候选文件：{result['candidate_path']}；确认后执行 industry-mapping publish[/dim]")
            if result["failed_industries"]:
                console.print(
                    f"[yellow]⚠ 失败行业 {len(result['failed_industries'])} 个: "
                    f"{', '.join(result['failed_industries'])}[/yellow]"
                )
            if verbose:
                console.print(f"[dim]遍历行业 {result['total_industries']} 个[/dim]")
    except IndustryMappingError as e:
        console.print(f"[red]✗ 行业映射操作失败: {e}[/red]")
        sys.exit(1)


@main.group()
def config():
    """管理配置"""


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key, value):
    """设置配置项"""
    from utils.config import Config
    cfg = Config()
    converted = _convert_value(value)
    cfg.set(key, converted)
    console.print(f"[green]✓ {key} = {converted}[/green]")


@config.command("get")
@click.argument("key")
def config_get(key):
    """获取配置项"""
    from utils.config import Config
    cfg = Config()
    val = cfg.get(key)
    console.print(f"{key} = {val}")


@main.group()
def cache():
    """管理缓存"""


@cache.command("clear")
@click.option("--data-type", type=click.Choice(
    ["price", "financial", "valuation", "industry", "news"]),
    help="仅清空指定数据类型的缓存")
def cache_clear(data_type):
    """清空全部或指定类型的缓存"""
    c = _get_cache()
    if data_type:
        count = c.invalidate_data_type(data_type)
        console.print(f"[green]✓ 已清空 {data_type} 缓存（{count} 条）[/green]")
    else:
        c.clear()
        console.print("[green]✓ 缓存已清空[/green]")


@cache.command("status")
def cache_status():
    """查看缓存状态"""
    c = _get_cache()
    stats = c.stats()
    table = Table(title="缓存状态")
    table.add_column("指标", style="cyan")
    table.add_column("数值", style="green")
    table.add_row("缓存条目", str(stats["total_entries"]))
    table.add_row("数据库大小", f"{stats['db_size_bytes'] / 1024:.1f} KB")
    console.print(table)


def _resolve_api_bind(host: str | None, port: int | None, config: Config) -> tuple[str, int]:
    """解析 API 监听地址：CLI 显式参数 > 配置文件 > 默认值"""
    resolved_host = host or config.get("api.host", "127.0.0.1")
    resolved_port = port if port is not None else config.get("api.port", 25618)
    return resolved_host, resolved_port


def _run_web_server(app, host: str, port: int) -> None:
    """运行 Web 服务。"""
    import uvicorn

    uvicorn.run(app, host=host, port=port)


@main.command("run")
@click.option("--host", default=None, help="监听地址（默认读配置 api.host，缺省 127.0.0.1）")
@click.option("--port", default=None, type=int, help="监听端口（默认读配置 api.port，缺省 25618）")
def run(host, port):
    """启动 Web API 服务（含 Web UI）"""
    from api.app import create_app
    from api.bootstrap import build_agent_core

    config = Config()
    bind_host, bind_port = _resolve_api_bind(host, port, config)
    with _create_cli_progress() as progress:
        task_id = progress.add_task("正在构建 Agent 核心", total=3)
        core = build_agent_core(config)
        progress.update(task_id, completed=1, description="正在创建 Web 应用")
        app = create_app(core=core)
        progress.update(task_id, completed=3, description="正在启动 HTTP 服务")

    logger.info("Stock Robot API 启动于 http://%s:%d", bind_host, bind_port)
    console.print(f"[green]Web 服务正在运行: http://{bind_host}:{bind_port}[/green]")
    _run_web_server(app, bind_host, bind_port)


@main.command()
@click.option("--ask", "-a", default=None, help="单次对话（非交互式）")
@click.option("--verbose", "-v", is_flag=True, help="显示计划和工具调用细节")
def chat(ask, verbose):
    """进入 AI Agent 对话模式，支持复杂投研任务的自主拆解和分析"""
    from agent.chat import ChatResponder
    from agent.executor import Executor
    from agent.graph import DEFAULT_CHECKPOINT_DIR
    from agent.memory import Memory
    from agent.planner import Planner
    from api.bootstrap import build_agent_core
    from output.renderer import RichRenderer
    from utils.config import Config

    config = Config()
    renderer = RichRenderer(console=console)
    core = build_agent_core(config)

    memory = Memory()
    planner = Planner(llm=core.llm, registry=core.registry, memory=memory)
    executor = Executor(registry=core.registry, memory=memory, model=core.model,
                        persist_dir=str(DEFAULT_CHECKPOINT_DIR))
    chat_responder = ChatResponder(model=core.model)

    if ask:
        _run_agent_query(ask, planner, executor, memory, renderer, chat_responder)
        return

    _run_interactive_chat(planner, executor, memory, renderer, chat_responder)


def _run_agent_query(query, planner, executor, memory, renderer, chat_responder):
    """单次 Agent 查询"""
    plan = planner.plan(query)
    if plan.mode == "agent":
        # CLI 暂不接入自主循环：映射为单步 plan（与 planner 降级路径一致）
        from agent.memory import Plan, TaskStep

        plan = Plan(goal=plan.goal,
                    steps=[TaskStep(id="step-1", description=plan.goal)])
    if plan.mode == "chat":
        import asyncio
        # 与 API 路径对称：先写 user 再回复，保证 memory 有完整 user/assistant 轮次
        memory.add_message("user", query)
        reply = asyncio.run(chat_responder.reply(query, memory))
        memory.add_message("assistant", reply)
        console.print(reply)
        return
    console.print(renderer.render_plan(plan))

    import asyncio
    result = asyncio.run(executor.execute(plan))

    console.print(renderer.render_summary(result))


def _run_interactive_chat(planner, executor, memory, renderer, chat_responder):
    """交互式对话循环"""
    console.print("[bold]Stock Robot Agent[/bold] — AI 驱动的投资研究助手")
    console.print("输入你的投研问题，或输入 /exit 退出。输入 /help 查看可用指令。\n")

    while True:
        try:
            user_input = click.prompt("你", prompt_suffix="> ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n再见！")
            break

        if not user_input:
            continue

        # 处理快捷指令
        result = _handle_slash_command(user_input, memory, renderer)
        if result == "exit":
            break
        if result:
            continue

        plan = planner.plan(user_input)
        if plan.mode == "agent":
            # CLI 暂不接入自主循环：映射为单步 plan（与 planner 降级路径一致）
            from agent.memory import Plan, TaskStep

            plan = Plan(goal=plan.goal,
                        steps=[TaskStep(id="step-1", description=plan.goal)])
        if plan.mode == "chat":
            import asyncio
            # 与 API 路径对称：先写 user 再回复，保证 memory 有完整 user/assistant 轮次
            memory.add_message("user", user_input)
            reply = asyncio.run(chat_responder.reply(user_input, memory))
            memory.add_message("assistant", reply)
            console.print(reply)
            continue

        console.print(renderer.render_plan(plan))

        import asyncio
        result = asyncio.run(executor.execute(plan))

        console.print(renderer.render_summary(result))


def _handle_slash_command(text, memory, renderer):
    """处理 / 开头的快捷指令，返回 'exit' 表示退出，True 表示已处理"""
    cmd = text.strip().lower()

    if cmd == "/exit":
        console.print("再见！")
        return "exit"

    if cmd == "/help":
        console.print("""
[bold]可用快捷指令:[/bold]
  /help     - 显示此帮助
  /tools    - 列出可用工具
  /plan     - 显示最近一次执行计划
  /clear    - 清空当前会话上下文
  /verbose  - 切换详细输出模式
  /exit     - 退出对话模式
        """.strip())
        return True

    if cmd == "/tools":
        console.print("[dim]工具列表功能需要在上下文中访问 registry，暂时不可用[/dim]")
        return True

    if cmd == "/plan":
        last = memory.get_last_plan()
        if last is None:
            console.print("[dim]暂无执行计划[/dim]")
        else:
            console.print(renderer.render_plan(last))
        return True

    if cmd == "/clear":
        memory.clear_session()
        console.print("[dim]会话上下文已清空[/dim]")
        return True

    if cmd == "/verbose":
        console.print("[dim]详细模式已切换[/dim]")
        return True

    return False


# ---------------------------------------------------------------------------
# RAG 知识库管理命令组
# ---------------------------------------------------------------------------

@main.group()
def rag():
    """知识库管理 — 文档摄入、清理、统计"""


@rag.command("ingest")
@click.argument("path")
@click.option("--source-type", "-s", required=True,
              type=click.Choice([
                  "research_reports", "financial_filings",
                  "policy_macro", "academic",
                  "history_reports", "system_rules",
              ]),
              help="知识库类型")
@click.option("--title", "-t", default="", help="文档标题")
@click.option("--date", "-d", default="", help="文档日期 (YYYY-MM-DD)")
@click.option("--symbol", multiple=True, help="关联股票代码（可多次指定）")
@click.option("--tag", multiple=True, help="内容标签（可多次指定）")
def rag_ingest(path, source_type, title, date, symbol, tag):
    """摄入文档或目录到知识库

    PATH 可以是单个文件或目录路径。
    """
    import os

    from rag.engine import RAGEngine

    console.print("[bold]正在摄入知识库...[/bold]")
    console.print(f"  类型: {source_type}")
    console.print(f"  路径: {path}")

    engine = RAGEngine()
    symbols = list(symbol)
    tags = list(tag)

    if os.path.isdir(path):
        console.print("  模式: 目录批量导入")
        results = engine.ingest_directory(
            directory=path,
            source_type=source_type,
            title_prefix=title,
            symbols=symbols,
            tags=tags,
        )
        succeeded = sum(1 for r in results if r.get("status") == "success")
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        failed = sum(1 for r in results if r.get("status") == "error")
        console.print(
            f"[green]✓ 成功: {succeeded}[/green]  "
            f"[yellow]跳过: {skipped}[/yellow]  "
            f"[red]失败: {failed}[/red]"
        )
        for r in results:
            if r.get("status") == "error":
                console.print(f"  [red]✗ {r.get('file_path', '?')}: {r.get('reason')}[/red]")
    else:
        result = engine.ingest_file(
            file_path=path,
            source_type=source_type,
            title=title,
            date=date,
            symbols=symbols,
            tags=tags,
        )
        if result["status"] == "success":
            console.print(
                f"[green]✓ 摄入成功: {result['chunks_count']} 个分块[/green]"
            )
            console.print(f"  哈希: {result.get('source_hash', '')[:16]}...")
        elif result["status"] == "skipped":
            console.print(f"[yellow]跳过: {result.get('reason', '?')}[/yellow]")
        else:
            console.print(f"[red]✗ 失败: {result.get('reason', '?')}[/red]")


@rag.command("clean")
@click.option("--source", "-s", "source_type",
              type=click.Choice([
                  "research_reports", "financial_filings",
                  "policy_macro", "academic",
                  "history_reports", "system_rules",
              ]),
              help="清除指定知识库类型")
@click.option("--before", "before_date", default="",
              help="清除指定日期前的文档 (YYYY-MM-DD)")
@click.option("--symbol", default="",
              help="清除指定股票关联的文档")
@click.option("--dry-run", is_flag=True, default=False,
              help="预览，不实际删除")
def rag_clean(source_type, before_date, symbol, dry_run):
    """清理知识库中的文档"""
    from rag.engine import RAGEngine

    engine = RAGEngine()

    if dry_run:
        console.print("[bold yellow]DRY RUN 模式 — 仅预览，不实际删除[/bold yellow]\n")

    if source_type:
        if dry_run:
            stats = engine.collection_stats()
            for s in stats:
                if s["name"] == source_type:
                    console.print(
                        f"将清除 [bold]{source_type}[/bold] 全部 "
                        f"[bold]{s['count']}[/bold] 个文档"
                    )
                    break
        else:
            result = engine.delete_by_source_type(source_type)
            console.print(f"[green]✓ 已清除 {source_type} 知识库[/green]")

    elif before_date:
        if dry_run:
            console.print(f"将清除 [bold]{before_date}[/bold] 之前的文档")
        else:
            result = engine.delete_before_date(before_date)
            console.print(
                f"[green]✓ 已清除 {result['deleted']} 个文档"
                f"（{before_date} 之前）[/green]"
            )

    elif symbol:
        if dry_run:
            console.print(f"将清除与股票 [bold]{symbol}[/bold] 关联的文档")
        else:
            result = engine.delete_by_symbol(symbol)
            console.print(
                f"[green]✓ 已清除 {result['deleted']} 个与 {symbol} 关联的文档[/green]"
            )

    else:
        console.print(
            "[yellow]请指定清除条件: --source / --before / --symbol[/yellow]"
        )


@rag.command("stats")
def rag_stats():
    """查看知识库各 Collection 统计信息"""
    from rag.engine import RAGEngine

    engine = RAGEngine()
    stats = engine.collection_stats()
    embedding_name = engine.embedding_name

    table = Table(title="RAG 知识库统计")
    table.add_column("Collection", style="cyan")
    table.add_column("文档数", justify="right")
    table.add_column("状态", style="green")

    total = 0
    for entry in stats:
        table.add_row(
            entry["name"],
            str(entry["count"]),
            "✓" if entry["count"] > 0 else "空",
        )
        total += entry["count"]

    table.add_section()
    table.add_row("[bold]合计[/bold]", f"[bold]{total}[/bold]", "")
    console.print(table)
    console.print(f"\n[dim]Embedding 模型: {embedding_name}[/dim]")


@main.group()
def subscribe():
    """管理每日定时推送订阅（邮件/企业微信）"""


@subscribe.command("add")
@click.option("--name", required=True, help="订阅名称")
@click.option("--symbols", required=True, help="标的代码，逗号/空格分隔")
@click.option("--channel", type=click.Choice(["email", "wecom"]), required=True,
              help="推送渠道")
@click.option("--time", "push_time", required=True, help="每日推送时间 HH:MM")
@click.option("--kind", type=click.Choice(["auto", "stock", "index"]),
              default="auto", help="标的类型（默认 auto 自动判定）")
@click.option("--index-style", type=click.Choice(["broad", "sector", "overseas"]),
              default=None, help="指数风格（kind=index 时使用）")
def subscribe_add(name, symbols, channel, push_time, kind, index_style):
    """创建订阅"""
    import re
    from datetime import datetime
    from typing import Any

    from push.models import Subscription
    from push.store import PushStore
    from utils.config import Config

    config = Config()
    store = PushStore(config.config_dir / "push.db")
    # 订阅级类型选项作为所有标的的默认值（API 支持 per-symbol 覆盖）；
    # pydantic before-validator 接受 dict 简写，标注 list[Any] 规避静态类型误报
    raw_symbols: list[Any] = [
        {"symbol": s, "kind": kind, "index_style": index_style}
        for s in re.split(r"[,，\s]+", symbols) if s
    ]
    sub = Subscription(
        name=name,
        symbols=raw_symbols,
        channel=channel,
        time=push_time,
        created_at=datetime.now().astimezone().isoformat(),
    )
    sub_id = store.create(sub)
    console.print(f"[green]已创建订阅 #{sub_id}: {name}（{channel} {push_time}）[/green]")


@subscribe.command("list")
def subscribe_list():
    """列出全部订阅"""
    from push.store import PushStore
    from utils.config import Config

    store = PushStore(Config().config_dir / "push.db")
    table = Table(title="推送订阅")
    table.add_column("ID", style="cyan")
    table.add_column("名称", style="white")
    table.add_column("标的", style="yellow")
    table.add_column("渠道", style="green")
    table.add_column("时间", style="magenta")
    table.add_column("状态", style="green")
    for sub in store.list():
        last = store.last_run(sub.id) if sub.id else None
        status = "启用" if sub.enabled else "停用"
        if last:
            status += f"（上次 {last['ok']}/{last['total']} 成功）"
        table.add_row(str(sub.id), sub.name,
                      "、".join(s.symbol for s in sub.symbols),
                      sub.channel, sub.time, status)
    console.print(table)


@subscribe.command("remove")
@click.option("--id", "sub_id", type=int, required=True, help="订阅 ID")
def subscribe_remove(sub_id):
    """删除订阅"""
    from push.store import PushStore
    from utils.config import Config

    store = PushStore(Config().config_dir / "push.db")
    if store.delete(sub_id):
        console.print(f"[green]已删除订阅 #{sub_id}[/green]")
    else:
        console.print(f"[red]订阅 #{sub_id} 不存在[/red]")


@subscribe.command("enable")
@click.option("--id", "sub_id", type=int, required=True, help="订阅 ID")
def subscribe_enable(sub_id):
    """启用订阅"""
    _set_enabled(sub_id, True)


@subscribe.command("disable")
@click.option("--id", "sub_id", type=int, required=True, help="订阅 ID")
def subscribe_disable(sub_id):
    """停用订阅"""
    _set_enabled(sub_id, False)


def _set_enabled(sub_id: int, enabled: bool):
    from push.store import PushStore
    from utils.config import Config

    store = PushStore(Config().config_dir / "push.db")
    sub = store.get(sub_id)
    if sub is None:
        console.print(f"[red]订阅 #{sub_id} 不存在[/red]")
        return
    sub.enabled = enabled
    store.update(sub)
    console.print(f"[green]订阅 #{sub_id} 已{'启用' if enabled else '停用'}[/green]")


@subscribe.command("run")
@click.option("--id", "sub_id", type=int, required=True, help="订阅 ID")
def subscribe_run(sub_id):
    """手动触发一次推送（同步执行，耗时取决于标的数）"""
    from push.executor import PushExecutor
    from push.store import PushStore
    from utils.config import Config

    config = Config()
    store = PushStore(config.config_dir / "push.db")
    sub = store.get(sub_id)
    if sub is None:
        console.print(f"[red]订阅 #{sub_id} 不存在[/red]")
        return
    from api.bootstrap import build_agent_core
    core = build_agent_core(config)
    executor = PushExecutor(core, store, config)
    with console.status("正在生成报告并推送..."):
        result = executor.run_subscription(sub)
    console.print(f"[green]推送完成: {result['ok']}/{result['total']} 成功[/green]")
    for failure in result["failures"]:
        console.print(f"[yellow]失败: {failure}[/yellow]")


if __name__ == "__main__":
    main()
