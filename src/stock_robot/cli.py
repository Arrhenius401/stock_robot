"""Stock Robot CLI — AI 驱动的股票分析研报助手"""
import os
os.environ["TQDM_DISABLE"] = "1"

import sys
from pathlib import Path
import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


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
def analyze(symbol, dimension, refresh_cache, no_llm, verbose):
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

    builder = ReportBuilder()
    report = builder.build(symbol, name, results, commentary)

    saved_path = ReportFormatter.save(report, symbol)
    console.print(ReportFormatter.to_rich_markdown(report))
    console.print(f"\n[dim]报告已保存至: {saved_path}[/dim]")


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


if __name__ == "__main__":
    main()
