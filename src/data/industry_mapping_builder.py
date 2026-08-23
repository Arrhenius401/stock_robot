"""行业映射表构建器 — legulegu 申万口径全量重建 + 单只更新

数据源为乐咕乐股（legulegu.com）申万 2021 版行业分类，口径与
src/analysis/config/申万_大类_映射.yaml 的申万一级命名对齐。
"""
import csv
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import requests
import yaml

logger = logging.getLogger(__name__)

OVERVIEW_URL = "https://legulegu.com/stockdata/sw-industry-overview/"
COMPOSITION_URL = "https://legulegu.com/stockdata/index-composition"
STOCK_URL = "https://legulegu.com/s/{symbol}"
DEFAULT_DELAY = 1.5  # 限流实测：0.3s 连续 335 请求触发 429/504 封禁

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://legulegu.com/",
}

MAPPING_COLUMNS = ["symbol", "sw_level1", "sw_level2", "style_category"]

# 行业树日级稳定，模块级缓存避免每次分析重复抓取 1MB 页面
_TAXONOMY_CACHE: tuple | None = None

# 结构退化的最低门槛：容器数 / 二级数 / 三级数低于阈值即中止（页面改版保护）
MIN_TAXONOMY_COUNT = 400
MIN_LEVEL2_COUNT = 100
MIN_LEVEL3_COUNT = 300


class IndustryMappingError(Exception):
    """行业映射表构建/更新异常"""


def _csv_path() -> Path:
    return Path(__file__).parent.parent.parent / "data" / "industry_mapping.csv"


def _style_mapping_path() -> Path:
    return Path(__file__).parent.parent / "analysis" / "config" / "申万_大类_映射.yaml"


def _load_style_mapping() -> dict[str, str]:
    with open(_style_mapping_path(), "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_with_retry(url: str, retries: int = 3) -> str:
    """GET 带指数退避重试（网络/5xx 边界），返回响应文本"""
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            return resp.text
        except (requests.RequestException, OSError) as e:
            if attempt == retries - 1:
                raise IndustryMappingError(f"请求失败: {url}: {e}") from e
            time.sleep(2 ** (attempt + 1))  # 2s/4s 退避，缓解站点限流
    raise IndustryMappingError(f"请求失败: {url}")


def _to_float(raw: str) -> float | None:
    """表格单元格数值解析，空/非数字 → None"""
    if not raw or raw in ("nan", "None", "-"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def fetch_taxonomy(refresh: bool = False) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """抓取申万行业树 → (二级名→一级名, 三级码→(三级名, 二级名))

    overview 页平铺 497 个行业容器（31 一级无 parent span + 131 二级 + 335 三级）。
    解析失败（容器数或分层数异常）立即抛 IndustryMappingError——结构变了全量数据不可信。
    """
    global _TAXONOMY_CACHE
    if not refresh and _TAXONOMY_CACHE is not None:
        return _TAXONOMY_CACHE

    html = _get_with_retry(OVERVIEW_URL)
    pattern = re.compile(
        r'<div id="(\d{6}\.SI)" class="lg-industries-item[^"]*">(.*?)(?=<div id="\d{6}\.SI"|$)',
        re.DOTALL,
    )
    records: list[tuple[str, str, str | None]] = []
    for m in pattern.finditer(html):
        code, body = m.group(1), m.group(2)
        nm = re.search(r'lg-industries-item-number">([^<]+?)(?:<span|</div>)', body)
        pm = re.search(r'parent-industry-name">\[([^\]]+)\]', body)
        if not nm:
            continue
        name = re.sub(r"\((\d+)\)$", "", nm.group(1)).strip()
        records.append((code, name, pm.group(1).strip() if pm else None))

    if len(records) < MIN_TAXONOMY_COUNT:
        raise IndustryMappingError(
            f"行业树解析异常：仅解析到 {len(records)} 个行业（预期 497），页面结构可能已变更"
        )

    level1_names = {name for _, name, parent in records if parent is None}
    level2_map: dict[str, str] = {}
    level3_map: dict[str, tuple[str, str]] = {}
    for code, name, parent in records:
        if parent is None:
            continue
        if parent in level1_names:
            level2_map[name] = parent
        else:
            level3_map[code] = (name, parent)

    if len(level2_map) < MIN_LEVEL2_COUNT or len(level3_map) < MIN_LEVEL3_COUNT:
        raise IndustryMappingError(
            f"行业树分层异常：二级 {len(level2_map)} 个、三级 {len(level3_map)} 个"
        )

    result = (level2_map, level3_map)
    _TAXONOMY_CACHE = result
    return result


def _parse_composition_table(html: str) -> list[dict[str, Any]]:
    """解析成分股表 → [{symbol, name, level2, pe_ttm, pb, market_cap}]

    按列位置取值（列名被 JSON-LD 注入污染，不可依赖）。跳过表头行与 ST/退市股。
    level2 为该股票在表中标注的申万2级（可能与遍历容器不一致，由调用方交叉校验）。
    """
    results: list[dict[str, Any]] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
        if len(cells) < 13:
            continue
        plain = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        code_m = re.search(r"\d{6}", plain[1])
        name = plain[2]
        if not code_m or not name or any(tag in name for tag in ("ST", "退市", "PT")):
            continue
        results.append({
            "symbol": code_m.group(0),
            "name": name,
            "level2": plain[4] or None,
            "pe_ttm": _to_float(plain[8]),
            "pb": _to_float(plain[9]),
            "market_cap": _to_float(plain[12]),
        })
    return results


def fetch_constituents(code: str) -> list[dict[str, Any]]:
    """拉取单个申万行业指数成分股（含 PE/PB/市值）"""
    html = _get_with_retry(f"{COMPOSITION_URL}?industryCode={code}")
    return _parse_composition_table(html)


def _write_csv(rows: list[dict]) -> None:
    """原子写 CSV：先写临时文件再 rename，中途失败不损坏现有表"""
    path = _csv_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MAPPING_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def rebuild_all(delay: float = DEFAULT_DELAY,
                on_progress=None) -> dict:
    """全量重建行业映射表，返回统计 {total_industries, failed_industries,
    stock_count, coverage_pct}。覆盖率分母取旧 CSV 行数（全 A 股近似）。"""
    level2_map, level3_map = fetch_taxonomy(refresh=True)
    style_map = _load_style_mapping()

    stock_rows: dict[str, dict] = {}
    failed: list[str] = []
    total = len(level3_map)
    for i, (code, (name, container_level2)) in enumerate(level3_map.items(), 1):
        try:
            stocks = fetch_constituents(code)
        except IndustryMappingError as e:
            logger.warning("行业 %s(%s) 抓取失败: %s", name, code, e)
            failed.append(code)
        else:
            for s in stocks:
                # 行内"申万2级"列优先，缺失用容器 parent 兜底；一级随之推导
                row_level2 = s.get("level2") or container_level2
                row_level1 = level2_map.get(row_level2, "综合")
                if s["symbol"] not in stock_rows:
                    stock_rows[s["symbol"]] = {
                        "symbol": s["symbol"],
                        "sw_level1": row_level1,
                        "sw_level2": row_level2,
                        "style_category": style_map.get(row_level1, "高端制造"),
                    }
        if on_progress:
            on_progress(i, total, name)
        if delay:
            time.sleep(delay)

    # 覆盖率分母 = 旧 CSV 行数（全 A 股近似），须在 _write_csv 覆盖前读取
    known_total: int | None = None
    old_path = _csv_path()
    if old_path.exists():
        with open(old_path, encoding="utf-8") as f:
            known_total = sum(1 for _ in f) - 1

    rows = list(stock_rows.values())
    _write_csv(rows)

    denominator = known_total or len(rows)
    coverage_pct = round(len(rows) / denominator * 100, 1) if denominator else 100.0

    logger.info("行业映射表重建完成: %d 只股票, 覆盖率 %.1f%%, 失败行业 %d",
                len(rows), coverage_pct, len(failed))
    return {
        "total_industries": total,
        "failed_industries": failed,
        "stock_count": len(rows),
        "coverage_pct": coverage_pct,
    }


def _parse_stock_industry(html: str) -> tuple[str, str]:
    """解析个股页行业区块 → (一级名, 二级名)；无区块或层级不全抛 IndustryMappingError"""
    m = re.search(r'<span class="industry">(.*?)</span>', html, re.DOTALL)
    if not m:
        raise IndustryMappingError("个股页无行业区块，可能未分类")
    levels: dict[int, str] = {}
    for link in re.findall(r'<a class="industry-name"[^>]*>(.*?)</a>', m.group(1)):
        mm = re.match(r"^(I{1,3})(.+)$", link.strip())
        if mm:
            levels[len(mm.group(1))] = mm.group(2).strip()
    if 1 not in levels or 2 not in levels:
        raise IndustryMappingError("个股页行业区块不完整，缺一级/二级行业")
    return levels[1], levels[2]


def update_symbol(symbol: str) -> dict:
    """单只更新行业分类（个股页秒级反查），返回 {symbol, sw_level1, sw_level2,
    style_category, action}。symbol 不在表中则追加。"""
    html = _get_with_retry(STOCK_URL.format(symbol=symbol))
    level1, level2 = _parse_stock_industry(html)
    style_map = _load_style_mapping()
    style = style_map.get(level1, "高端制造")

    path = _csv_path()
    rows: list[dict] = []
    updated = False
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["symbol"] == symbol:
                    row["sw_level1"], row["sw_level2"], row["style_category"] = level1, level2, style
                    updated = True
                rows.append(row)
    if not updated:
        rows.append({"symbol": symbol, "sw_level1": level1,
                     "sw_level2": level2, "style_category": style})
    _write_csv(rows)

    logger.info("行业映射单只更新 %s: %s/%s (%s)", symbol, level1, level2,
                "更新" if updated else "新增")
    return {"symbol": symbol, "sw_level1": level1, "sw_level2": level2,
            "style_category": style, "action": "updated" if updated else "inserted"}
