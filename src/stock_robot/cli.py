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


def _get_registry():
    """构建默认注册表"""
    from core.registry import Registry
    from data.akshare import AkShareAdapter
    from analysis.financial import FinancialAnalyzer
    from analysis.technical import TechnicalAnalyzer
    from analysis.valuation import ValuationAnalyzer
    from analysis.industry import IndustryAnalyzer
    from analysis.sentiment import SentimentAnalyzer

    reg = Registry()
    reg.register_data_source(AkShareAdapter())
    reg.register_analysis_module(FinancialAnalyzer())
    reg.register_analysis_module(TechnicalAnalyzer())
    reg.register_analysis_module(ValuationAnalyzer())
    reg.register_analysis_module(IndustryAnalyzer())
    reg.register_analysis_module(SentimentAnalyzer())
    return reg


def _register_llm(reg, config):
    """注册 LLM 后端"""
    from llm.openai import OpenAIAdapter
    from llm.claude import ClaudeAdapter

    provider = config.get("llm.provider", "openai")
    api_key = config.get("llm.api_key", "")
    base_url = config.get("llm.base_url", "") or None

    if provider == "openai":
        reg.register_llm_backend(
            OpenAIAdapter(api_key=api_key, model=config.get("llm.model", "gpt-4o"),
                          temperature=config.get("llm.temperature", 0.3),
                          max_tokens=config.get("llm.max_tokens", 2000),
                          base_url=base_url),
            provider="openai",
        )
    elif provider == "claude":
        reg.register_llm_backend(
            ClaudeAdapter(api_key=api_key, model=config.get("llm.model", "claude-sonnet-4-6"),
                          temperature=config.get("llm.temperature", 0.3),
                          max_tokens=config.get("llm.max_tokens", 2000),
                          base_url=base_url),
            provider="claude",
        )


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
    from utils.config import Config
    from data.cache import CacheManager
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
    pass


@main.command()
@click.argument("symbol")
@click.option("--dimension", "-d", help="指定分析维度 (financial/technical/valuation/industry/sentiment)")
@click.option("--refresh-cache", is_flag=True, help="强制刷新缓存")
@click.option("--no-llm", is_flag=True, help="仅输出数据，跳过 LLM 解读")
@click.option("--verbose", "-v", is_flag=True, help="显示采集和分析过程")
@click.option("--with-market", is_flag=True, help="在报告中嵌入大盘环境分析")
def analyze(symbol, dimension, refresh_cache, no_llm, verbose, with_market):
    """分析股票并生成研报"""
    from utils.symbols import normalize_symbol, validate_symbol, resolve_name
    from report.builder import ReportBuilder
    from report.formatter import ReportFormatter
    from utils.config import Config

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

    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
            transient=True,
        ) as progress:
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
    except Exception as e:
        console.print(f"[red]分析失败: {e}[/red]")
        sys.exit(1)

    # 计算综合打分
    dim_weights = {"financial": 0.30, "technical": 0.20,
                   "valuation": 0.25, "industry": 0.25}
    base_score = 0.0
    total_weight = 0.0
    results_map = {r.dimension: r for r in results}
    score_rows = []
    all_risk_flags = []
    sufficiency_label = {"ok": "充足", "partial": "部分可用", "unavailable": "数据不足"}
    dim_labels = {"financial": "财务健康", "technical": "技术趋势",
                  "valuation": "估值合理", "industry": "行业对比",
                  "sentiment": "舆情风险"}
    dim_weight_labels = {"financial": "30%", "technical": "20%",
                         "valuation": "25%", "industry": "25%",
                         "sentiment": "不计分"}

    for dim, weight in dim_weights.items():
        r = results_map.get(dim)
        if r and r.score is not None:
            base_score += r.score * weight
            total_weight += weight

    if total_weight > 0:
        base_score = round(base_score / total_weight, 1)

    # 计算风险扣分（每条风险标签扣 1 分，上限 10）
    risk_deduction = 0
    for r in results:
        risk_deduction += len(r.risk_flags)
    risk_deduction = min(risk_deduction, 10)
    final_score = max(0, base_score - risk_deduction)

    # 构建打分行
    for dim, label in dim_labels.items():
        r = results_map.get(dim)
        if r:
            score_rows.append({
                "label": label,
                "score": f"{r.score:.1f}" if r.score is not None else "N/A",
                "weight": dim_weight_labels[dim],
                "sufficiency": sufficiency_label.get(r.status, r.status),
                "detail": r.score_detail or "",
            })
        all_risk_flags.extend(r.risk_flags if r else [])

    # 基础信息
    price_data = ctx.price_data or []
    year_high = max(p.high for p in price_data) if price_data else None
    year_low = min(p.low for p in price_data) if price_data else None
    latest_price = price_data[-1].close if price_data else None
    if year_high and year_low and latest_price and (year_high - year_low) > 0:
        pct = (latest_price - year_low) / (year_high - year_low) * 100
        price_position = f"{pct:.0f}%"
    else:
        price_position = "暂无"

    # 大盘环境快照（可选，供报告中嵌入指数环境摘要）
    market_env = None
    if with_market:
        try:
            from index.pipeline import IndexPipeline
            index_pipeline = IndexPipeline()
            snapshot = index_pipeline.get_snapshot("000300")
            if snapshot:
                market_env = snapshot
        except Exception:
            pass

    builder = ReportBuilder()
    report = builder.build(
        symbol, name, results, commentary,
        no_llm=no_llm,
        industry=(ctx.industry_data.industry if ctx.industry_data else "未知"),
        year_high=f"{year_high:.2f}" if year_high else "暂无",
        year_low=f"{year_low:.2f}" if year_low else "暂无",
        price_position=price_position,
        score_rows=score_rows,
        base_score=base_score,
        risk_deduction=risk_deduction,
        final_score=final_score,
        risk_flags=all_risk_flags,
        market_env=market_env,
    )

    saved_path = ReportFormatter.save(report, symbol)
    console.print(ReportFormatter.to_rich_markdown(report))
    console.print(f"\n[dim]报告已保存至: {saved_path}[/dim]")


def _render_index_report(report) -> str:
    """将 IndexReport 渲染为终端可读的 Rich Markdown"""
    from datetime import datetime
    from jinja2 import Environment, FileSystemLoader
    from pathlib import Path
    from report.builder import _md_table

    template_dir = Path(__file__).parent.parent / "report" / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["md_table"] = _md_table
    template = env.get_template("index_report.jinja2")
    md = template.render(
        code=report.code,
        name=report.name,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
    from pathlib import Path
    from report.builder import _md_table

    template_dir = Path(__file__).parent.parent / "report" / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["md_table"] = _md_table
    template = env.get_template("index_report.jinja2")
    return template.render(
        code=report.code,
        name=report.name,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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


def _render_compare_table(compare) -> str:
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
    from utils.symbols import validate_index_symbol, normalize_index_symbol
    from utils.config import Config
    from data.index_mapping import IndexMapping
    from data.schemas import AnalysisTarget
    from index.pipeline import IndexPipeline

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

    from report.formatter import ReportFormatter

    if not compare_only:
        for report in result.reports:
            if output == "terminal":
                console.print(_render_index_report(report))
            elif output == "markdown":
                saved = ReportFormatter.save(
                    _render_index_report_md(report),
                    report.code
                )
                console.print(f"[green]报告已保存: {saved}[/green]")

    if result.compare is not None:
        console.print(_render_compare_table(result.compare))

    if result.errors:
        for err in result.errors:
            console.print(f"[yellow]警告: {err}[/yellow]")


@main.group()
def config():
    """管理配置"""
    pass


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
    pass


@cache.command("clear")
def cache_clear():
    """清空所有缓存"""
    c = _get_cache()
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


@main.command()
@click.option("--ask", "-a", default=None, help="单次对话（非交互式）")
@click.option("--verbose", "-v", is_flag=True, help="显示计划和工具调用细节")
def chat(ask, verbose):
    """进入 AI Agent 对话模式，支持复杂投研任务的自主拆解和分析"""
    from agent.tools import ToolRegistry
    from agent.memory import Memory
    from agent.planner import Planner
    from agent.executor import Executor
    from agent.pipeline_tools import (
        AnalyzeStockTool, AnalyzeIndexTool, GetSnapshotTool, ScreenStocksTool,
    )
    from output.renderer import RichRenderer
    from utils.config import Config

    config = Config()
    renderer = RichRenderer(console=console)

    # 构建工具注册表
    registry = ToolRegistry()
    registry.register(AnalyzeStockTool())
    registry.register(AnalyzeIndexTool())
    registry.register(GetSnapshotTool())
    registry.register(ScreenStocksTool())

    # 构建 LLM 后端
    llm = _get_llm_for_agent(config)

    memory = Memory()
    planner = Planner(llm=llm, registry=registry, memory=memory)
    executor = Executor(registry=registry, memory=memory)

    if ask:
        _run_agent_query(ask, planner, executor, memory, renderer)
        return

    _run_interactive_chat(planner, executor, memory, renderer)


def _run_agent_query(query, planner, executor, memory, renderer):
    """单次 Agent 查询"""
    plan = planner.plan(query)
    console.print(renderer.render_plan(plan))

    import asyncio
    result = asyncio.run(executor.execute(plan))

    console.print(renderer.render_summary(result))


def _run_interactive_chat(planner, executor, memory, renderer):
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


def _get_llm_for_agent(config):
    """为 Agent 创建 LLM 后端实例"""
    provider = config.get("llm.provider", "openai")
    api_key = config.get("llm.api_key", "")
    base_url = config.get("llm.base_url", "") or None

    try:
        if provider == "openai":
            from llm.openai import OpenAIAdapter
            return OpenAIAdapter(
                api_key=api_key,
                model=config.get("llm.model", "gpt-4o"),
                temperature=config.get("llm.temperature", 0.3),
                max_tokens=config.get("llm.max_tokens", 2000),
                base_url=base_url,
            )
        elif provider == "claude":
            from llm.claude import ClaudeAdapter
            return ClaudeAdapter(
                api_key=api_key,
                model=config.get("llm.model", "claude-sonnet-4-6"),
                temperature=config.get("llm.temperature", 0.3),
                max_tokens=config.get("llm.max_tokens", 2000),
                base_url=base_url,
            )
    except Exception as e:
        logger.warning(f"LLM 后端初始化失败: {e}")

    return None


if __name__ == "__main__":
    main()
