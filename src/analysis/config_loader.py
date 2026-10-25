"""配置加载器 — 深度合并 base + override YAML，校验权重，带内存缓存"""
import logging
from copy import deepcopy
from pathlib import Path

import yaml

from data.schemas import AnalysisContext

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """配置加载/校验异常"""


class ConfigLoader:
    def __init__(self, config_dir: Path | None = None):
        if config_dir is None:
            config_dir = Path(__file__).parent / "config"
        self.config_dir = Path(config_dir)
        self.global_const = self._load_yaml(self.config_dir / "global_const.yaml")
        self._cache: dict[str, dict] = {}

    def load_for_context(self, ctx: AnalysisContext) -> dict:
        """计分层选择投资风格，不改写真实市场分类或同行资格。"""
        if ctx.market != "hk":
            return self.load(ctx.sw_industry)
        data = ctx.industry_data
        sector, industry = (data.industry, data.sector) if data else ("", "")
        if not sector or not industry:
            # 本地已校验 HSICS 快照覆盖非港股通；不为评分扩大同行池或联网。
            from data.hk_hsics_industry import HsicsIndustryError, load_hsics_members

            try:
                member = load_hsics_members().get(ctx.symbol)
            except (HsicsIndustryError, OSError, ValueError) as error:
                logger.warning("港股评分 HSICS 快照无效 %s：%s", ctx.symbol, type(error).__name__)
                member = None
            if member is not None and member.status == "mapped":
                sector, industry = member.sector, member.industry
        mapping = self._load_yaml(self.config_dir / "港股_计分模板_映射.yaml")
        representative = mapping.get("sectors", {}).get(sector) or mapping.get("industries", {}).get(industry)
        fallback = representative is None
        config = deepcopy(self.load(representative or "综合"))
        config["meta"].update(cache_key=f"hk::{industry}::{sector}",
            scoring_template_source="HSICS 投资风格映射" if not fallback else "通用规则（HSICS 未映射或综合企业）",
            scoring_template_fallback=fallback, hsics_sector=sector, hsics_industry=industry)
        if sector == "保险":
            config["financial"]["debt_ratio"]["enabled"] = False
            config["financial"]["gross_margin_stability"]["enabled"] = False
        return config

    def load(self, sw_industry: str) -> dict:
        """根据申万一级行业名返回合并后的完整打分配置。"""
        cache_key = f"industry::{sw_industry}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 1. 申万行业 → 大类
        mapping = self._load_yaml(self.config_dir / "申万_大类_映射.yaml")
        style = mapping.get(sw_industry, "高端制造")

        # 2. 加载 base 模板
        base = self._load_yaml(self.config_dir / "base" / f"{style}.yaml")
        if base.get("meta", {}).get("inherit", "") != "":
            raise ConfigError(f"Base 模板 inherit 必须为空，收到: {style}")

        # 3. 检查 override
        override_path = self.config_dir / "override" / f"{sw_industry}.yaml"
        if override_path.exists():
            override = self._load_yaml(override_path)
            expected_inherit = override.get("meta", {}).get("inherit", "")
            if expected_inherit != style:
                raise ConfigError(
                    f"覆写文件 inherit='{expected_inherit}' 与父类 '{style}' 不匹配"
                )
            merged = self._deep_merge(base, override)
        else:
            merged = base

        # 4. 权重校验
        self._validate_weights(merged)

        # 5. 未配置 strategy_key 则默认 GeneralScorer
        if not merged.get("meta", {}).get("strategy_key", ""):
            merged["meta"]["strategy_key"] = "GeneralScorer"

        # 6. 注入 cache_key
        merged["meta"]["cache_key"] = cache_key

        self._cache[cache_key] = merged
        return merged

    def _deep_merge(self, base: dict, override: dict) -> dict:
        """深度合并：字典递归覆写，列表直接替换，标量覆写优先。"""
        result = {}
        for key in set(base.keys()) | set(override.keys()):
            if key in override and key in base:
                if isinstance(base[key], dict) and isinstance(override[key], dict):
                    result[key] = self._deep_merge(base[key], override[key])
                else:
                    result[key] = override[key]
            elif key in override:
                result[key] = override[key]
            else:
                result[key] = base[key]
        return result

    def _validate_weights(self, config: dict):
        """验证五维度权重总和 = 1.0"""
        dims = ["financial", "valuation", "industry", "technical", "sentiment"]
        total = sum(config.get(d, {}).get("weight", 0) for d in dims)
        if abs(total - 1.0) > 0.001:
            raise ConfigError(
                f"权重总和应为 1.0，实际 {total}。检查各维度 weight 字段。"
            )

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
