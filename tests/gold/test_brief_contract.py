"""新版黄金简报与旧存档的服务合同。"""
import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

from gold.models import GoldCollection
from gold.service import GoldService, load_gold_snapshot


def test_brief_is_deterministic_and_archived(tmp_path, mocker):
    now = datetime(2026, 10, 26, tzinfo=UTC)
    collector = mocker.Mock()
    collector.collect.return_value = GoldCollection(generated_at=now)
    llm = mocker.Mock()
    config = SimpleNamespace(config_dir=tmp_path, get=lambda key, default=None: default)
    service = GoldService(config=config, collector=collector, llm=llm, output_dir=tmp_path,
                          analyzer=lambda *_: {"observations": [], "markets": {}, "fx_decomposition": []},
                          renderer=lambda _: "# 黄金市场观察简报")
    result = service.run(now=now)
    assert result["report_schema"] == "gold-observation-v2"
    assert result["name"] == "黄金市场观察简报"
    assert result["commentary_status"] == "not_used"
    assert "long_term" not in result
    assert "core_weight" not in result["rules_snapshot"]
    llm.generate.assert_not_called()
    path = tmp_path / result["report_path"]
    assert load_gold_snapshot(path, path.read_text(encoding="utf-8")) is not None


def test_legacy_snapshot_and_invalid_new_snapshot(tmp_path):
    path = tmp_path / "old.md"
    markdown = "# 原始黄金报告\n旧内容"
    wrapper = {"schema_version": 1, "markdown_sha256": hashlib.sha256(markdown.encode()).hexdigest(),
               "payload": {"report_type": "gold", "symbol": "GOLD", "long_term": {}, "markets": {}}}
    path.with_suffix(".report.json").write_text(json.dumps(wrapper), encoding="utf-8")
    assert load_gold_snapshot(path, markdown) == wrapper["payload"]
    wrapper["payload"]["report_schema"] = "gold-observation-v2"
    path.with_suffix(".report.json").write_text(json.dumps(wrapper), encoding="utf-8")
    assert load_gold_snapshot(path, markdown) is None
