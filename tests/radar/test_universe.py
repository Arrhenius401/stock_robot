"""标的池配置契约测试。"""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from radar.models import RadarUniverse
from radar.universe import UniverseConfigError, UniverseRepository


def _instrument(symbol: str = "510300", **updates: object) -> dict[str, object]:
    data: dict[str, object] = {
        "symbol": symbol,
        "name": "测试ETF",
        "asset_type": "etf",
        "category": "broad",
        "market": "SSE",
        "exposure_region": "CN",
        "effective_from": "2020-01-01",
        "min_avg_amount": 1_000_000,
    }
    data.update(updates)
    return data


def _universe(**updates: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": "test_etf",
        "name": "测试 ETF 池",
        "version": 1,
        "asset_type": "etf",
        "score_profile": "core_rotation_v1",
        "description": "测试用标的池",
        "instruments": [_instrument()],
    }
    data.update(updates)
    return data


def test_universe_rejects_duplicate_and_leveraged_etf():
    with pytest.raises(ValueError, match="重复"):
        RadarUniverse.model_validate(_universe(instruments=[_instrument(), _instrument()]))
    with pytest.raises(ValueError, match="杠杆"):
        RadarUniverse.model_validate(_universe(instruments=[_instrument(leveraged=True)]))


def test_universe_rejects_invalid_date_and_mismatched_asset_type():
    with pytest.raises(ValueError, match="effective_until"):
        RadarUniverse.model_validate(
            _universe(instruments=[_instrument(effective_until="2019-12-31")])
        )
    with pytest.raises(ValueError, match="asset_type"):
        RadarUniverse.model_validate(_universe(instruments=[_instrument(asset_type="stock")]))


def test_repository_selects_version_active_on_date():
    first = _universe(instruments=[_instrument(effective_until="2021-12-31")])
    second = _universe(
        version=2,
        instruments=[_instrument(effective_from="2022-01-01")],
    )
    import yaml

    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        path = Path(tmp_dir)
        (path / "one.yaml").write_text(yaml.safe_dump(first, allow_unicode=True), encoding="utf-8")
        (path / "two.yaml").write_text(yaml.safe_dump(second, allow_unicode=True), encoding="utf-8")
        repo = UniverseRepository(path)

        assert repo.active_on("test_etf", date(2021, 12, 31)).version == 1
        assert repo.active_on("test_etf", date(2022, 1, 1)).version == 2


def test_repository_rejects_overlapping_symbol_periods():
    import yaml

    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        path = Path(tmp_dir)
        (path / "one.yaml").write_text(
            yaml.safe_dump(_universe(), allow_unicode=True), encoding="utf-8"
        )
        (path / "two.yaml").write_text(
            yaml.safe_dump(_universe(version=2), allow_unicode=True), encoding="utf-8"
        )
        with pytest.raises(UniverseConfigError, match="重叠"):
            UniverseRepository(path).load_all()


def test_initial_universes_are_separate_and_loadable():
    repo = UniverseRepository(Path("config/radar_universes"))
    cn_hk = repo.get("cn_hk_etf")
    overseas = repo.get("overseas_etf")

    assert {item.exposure_region for item in cn_hk.instruments} == {"CN", "HK"}
    assert not {item.exposure_region for item in overseas.instruments} & {"CN", "HK"}
    assert cn_hk.asset_type == overseas.asset_type == "etf"


def test_enabled_selection_filters_members_and_versions(tmp_path):
    import yaml

    (tmp_path / "one.yaml").write_text(yaml.safe_dump(_universe(instruments=[
        _instrument(), _instrument("510500", effective_from="2027-01-01")
    ])), encoding="utf-8")
    (tmp_path / "disabled.yaml").write_text(yaml.safe_dump(_universe(id="disabled", enabled=False)), encoding="utf-8")
    selected = UniverseRepository(tmp_path).enabled_on(date(2026, 9, 30))
    assert len(selected) == 1
    assert selected[0].enabled is True
    assert [item.symbol for item in selected[0].instruments] == ["510300"]
    assert selected[0].fingerprint


def test_enabled_selection_rejects_distinct_overlapping_versions(tmp_path):
    import yaml

    (tmp_path / "one.yaml").write_text(yaml.safe_dump(_universe()), encoding="utf-8")
    (tmp_path / "two.yaml").write_text(yaml.safe_dump(_universe(version=2, instruments=[_instrument("510500")])), encoding="utf-8")
    with pytest.raises(UniverseConfigError, match="唯一"):
        UniverseRepository(tmp_path).enabled_on(date(2026, 9, 30))
