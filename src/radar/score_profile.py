"""配置雷达评分依据的版本化加载。"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from radar.models import RadarScoreProfile


class ScoreProfileConfigError(Exception):
    """评分依据配置不合法。"""


class ScoreProfileRepository:
    """从 YAML 加载评分依据，避免将策略规则分散在代码常量中。"""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def get(self, profile_id: str) -> RadarScoreProfile:
        """按 ID 读取唯一的评分依据。"""
        try:
            with (self.directory / f"{profile_id}.yaml").open(encoding="utf-8") as file:
                raw = yaml.safe_load(file)
        except OSError as exc:
            raise ScoreProfileConfigError(f"读取评分依据失败: {profile_id}") from exc
        except yaml.YAMLError as exc:
            raise ScoreProfileConfigError(f"解析评分依据失败: {profile_id}") from exc
        if not isinstance(raw, dict):
            raise ScoreProfileConfigError(f"评分依据内容必须是映射: {profile_id}")
        try:
            profile = RadarScoreProfile.model_validate(raw)
        except ValidationError as exc:
            raise ScoreProfileConfigError(f"评分依据配置无效: {profile_id}") from exc
        if profile.id != profile_id:
            raise ScoreProfileConfigError(f"评分依据文件名与 ID 不一致: {profile_id}")
        return profile
