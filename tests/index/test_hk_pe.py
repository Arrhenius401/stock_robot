from datetime import date, datetime
from io import BytesIO

import pytest
from openpyxl import Workbook

from index.hk_pe import parse_hsi_pe


def workbook(title="Hang Seng Index", metric="MONTH-END WEIGHTED AVERAGE P/E RATIO (times)"):
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "HSI"
    sheet.append([metric])
    sheet.append(["(last update on 30 Sep 2026)"])
    sheet.append([None, title])
    sheet.append([date(2026, 9, 30), 13.79])
    result = BytesIO()
    book.save(result)
    return result.getvalue()


def test_pe_requires_correct_type_and_identity():
    assert parse_hsi_pe(workbook(), "HSI", "Hang Seng Index") == (date(2026, 9, 30), 13.79)
    with pytest.raises(ValueError):
        parse_hsi_pe(b"<html>404</html>", "HSI", "Hang Seng Index")
    with pytest.raises(ValueError):
        parse_hsi_pe(workbook(title="Other"), "HSI", "Hang Seng Index")
    with pytest.raises(ValueError):
        parse_hsi_pe(workbook(metric="OTHER"), "HSI", "Hang Seng Index")


def test_month_only_snapshot_never_displays_a_fabricated_day():
    from data.schemas import AnalysisTarget, IndexAnalysisContext, IndexValuationData
    from index.build_single import IndexReportBuilder
    from index.presentation import valuation_rows
    from report.valuation_dates import index_date_presentation

    value = IndexValuationData(symbol="HSTECH", date=date(2026, 5, 31), pe_snapshot=20.0,
        pe_snapshot_period="2026-05", pe_snapshot_basis="月末加权平均市盈率", valuation_valid=False)
    target = AnalysisTarget(target_type="index", symbol="HSTECH", name="恒生科技", market="hk", index_style="overseas")
    ctx = IndexAnalysisContext(target=target, valuation_data=value)
    assert "2026-05-31" not in str(valuation_rows(ctx))
    assert "2026-05" in str(valuation_rows(ctx))
    assert "2026-05-31" not in str(index_date_presentation(value, {"pe_snapshot": 20.0}, strategy=False))
    assert "2026-05-31" not in str(IndexReportBuilder().build(ctx, []).data_quality)


def test_corrupt_xlsx_has_safe_parse_error():
    with pytest.raises(ValueError):
        parse_hsi_pe(b"PKbroken", "HSI", "Hang Seng Index")


def test_corrupt_cached_xlsx_is_refetched(monkeypatch):
    import base64
    from types import SimpleNamespace
    from unittest.mock import Mock

    from index.hk_pe import fetch_hsi_pe

    cache = Mock()
    cache.get_entry.return_value = SimpleNamespace(value=base64.b64encode(b"PKbroken").decode(), created_at=0, ttl_seconds=86400)
    response = Mock(content=workbook())
    monkeypatch.setattr("index.hk_pe.requests.get", lambda *args, **kwargs: response)
    result = fetch_hsi_pe("HSI", "00001.00", cache)
    assert result.value is not None and result.value.pe_snapshot == 13.79
    cache.put.assert_called_once()


def test_hstech_title_in_first_header_cell_uses_value_column():
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "HSTECH"
    sheet.append(["MONTH-END WEIGHTED AVERAGE P/E RATIO (times)"])
    sheet.append(["(last update on 30 Sep 2026)"])
    sheet.append(["Hang Seng TECH Index"])
    sheet.append(["Sep 2026", 28.23])
    result = BytesIO()
    book.save(result)
    assert parse_hsi_pe(result.getvalue(), "HSTECH", "Hang Seng TECH Index") == (date(2026, 9, 30), 28.23)


def test_quality_labels_snapshot_without_daily_samples():
    from data.schemas import AnalysisTarget, IndexAnalysisContext, IndexValuationData
    from index.presentation import build_quality

    value = IndexValuationData(symbol="HSI", date=date(2026, 10, 30), pe_snapshot=13.04,
        pe_snapshot_as_of=date(2026, 10, 30), pe_snapshot_basis="月末加权平均市盈率")
    ctx = IndexAnalysisContext(target=AnalysisTarget(
        target_type="index", symbol="HSI", name="恒生指数", market="hk", index_style="overseas"), valuation_data=value)
    quality = build_quality(ctx, {"valuation": {"status": "partial", "metrics": {}}})
    fields = {field["label"]: field["value"] for dimension in quality["dimensions"]
              if dimension["key"] == "valuation" for field in dimension["fields"]}
    assert "PE日频样本0个" in fields["样本范围"]
    assert "PE快照13.04" in fields["样本范围"]


def test_cache_fallback_keeps_original_collection_time(monkeypatch):
    import base64
    from types import SimpleNamespace
    from unittest.mock import Mock

    from index.hk_pe import fetch_hsi_pe

    created = int(datetime.now().astimezone().timestamp()) - 60
    cache = Mock()
    cache.get_entry.return_value = SimpleNamespace(
        value=base64.b64encode(workbook()).decode(), created_at=created, ttl_seconds=1)
    monkeypatch.setattr("index.hk_pe.requests.get", Mock(side_effect=OSError("offline")))
    result = fetch_hsi_pe("HSI", "00001.00", cache)
    assert result.record.cache_status == "stale"
    assert result.record.collected_at == datetime.fromtimestamp(created).astimezone()
