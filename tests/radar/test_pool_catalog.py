"""独立股票池与统一目录的配置边界。"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from radar.models import RadarUniverse
from radar.pool_catalog import PoolCatalog, PoolCatalogError
from radar.stocks.models import StockPool
from radar.stocks.pools import StockPoolConfigError, StockPoolRepository
from radar.universe import UniverseRepository

ROOT = Path(__file__).parents[2]


def write_pool(directory, **changes):
    raw = {"id": "extra_pool", "name": "新增池", "asset_type": "stock",
           "description": "测试用途", "display_order": 30, **changes}
    directory.mkdir(exist_ok=True)
    (directory / f"{raw['id']}.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_catalog_extends_from_config_and_omits_disabled(tmp_path):
    write_pool(tmp_path)
    write_pool(tmp_path, id="disabled", enabled=False)
    catalog = PoolCatalog(UniverseRepository(ROOT / "config/radar_universes"),
                          StockPoolRepository(tmp_path))
    rows = catalog.list()
    assert {item.id for item in rows} == {"cn_hk_etf", "overseas_etf", "extra_pool"}
    assert rows[-1].name == "新增池"
    assert rows[-1].capabilities.update is True
    assert rows[-1].capabilities.manage is True
    assert rows[-1].rule_status == "unconfigured"


def test_stock_definition_accepts_no_members_but_etf_does_not():
    pool = StockPool(id="empty", name="空池", description="用途", display_order=0)
    assert "score_profile" not in pool.model_dump()
    with pytest.raises(ValidationError):
        RadarUniverse(id="empty", name="空池", version=1, asset_type="etf",
                      score_profile="core_rotation_v1", description="用途", instruments=[])


@pytest.mark.parametrize("changes", [{"id": "../bad"}, {"asset_type": "etf"},
                                    {"name": ""}, {"display_order": True},
                                    {"rule_version": "not_confirmed"}, {"description": ""}])
def test_bad_stock_configuration_is_explicit(tmp_path, changes):
    raw = {"id": "extra_pool", "name": "新增池", "description": "用途",
           "display_order": 30, **changes}
    (tmp_path / "bad.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(StockPoolConfigError):
        StockPoolRepository(tmp_path).load_all()


def test_duplicate_stock_id_and_cross_type_collision_are_errors(tmp_path):
    write_pool(tmp_path, id="cn_hk_etf")
    catalog = PoolCatalog(UniverseRepository(ROOT / "config/radar_universes"),
                          StockPoolRepository(tmp_path))
    with pytest.raises(PoolCatalogError, match="重复"):
        catalog.list()
    (tmp_path / "duplicate.yaml").write_text(
        (tmp_path / "cn_hk_etf.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(StockPoolConfigError, match="重复"):
        StockPoolRepository(tmp_path).load_all()


@pytest.mark.parametrize("content", ["[]", "name: 只有名称", "id: [损坏"])
def test_non_mapping_missing_fields_and_bad_yaml_are_rejected(tmp_path, content):
    (tmp_path / "bad.yaml").write_text(content, encoding="utf-8")
    with pytest.raises(StockPoolConfigError):
        StockPoolRepository(tmp_path).load_all()


def test_disabled_stock_cannot_be_read_directly(tmp_path):
    write_pool(tmp_path, enabled=False)
    repository = StockPoolRepository(tmp_path)
    assert repository.get("extra_pool") is None
    assert repository.members("extra_pool") is None
