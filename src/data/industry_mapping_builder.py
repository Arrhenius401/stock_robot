"""行业映射表构建器 — legulegu 申万口径全量重建 + 单只更新

数据源为乐咕乐股（legulegu.com）申万 2021 版行业分类，口径与
src/analysis/config/申万_大类_映射.yaml 的申万一级命名对齐。
"""
import logging
import re
import time
from pathlib import Path

import requests
import yaml

logger = logging.getLogger(__name__)

OVERVIEW_URL = "https://legulegu.com/stockdata/sw-industry-overview/"
COMPOSITION_URL = "https://legulegu.com/stockdata/index-composition"
STOCK_URL = "https://legulegu.com/s/{symbol}"
DEFAULT_DELAY = 0.3

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
            time.sleep(0.5 * (2 ** attempt))
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
