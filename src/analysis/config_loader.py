"""配置加载器 — 深度合并 base + override YAML，校验权重，带内存缓存"""
from pathlib import Path

import yaml


class ConfigError(Exception):
    """配置加载/校验异常"""


class ConfigLoader:
    def __init__(self, config_dir: Path | None = None):
        if config_dir is None:
            config_dir = Path(__file__).parent / "config"
        self.config_dir = Path(config_dir)
        self.global_const = self._load_yaml(self.config_dir / "global_const.yaml")
        self._cache: dict[str, dict] = {}

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
