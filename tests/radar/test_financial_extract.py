"""年报建议保持原始口径和保守列映射。"""
import hashlib
from datetime import date
from pathlib import Path

import pytest

from radar.stocks.financial_extract import (
    extract_annual_text,
    extract_archived_financials,
)

URL = "https://www.cypc.com.cn/report.pdf"
AS_OF = date(2026, 10, 14)
HEADER = "公司代码：600900\n2025 年年度报告"
TABLE = """主要会计数据 单位：元 币种：人民币
2025年 2024年 本期比上年同期增减(%) 2023年
营业收入 100.00 90.00 11.11 80.00
归属于上市公司股东的净利润 30.00 25.00 20.00 20.00
归属于上市公司股东的扣除非经常性损益的净利润 28.00 24.00 16.67 19.00
经营活动产生的现金流量净额 -2.00 20.00 -110.00 18.00
"""


def extract(pages):
    return extract_annual_text(pages, symbol="600900", source_url=URL,
                               disclosed_on="2026-05-18", as_of=AS_OF)


def test_standard_table_has_three_years_and_negative_value():
    result = extract([HEADER, TABLE])
    assert result["status"] == "matched_template"
    values = result["financial_proposals"]
    assert len(values) == 12
    assert all(v["requires_review"] and not v["financial_verified"] for v in values)
    assert {(v["fiscal_year"], v["amount_yuan"]) for v in values if v["metric"] == "operating_cash_flow"} == {(2025, "-2"), (2024, "20"), (2023, "18")}
    assert {v["scope"] for v in values} == {"reported_parent", "consolidated"}
    assert all(v["page_number"] == 2 for v in values)
    assert "profits" not in result and "coverage" not in result


def test_annual_title_without_repeated_year_character():
    # 美的集团2024年报封面为“2024年度报告”，同样是完整年度标题。
    result = extract([HEADER.replace("2025 年年度报告", "2025年度报告"), TABLE])
    assert result["status"] == "matched_template"
    assert len(result["financial_proposals"]) == 12


def test_real_adjusted_table_only_current_year():
    pages = Path("tests/resources/radar/cypc-600900-2025-annual-summary.txt").read_text(encoding="utf-8").split("\f")
    result = extract(pages)
    values = {v["metric"]: v["amount_yuan"] for v in result["financial_proposals"]}
    assert values == {"revenue": "86241940222.2", "parent_net_profit": "34502809176.39", "deducted_parent_net_profit": "33445575299.94", "operating_cash_flow": "60562925570.41"}
    assert all(v["fiscal_year"] == 2025 for v in result["financial_proposals"])
    assert result["comparison_requires_review"]


@pytest.mark.parametrize("mutation", [lambda s: s.replace("人民币", "美元"), lambda s: s.replace("2024年", "2022年"), lambda s: s.replace("单位：元", "单位不明"), lambda s: s.replace("100.00 90.00 11.11 80.00", "100.00")])
def test_unsupported_tables_do_not_guess(mutation):
    result = extract([HEADER, mutation(TABLE)])
    assert result["status"] == "pending_verification"


def test_conflicting_current_amount_not_selected():
    result = extract([HEADER, TABLE, TABLE.replace("100.00", "101.00")])
    assert not any(v["metric"] == "revenue" and v["fiscal_year"] == 2025 for v in result["financial_proposals"])
    assert result["status"] == "pending_verification"


@pytest.mark.parametrize("header", [HEADER.replace("600900", "600901"), HEADER.replace("2025", "2026"), HEADER + " 更正", "扫描图片"])
def test_invalid_identity_or_title_pending(header):
    assert not extract([header, TABLE])["financial_proposals"]


def test_unit_conversion_without_float():
    result = extract([HEADER, TABLE.replace("单位：元", "单位：万元")])
    assert next(v for v in result["financial_proposals"] if v["metric"] == "revenue")["amount_yuan"] == "1000000"


def _midea_pages():
    return Path("tests/resources/radar/cninfo-000333-2025-annual-data.txt").read_text(encoding="utf-8").split("\f")


def _midea_extract(pages):
    return extract_annual_text(pages, symbol="000333", source_url="https://static.cninfo.com.cn/finalpage/2026-03-31/1225065145.PDF",
                               disclosed_on="2026-03-31", as_of=AS_OF)


def test_real_szse_table_has_identity_and_units_but_currency_remains_pending():
    result = _midea_extract(_midea_pages())
    assert result["status"] == "pending_verification"
    assert result["financial_proposals"] == []
    assert any("第9页年度表币种" in reason for reason in result["reasons"])
    assert not any("身份" in reason for reason in result["reasons"])


def _explicit_currency_pages():
    # 合成明确币种声明仅验证解析能力，不把这项声明写进真实样本。
    pages = _midea_pages()
    pages[8] = pages[8].replace("六、主要会计数据和财务指标", "六、主要会计数据和财务指标\n币种：人民币")
    return pages


def test_szse_explicit_currency_and_per_row_units():
    result = _midea_extract(_explicit_currency_pages())
    assert result["status"] == "matched_template", result["reasons"]
    assert len(result["financial_proposals"]) == 12
    assert not result["comparison_requires_review"]
    values = {v["metric"]: v["amount_yuan"] for v in result["financial_proposals"] if v["fiscal_year"] == 2025}
    assert values == {"revenue": "456451731000", "parent_net_profit": "43945411000",
                      "deducted_parent_net_profit": "41267233000", "operating_cash_flow": "53345930000"}
    assert all(v["source_unit"] == "千元" and v["page_number"] == 9 and v["requires_review"] for v in result["financial_proposals"])


@pytest.mark.parametrize("old,new", [
    ("币种：人民币", "币种：美元"),
    ("币种：人民币", "币种：人民币 币种：美元"),
    ("币种：人民币", ""),
    ("□ 是 √ 否", "√ 是 □ 否"),
    ("□ 是 √ 否", "□ 是 □ 否"),
    ("公司是否需追溯调整或重述以前年度会计数据", "是否存在其他事项"),
    ("营业收入（千元）", "营业收入"),
    ("000333（A 股）", "000334（A 股）"),
])
def test_szse_unverified_fields_do_not_create_proposals(old, new):
    pages = [page.replace(old, new) for page in _explicit_currency_pages()]
    result = _midea_extract(pages)
    assert result["status"] == "pending_verification"
    assert result["financial_proposals"] == []


def test_szse_currency_in_other_section_is_not_table_evidence():
    pages = _midea_pages()
    pages[1] += "\n本期分红币种：人民币"
    assert not _midea_extract(pages)["financial_proposals"]


def test_szse_conflicting_company_information_and_quarter_table_are_rejected():
    pages = _explicit_currency_pages()
    pages[6] = "一、公司信息\n股票代码：600900\n二、其他信息"
    assert not _midea_extract(pages)["financial_proposals"]
    pages = _explicit_currency_pages()
    pages[8] = pages[8].replace("六、主要会计数据和财务指标", "八、分季度主要财务指标")
    assert not _midea_extract(pages)["financial_proposals"]


def test_archive_failure_isolated_and_review_status_rejected(tmp_path):
    output = extract_archived_financials({"items": [None, {"symbol": "600900", "source_url": URL, "status": "downloaded", "discovery_status": "pending_review"}]}, tmp_path, AS_OF)
    assert len(output["items"]) == 2
    assert not output["financial_proposals"]


def test_archive_integrity_before_parser(tmp_path):
    payload = b"%PDF-broken"
    name = hashlib.sha256(URL.encode()).hexdigest() + ".pdf"
    (tmp_path / name).write_bytes(payload)
    item = {"symbol": "600900", "source_url": URL, "disclosed_on": "2026-05-18", "status": "downloaded", "file": name, "sha256": "wrong", "bytes": len(payload)}
    result = extract_archived_financials({"items": [item]}, tmp_path, AS_OF)
    assert "SHA" in result["items"][0]["reasons"][0]


@pytest.mark.parametrize("page_count, expected_count", [(21, 20), (1001, 0)])
def test_archive_verified_bytes_and_page_budget(tmp_path, monkeypatch, page_count, expected_count):
    from radar.stocks import financial_extract

    payload = b"%PDF-controlled"
    name = hashlib.sha256(URL.encode()).hexdigest() + ".pdf"
    path = tmp_path / name
    path.write_bytes(payload)
    calls = []

    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            calls.append(self.text)
            return self.text

    class Reader:
        def __init__(self, stream):
            assert stream.read() == payload
            # 校验后的路径内容变动不能改变解析字节。
            path.write_bytes(b"changed-after-validation")
            self.pages = [Page(HEADER), Page(TABLE)] + [Page("") for _ in range(page_count - 2)]

    monkeypatch.setattr(financial_extract, "PdfReader", Reader)
    item = {"symbol": "600900", "source_url": URL, "disclosed_on": "2026-05-18", "status": "downloaded", "file": name, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
    result = extract_archived_financials({"items": [item]}, tmp_path, AS_OF)
    assert len(calls) == expected_count
    if expected_count:
        assert result["items"][0]["file_sha256"] == item["sha256"]
        assert result["items"][0]["parsed_page_count"] == 20
        assert len(result["financial_proposals"]) == 12
    else:
        assert not result["financial_proposals"]
        assert "1000" in result["items"][0]["reasons"][0]


def test_adjusted_multiple_columns_do_not_map_comparison_years():
    table = TABLE.replace("营业收入", "调整后 调整前\n营业收入").replace("100.00 90.00 11.11 80.00", "100.00 90.00 88.00 11.11 80.00 79.00")
    # 其他行也使用调整后前五列。
    table = table.replace("30.00 25.00 20.00 20.00", "30.00 25.00 24.00 20.00 20.00").replace("28.00 24.00 16.67 19.00", "28.00 24.00 23.00 16.67 19.00").replace("-2.00 20.00 -110.00 18.00", "-2.00 20.00 19.00 -110.00 18.00")
    result = extract([HEADER, table])
    assert len(result["financial_proposals"]) == 4
    assert {v["fiscal_year"] for v in result["financial_proposals"]} == {2025}



def test_real_financial_explanation_and_quarterly_page_is_not_annual_table():
    pages = Path("tests/resources/radar/cypc-600900-2025-annual-summary.txt").read_text(encoding="utf-8").split("\f")
    assert "报告期末公司前三年主要会计数据和财务指标的说明" in pages[2]
    assert "分季度主要财务数据" in pages[2]
    result = extract(pages)
    assert result["status"] == "matched_template"
    assert not result["reasons"]
    assert len(result["financial_proposals"]) == 4
    assert all(v["page_number"] == 2 and v["requires_review"] for v in result["financial_proposals"])


def test_genuine_unsupported_accounting_table_still_pending():
    result = extract([HEADER, TABLE, "(一) 主要会计数据\n单位：美元 币种：美元\n2025年 2024年 本期比上年同期增减 2023年\n营业收入 1 2 3 4"])
    assert result["status"] == "pending_verification"
    assert result["reasons"]


@pytest.mark.parametrize("bad", ["100,00 90.00 11.11 80.00", "1,,00.00 90.00 11.11 80.00", "100.00 90.00 -- 80.00 1", "100.00 90.00 - 11.11 80.00", "100.00% 90.00 11.11 80.00", "100.00 90.00 11.11 80.00 1", "100.00 90.00 11.11 80.00*", "1.2.3 90.00 11.11 80.00"])
def test_numeric_lines_require_complete_valid_tokens(bad):
    result = extract([HEADER, TABLE.replace("100.00 90.00 11.11 80.00", bad)])
    assert result["status"] == "pending_verification"
    assert not any(v["metric"] == "revenue" for v in result["financial_proposals"])


def test_delta_percentage_is_allowed_but_amount_percentage_is_not():
    result = extract([HEADER, TABLE.replace("100.00 90.00 11.11 80.00", "100.00 90.00 11.11% 80.00")])
    assert result["status"] == "matched_template"



def test_archive_bounded_read_rejects_growth_after_stat(tmp_path, monkeypatch):
    from io import BytesIO

    name = hashlib.sha256(URL.encode()).hexdigest() + ".pdf"
    path = tmp_path / name
    path.write_bytes(b"%PDF-small")
    original_open = Path.open
    limits = []

    class GrowingStream(BytesIO):
        def read(self, size=-1):
            limits.append(size)
            return super().read(size)

    def controlled_open(self, *args, **kwargs):
        if self == path and args == ("rb",):
            return GrowingStream(b"%PDF-" + b"a" * (25 * 1024 * 1024))
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", controlled_open)
    item = {"symbol": "600900", "source_url": URL, "disclosed_on": "2026-05-18", "status": "downloaded", "file": name, "sha256": "unused", "bytes": 10}
    result = extract_archived_financials({"items": [item]}, tmp_path, AS_OF)
    assert limits == [25 * 1024 * 1024 + 1]
    assert "25MiB" in result["items"][0]["reasons"][0]


@pytest.mark.parametrize("second_amount", ["100.00", "200.00"])
def test_multiple_accounting_tables_on_same_page_are_not_selected(second_amount):
    repeated = TABLE.replace("100.00", second_amount)
    result = extract([HEADER, TABLE + "主要财务指标\n" + repeated])
    # 即使两表一致，也不猜测报表类型或合并范围。
    assert result["status"] == "pending_verification"
    assert not result["financial_proposals"]
    assert any("多个" in reason for reason in result["reasons"])


def test_section_title_and_table_heading_are_single_table():
    result = extract([HEADER, "(一) 主要会计数据\n" + TABLE])
    assert result["status"] == "matched_template"
    assert len(result["financial_proposals"]) == 12


def test_second_main_table_is_rejected_even_if_first_has_no_revenue_row():
    first = TABLE.replace("营业收入 100.00 90.00 11.11 80.00\n", "")
    second = TABLE.replace("净利润 30.00", "净利润 300.00")
    result = extract([HEADER, first + "主要财务指标\n" + second])
    assert result["status"] == "pending_verification"
    assert result["financial_proposals"] == []
    assert any("多个主要会计数据表" in reason for reason in result["reasons"])
