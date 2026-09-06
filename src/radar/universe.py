"""版本化标的池的加载与生效日选择。"""

from __future__ import annotations

from datetime import date
from itertools import pairwise
from pathlib import Path

import yaml
from pydantic import ValidationError

from radar.models import RadarUniverse


class UniverseConfigError(Exception):
    """标的池配置不合法。"""


class UniverseRepository:
    """从 YAML 目录加载并按日期选择标的池版本。"""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def load_all(self) -> list[RadarUniverse]:
        """加载全部池版本，并验证同一池 ID 的标的有效期不交叠。"""
        if not self.directory.exists():
            raise UniverseConfigError(f"标的池目录不存在: {self.directory}")
        universes = [self._load(path) for path in sorted(self.directory.glob("*.yaml"))]
        if not universes:
            raise UniverseConfigError(f"标的池目录没有 YAML 配置: {self.directory}")

        seen_versions: set[tuple[str, int]] = set()
        grouped: dict[str, list[RadarUniverse]] = {}
        for universe in universes:
            key = (universe.id, universe.version)
            if key in seen_versions:
                raise UniverseConfigError(f"标的池版本重复: {universe.id} v{universe.version}")
            seen_versions.add(key)
            grouped.setdefault(universe.id, []).append(universe)
        for versions in grouped.values():
            self._validate_no_overlap(versions)
        return universes

    def get(self, universe_id: str, version: int | None = None) -> RadarUniverse:
        """按 ID 获取池；给定版本时精确获取。"""
        matches = [item for item in self.load_all() if item.id == universe_id]
        if version is not None:
            matches = [item for item in matches if item.version == version]
        if len(matches) != 1:
            suffix = f" v{version}" if version is not None else ""
            raise UniverseConfigError(f"未找到唯一标的池: {universe_id}{suffix}")
        return matches[0]

    def active_on(self, universe_id: str, target_date: date) -> RadarUniverse:
        """返回在指定日期包含至少一个有效标的的唯一池版本。"""
        candidates = [
            item
            for item in self.load_all()
            if item.id == universe_id
            and any(
                instrument.effective_from <= target_date
                and (instrument.effective_until is None or target_date <= instrument.effective_until)
                for instrument in item.instruments
            )
        ]
        if len(candidates) != 1:
            raise UniverseConfigError(f"{target_date.isoformat()} 没有唯一生效的标的池: {universe_id}")
        return candidates[0]

    @staticmethod
    def _validate_no_overlap(versions: list[RadarUniverse]) -> None:
        """防止两个版本在同一日期同时承载同一代码。"""
        by_symbol: dict[str, list[tuple[date, date | None, int]]] = {}
        for universe in versions:
            for instrument in universe.instruments:
                by_symbol.setdefault(instrument.symbol, []).append(
                    (instrument.effective_from, instrument.effective_until, universe.version)
                )
        for symbol, periods in by_symbol.items():
            ordered = sorted(periods, key=lambda item: item[0])
            for (_, previous_end, previous_version), (current_start, _, current_version) in pairwise(ordered):
                if previous_end is None or current_start <= previous_end:
                    raise UniverseConfigError(
                        f"标的池 {symbol} 在 v{previous_version} 与 v{current_version} 的有效期重叠"
                    )

    @staticmethod
    def _load(path: Path) -> RadarUniverse:
        try:
            with path.open(encoding="utf-8") as file:
                raw = yaml.safe_load(file)
        except OSError as exc:
            raise UniverseConfigError(f"读取标的池失败: {path}") from exc
        except yaml.YAMLError as exc:
            raise UniverseConfigError(f"解析标的池失败: {path}") from exc
        if not isinstance(raw, dict):
            raise UniverseConfigError(f"标的池文件内容必须是映射: {path}")
        try:
            return RadarUniverse.model_validate(raw)
        except ValidationError as exc:
            raise UniverseConfigError(f"标的池配置无效: {path}") from exc
