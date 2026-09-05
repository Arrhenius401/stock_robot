"""行业映射表构建器 — legulegu 申万口径全量重建 + 单只更新

数据源为乐咕乐股（legulegu.com）申万 2021 版行业分类，口径与
src/analysis/config/申万_大类_映射.yaml 的申万一级命名对齐。
"""
import csv
import json
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
MIN_VALID_CLASSIFICATION_RATE = 0.95
MIN_COVERAGE_RATE = 0.95
CHECKPOINT_VERSION = 1

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


def _candidate_path() -> Path:
    """返回全量重建的候选文件路径，不直接覆盖正式映射表。"""
    return _csv_path().with_name("industry_mapping.candidate.csv")


def _checkpoint_path() -> Path:
    """返回候选构建的断点状态文件路径。"""
    return _csv_path().with_name("industry_mapping.candidate.state.json")


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


def _write_rows(path: Path, rows: list[dict]) -> None:
    """原子写 CSV：先写临时文件再 rename，中途失败不损坏目标文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MAPPING_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def _write_csv(rows: list[dict]) -> None:
    """原子写正式行业映射表。"""
    _write_rows(_csv_path(), rows)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_checkpoint(completed: set[str], failed: set[str], expected_codes: set[str],
                      level2_map: dict[str, str]) -> None:
    """原子保存已完成和失败行业，支持中断后续跑。"""
    path = _checkpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": CHECKPOINT_VERSION, "completed": sorted(completed),
                   "failed": sorted(failed), "expected_codes": sorted(expected_codes),
                   "level2_map": level2_map}, f,
                  ensure_ascii=False)
    os.replace(tmp, path)


def _read_checkpoint() -> dict[str, Any] | None:
    path = _checkpoint_path()
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict) or state.get("version") != CHECKPOINT_VERSION:
            raise IndustryMappingError("候选构建状态文件版本无效")
        if not all(isinstance(state.get(key), list) for key in ("completed", "failed", "expected_codes")):
            raise IndustryMappingError("候选构建状态文件字段无效")
        if not isinstance(state.get("level2_map"), dict):
            raise IndustryMappingError("候选构建状态文件缺少行业树快照")
        return state
    except (OSError, ValueError, TypeError) as e:
        raise IndustryMappingError(f"候选构建状态文件无法读取: {e}") from e


def validate_rows(rows: list[dict[str, str]], known_total: int | None = None,
                  level2_map: dict[str, str] | None = None) -> dict:
    """校验候选映射质量，返回统计；不满足发布门槛时抛出异常。"""
    symbols = [row.get("symbol", "") for row in rows]
    duplicate_count = len(symbols) - len(set(symbols))
    placeholder_count = sum(
        1 for row in rows if row.get("sw_level1", "") in ("", "综合", "未知"))
    invalid_symbol_count = sum(1 for symbol in symbols if not re.fullmatch(r"\d{6}", symbol))
    invalid_hierarchy_count = 0
    if level2_map is not None:
        invalid_hierarchy_count = sum(
            1 for row in rows
            if level2_map.get(row.get("sw_level2", "")) != row.get("sw_level1", ""))
    valid_count = len(rows) - placeholder_count
    valid_rate = valid_count / len(rows) if rows else 0.0
    coverage_pct = round(len(rows) / known_total * 100, 1) if known_total else 100.0
    result = {
        "stock_count": len(rows),
        "duplicate_count": duplicate_count,
        "placeholder_count": placeholder_count,
        "invalid_symbol_count": invalid_symbol_count,
        "invalid_hierarchy_count": invalid_hierarchy_count,
        "valid_classification_rate": round(valid_rate * 100, 1),
        "coverage_pct": coverage_pct,
    }
    if not rows:
        raise IndustryMappingError("候选映射为空，拒绝发布")
    if duplicate_count:
        raise IndustryMappingError(f"候选映射存在 {duplicate_count} 个重复股票代码，拒绝发布")
    if invalid_symbol_count:
        raise IndustryMappingError(f"候选映射存在 {invalid_symbol_count} 个无效股票代码，拒绝发布")
    if valid_rate < MIN_VALID_CLASSIFICATION_RATE:
        raise IndustryMappingError(
            f"候选映射有效分类率仅 {valid_rate:.1%}，低于 {MIN_VALID_CLASSIFICATION_RATE:.0%} 门槛")
    if invalid_hierarchy_count:
        raise IndustryMappingError(f"候选映射存在 {invalid_hierarchy_count} 个非法行业层级，拒绝发布")
    if known_total and len(rows) / known_total < MIN_COVERAGE_RATE:
        raise IndustryMappingError(
            f"候选映射覆盖率仅 {len(rows) / known_total:.1%}，低于 {MIN_COVERAGE_RATE:.0%} 门槛")
    return result


def validate_sample(sample_size: int = 5, delay: float = DEFAULT_DELAY) -> dict:
    """抽样验证当前站点结构和行业层级解析，不写入候选或正式映射表。"""
    if sample_size <= 0:
        raise IndustryMappingError("抽样数量必须大于 0")
    level2_map, level3_map = fetch_taxonomy(refresh=True)
    samples = list(level3_map.items())[:sample_size]
    rows: list[dict[str, str]] = []
    failed: list[str] = []
    fallback_count = 0
    for code, (_, container_level2) in samples:
        try:
            stocks = fetch_constituents(code)
        except IndustryMappingError:
            failed.append(code)
            continue
        for stock in stocks:
            reported_level2 = stock.get("level2")
            row_level2 = reported_level2 if reported_level2 in level2_map else container_level2
            fallback_count += int(row_level2 != reported_level2)
            rows.append({"symbol": stock["symbol"],
                         "sw_level1": level2_map.get(row_level2, "综合"),
                         "sw_level2": row_level2,
                         "style_category": ""})
        if delay:
            time.sleep(delay)
    if failed:
        raise IndustryMappingError(f"抽样校验有 {len(failed)} 个行业抓取失败")
    validation = validate_rows(rows, level2_map=level2_map)
    return {**validation, "sampled_industries": len(samples),
            "failed_industries": failed, "level2_fallback_count": fallback_count}


def rebuild_all(delay: float = DEFAULT_DELAY, on_progress=None, resume: bool = False) -> dict:
    """构建并校验候选映射表；调用 publish_candidate 后才会替换正式表。"""
    level2_map, level3_map = fetch_taxonomy(refresh=True)
    style_map = _load_style_mapping()

    candidate = _candidate_path()
    expected_codes = set(level3_map)
    if resume:
        state = _read_checkpoint()
        if state is None or not candidate.exists():
            raise IndustryMappingError("--resume 需要配套的候选文件和状态文件")
        if set(state["expected_codes"]) != expected_codes or state["level2_map"] != level2_map:
            raise IndustryMappingError("候选状态与当前行业树不一致，请重新开始构建")
        completed = set(state["completed"])
        failed = set(state["failed"])
        if not completed.issubset(expected_codes) or not failed.issubset(expected_codes):
            raise IndustryMappingError("候选状态包含未知行业代码")
        stock_rows = {row["symbol"]: row for row in _read_rows(candidate)}
    else:
        completed, failed, stock_rows = set(), set(), {}
        for path in (candidate, _checkpoint_path()):
            if path.exists():
                path.unlink()

    total = len(level3_map)
    fallback_count = 0
    current_delay = delay
    for i, (code, (name, container_level2)) in enumerate(level3_map.items(), 1):
        if code in completed:
            if on_progress:
                on_progress(i, total, name)
            continue
        try:
            stocks = fetch_constituents(code)
        except IndustryMappingError as e:
            logger.warning("行业 %s(%s) 抓取失败: %s", name, code, e)
            failed.add(code)
            # 上游限流或超时时指数降速；单行业重试已由 _get_with_retry 限定。
            current_delay = min(max(delay, current_delay * 2), 10.0)
        else:
            for s in stocks:
                # 行内二级名须能在行业树验证；否则用三级容器的父级，禁止写占位“综合”。
                reported_level2 = s.get("level2")
                row_level2 = reported_level2 if reported_level2 in level2_map else container_level2
                fallback_count += int(row_level2 != reported_level2)
                row_level1 = level2_map.get(row_level2, "综合")
                if s["symbol"] not in stock_rows:
                    stock_rows[s["symbol"]] = {
                        "symbol": s["symbol"],
                        "sw_level1": row_level1,
                        "sw_level2": row_level2,
                        "style_category": style_map.get(row_level1, "高端制造"),
                    }
            completed.add(code)
            failed.discard(code)
            # 连续成功时缓慢回落到用户指定的基础间隔。
            current_delay = max(delay, current_delay * 0.8)
        _write_rows(candidate, list(stock_rows.values()))
        _write_checkpoint(completed, failed, expected_codes, level2_map)
        if on_progress:
            on_progress(i, total, name)
        if current_delay:
            time.sleep(current_delay)

    rows = list(stock_rows.values())
    known_total = len(_read_rows(_csv_path()))
    validation = validate_rows(rows, known_total=known_total, level2_map=level2_map)
    result = {
        "total_industries": total,
        "failed_industries": sorted(failed),
        "level2_fallback_count": fallback_count,
        "candidate_path": str(candidate),
        **validation,
    }
    logger.info("行业映射候选构建完成: %d 只股票，有效分类率 %.1f%%，失败行业 %d",
                result["stock_count"], result["valid_classification_rate"], len(failed))
    return result


def publish_candidate() -> dict:
    """通过质量门槛后，将候选映射原子发布为正式映射表。"""
    candidate = _candidate_path()
    rows = _read_rows(candidate)
    state = _read_checkpoint()
    if state is None:
        raise IndustryMappingError("候选构建状态文件不存在，拒绝发布")
    completed = set(state["completed"])
    expected_codes = set(state["expected_codes"])
    failed = set(state["failed"])
    if not expected_codes or completed != expected_codes:
        raise IndustryMappingError("候选构建尚未完成全部行业，拒绝发布")
    if failed:
        raise IndustryMappingError(
            f"候选构建仍有 {len(failed)} 个失败行业，完成续跑后才能发布")
    validation = validate_rows(rows, known_total=len(_read_rows(_csv_path())),
                               level2_map=state["level2_map"])
    _write_csv(rows)
    return {**validation, "published_path": str(_csv_path())}


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


def backfill_symbol(symbol: str, industry: str) -> bool:
    """在线回填：把采集层观测到的行业名写入映射表占位行

    仅覆盖"综合"/空占位行或追加不存在行（新股）；已有申万分类的行不动，
    防止东财口径污染 legulegu 官方口径。返回是否发生更新。
    """
    if not industry or industry == "未知":
        return False
    style_map = _load_style_mapping()
    style = style_map.get(industry, "高端制造")

    path = _csv_path()
    rows: list[dict] = []
    updated = False
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["symbol"] == symbol and row["sw_level1"] in ("", "综合"):
                    row["sw_level1"], row["style_category"] = industry, style
                    updated = True
                rows.append(row)
    if not updated and not any(r["symbol"] == symbol for r in rows):
        rows.append({"symbol": symbol, "sw_level1": industry,
                     "sw_level2": "", "style_category": style})
        updated = True
    if updated:
        _write_csv(rows)
        logger.info("行业映射在线回填 %s → %s", symbol, industry)
    return updated
