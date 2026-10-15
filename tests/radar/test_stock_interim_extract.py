"""季度及半年累计会计数据保守解析回归。"""

from datetime import date
from pathlib import Path

import pytest

from radar.stocks.interim_extract import extract_interim_text

ROOT = Path(__file__).parent.parent / 'resources' / 'radar'
AS_OF = date(2026, 10, 15)


def parse(name, disclosed, *, pages=None, symbol='600900'):
    if pages is None:
        pages = (ROOT / name).read_text(encoding='utf-8').split('\f')
    return extract_interim_text(pages, symbol=symbol, source_url='https://static.cninfo.com.cn/report.pdf',
                                disclosed_on=disclosed, as_of=AS_OF)


@pytest.mark.parametrize('name,disclosed,months,current,previous', [
    ('cninfo-600900-2026-q1-accounting.txt', '2026-04-30', 3, '6761006898.48', '5180785597.87'),
    ('cninfo-600900-2026-h1-accounting.txt', '2026-08-31', 6, '14755694774.53', '13056352473.55'),
    ('cninfo-600900-2025-q3-accounting.txt', '2025-10-31', 9, '28192874494.95', '28024923212.82'),
])
def test_actual_fixtures_use_cumulative_current_and_prior(name, disclosed, months, current, previous):
    result = parse(name, disclosed)
    assert result['status'] == 'matched_template'
    assert result['period_months'] == months
    assert result['period_basis'] == 'year_to_date'
    assert len(result['financial_proposals']) == 8
    values = {row['comparison_basis']: row for row in result['financial_proposals'] if row['metric'] == 'parent_net_profit'}
    assert values['current']['amount_yuan'] == current
    prior = values.get('previous_unadjusted') or values['previous_adjusted_after']
    assert prior['amount_yuan'] == previous
    assert prior['fiscal_year'] == values['current']['fiscal_year'] - 1
    assert all(row['period_basis'] == 'year_to_date' and row['requires_review'] for row in result['financial_proposals'])
    assert result['comparison_requires_review'] == (months != 3)


def simple_q3():
    return '证券代码：600900\n2026年第三季度报告\n（一）主要会计数据和财务指标\n单位：元 币种：人民币\n项目 本报告期 本报告期比上年同期增减(%) 年初至报告期末 年初至报告期末比上年同期增减(%)\n营业收入 10 1 30 2\n归属于上市公司股东的净利润 2 1 7 2\n归属于上市公司股东的扣除非经常性损益的净利润 1 1 6 2\n经营活动产生的现金流量净额 不适用 不适用 8 2\n'


def test_ordinary_q3_never_treats_single_quarter_as_cumulative():
    result = parse('', '2026-10-10', pages=[simple_q3()])
    assert result['status'] == 'matched_template'
    assert len(result['financial_proposals']) == 4
    assert {row['comparison_basis'] for row in result['financial_proposals']} == {'current'}
    assert next(row for row in result['financial_proposals'] if row['metric'] == 'parent_net_profit')['amount_yuan'] == '7'


def test_target_cumulative_placeholder_is_not_guessable():
    result = parse('', '2026-10-10', pages=[simple_q3().replace('2 1 7 2', '2 1 不适用 2')])
    assert result['status'] == 'pending_verification'
    assert not any(row['metric'] == 'parent_net_profit' for row in result['financial_proposals'])


def test_reverse_adjusted_order_and_wrong_identity_do_not_parse():
    raw = (ROOT / 'cninfo-600900-2026-h1-accounting.txt').read_text(encoding='utf-8')
    assert parse('', '2026-08-31', pages=[raw.replace('调整后 调整前', '调整前 调整后')])['status'] == 'pending_verification'
    assert parse('', '2026-08-31', pages=[raw], symbol='000333')['financial_proposals'] == []


def test_whitespace_and_formfeed_row_continuation_keep_exact_values():
    raw = (ROOT / 'cninfo-600900-2026-q1-accounting.txt').read_text(encoding='utf-8')
    raw = raw.replace('6,761,006,898.48', '\f6,761,006,898.48').replace('营业收入 ', '营 业 收 入 \t')
    result = parse('', '2026-04-30', pages=raw.split('\f'))
    assert result['status'] == 'matched_template'
    parent = next(row for row in result['financial_proposals'] if row['metric'] == 'parent_net_profit' and row['comparison_basis'] == 'current')
    assert parent['amount_yuan'] == '6761006898.48'
    assert parent['page_number'] == 1


@pytest.mark.parametrize('change', ['wrong_unit', 'bad_commas', 'extra_column', 'footnote', 'missing', 'duplicates',
                                    'second_table', 'future', 'before_end', 'special_title', 'bad_currency'])
def test_unknown_layout_and_dates_remain_pending(change):
    raw = (ROOT / 'cninfo-600900-2026-q1-accounting.txt').read_text(encoding='utf-8')
    disclosed = '2026-04-30'
    if change == 'wrong_unit':
        raw = raw.replace('单位：元', '单位：千元')
    elif change == 'bad_commas':
        raw = raw.replace('6,761,006,898.48', '6,76,006,898.48')
    elif change == 'extra_column':
        raw = raw.replace('6,761,006,898.48', '6,761,006,898.48 100')
    elif change == 'footnote':
        raw = raw.replace('6,761,006,898.48', '6,761,006,898.48注1')
    elif change == 'missing':
        raw = raw.replace('6,761,006,898.48', '--')
    elif change == 'duplicates':
        raw += '\n归属于上市公司股东的净利润 1 2 3\n'
    elif change == 'second_table':
        raw += '\n（一）主要会计数据\n单位：元 币种：人民币\n营业收入 1 2 3\n'
    elif change == 'future':
        disclosed = '2026-10-16'
    elif change == 'before_end':
        disclosed = '2026-03-31'
    elif change == 'special_title':
        raw = raw.replace('2026 年第一季度报告', '2026 年第一季度报告（修订）')
    else:
        raw = raw.replace('币种：人民币', '币种：美元')
    result = parse('', disclosed, pages=[raw])
    assert result['status'] == 'pending_verification'
    assert result['requires_review'] is True
    assert result['financial_verified'] is False


def test_q3_wrong_after_order_and_target_placeholders_are_rejected():
    raw = (ROOT / 'cninfo-600900-2025-q3-accounting.txt').read_text(encoding='utf-8')
    unknown = raw.replace('调整前 调整后 调整后 调整前 调整后 调整后', '调整后 调整前 调整后 调整后 调整前 调整后')
    assert parse('', '2025-10-31', pages=[unknown])['financial_proposals'] == []
    unsupported = raw.replace('42,895,214,451.84', '不适用')
    result = parse('', '2025-10-31', pages=[unsupported])
    assert result['status'] == 'pending_verification'
    assert not any(row['metric'] == 'operating_cash_flow' for row in result['financial_proposals'])


def test_adjusted_prior_after_is_not_before_and_is_marked_for_review():
    raw = (ROOT / 'cninfo-600900-2026-h1-accounting.txt').read_text(encoding='utf-8')
    raw = raw.replace('13,056,352,473.55 13,056,352,473.55', '13,056,352,473.55 1.00')
    result = parse('', '2026-08-31', pages=raw.split('\f'))
    prior = next(row for row in result['financial_proposals'] if row['metric'] == 'parent_net_profit' and row['comparison_basis'] != 'current')
    assert prior['amount_yuan'] == '13056352473.55'
    assert prior['comparison_basis'] == 'previous_adjusted_after'
    assert prior['comparison_requires_review'] is True
    assert prior['period_end'] == '2025-06-30'
    assert prior['page_number'] == 2


def test_half_unadjusted_three_columns_and_units_are_exact():
    raw = (ROOT / 'cninfo-600900-2026-q1-accounting.txt').read_text(encoding='utf-8')
    raw = raw.replace('第一季度报告', '半年度报告').replace('项目 本报告期', '项目 本报告期（1－6月）')
    raw = raw.replace('单位：元', '单位：万元')
    result = parse('', '2026-08-31', pages=[raw])
    assert result['status'] == 'matched_template'
    assert result['comparison_requires_review'] is False
    parent = next(row for row in result['financial_proposals'] if row['metric'] == 'parent_net_profit' and row['comparison_basis'] == 'current')
    assert parent['amount_yuan'] == '67610068984800'
    assert parent['source_unit'] == '万元'


def test_negative_and_zero_amounts_preserved_without_imputing_missing():
    raw = simple_q3().replace('2 1 7 2', '2 1 -7 2').replace('1 1 6 2', '1 1 0 2')
    result = parse('', '2026-10-10', pages=[raw])
    assert result['status'] == 'matched_template'
    values = {row['metric']: row['amount_yuan'] for row in result['financial_proposals']}
    assert values['parent_net_profit'] == '-7'
    assert values['deducted_parent_net_profit'] == '0'


def test_quarter_identity_conflicting_titles_and_unsafe_url():
    raw = simple_q3() + '\n2026年第一季度报告\n'
    assert parse('', '2026-10-10', pages=[raw])['financial_proposals'] == []
    result = extract_interim_text([simple_q3()], symbol='600900', source_url='https://user:secret@example.com/a.pdf',
                                  disclosed_on='2026-10-10', as_of=AS_OF)
    assert result['financial_proposals'] == []


def test_empty_or_bad_pages_remain_pending():
    assert parse('', '2026-04-30', pages=[])['status'] == 'pending_verification'
    assert parse('', '2026-04-30', pages=[None])['status'] == 'pending_verification'


def test_q3_ambiguous_cumulative_header_order_is_not_assumed():
    raw = simple_q3().replace('项目 本报告期 本报告期比上年同期增减(%) 年初至报告期末 年初至报告期末比上年同期增减(%)',
        '项目 本报告期 年初至报告期末 上年同期 增幅')
    result = parse('', '2026-10-10', pages=[raw])
    assert result['status'] == 'pending_verification'
    assert result['financial_proposals'] == []


def test_wrapper_reuses_shared_pdf_execution_boundary(monkeypatch, tmp_path):
    from radar.stocks import interim_extract

    calls = []
    def execute(archive_result, archive_dir, as_of, *, text_extractor):
        calls.append((archive_result, archive_dir, as_of, text_extractor))
        return {'items': [], 'financial_proposals': []}
    monkeypatch.setattr(interim_extract, 'extract_archived_financials', execute)
    archive = {'items': []}
    assert interim_extract.extract_archived_interims(archive, tmp_path, AS_OF)['financial_proposals'] == []
    assert calls == [(archive, tmp_path, AS_OF, interim_extract.extract_interim_text)]


def test_actual_variance_explanation_is_not_second_accounting_table():
    raw = (ROOT / 'cninfo-600900-2026-q1-accounting.txt').read_text(encoding='utf-8')
    explanation = '\n(三)主要会计数据、财务指标发生变动的情况、原因\n√适用 □不适用\n项目名称 变动比例（%） 主要原因\n利润总额 32.07\n2026 年一季度，公司利润总额等指标同比增长，主要系公司售电收入增加及所持金融资产浮盈所致。\n归属于上市公司股东的净利润 30.50\n基本每股收益 30.50\n稀释每股收益 30.50\n二、股东信息\n'
    # 完整报告在损益数据后先进入余额表，再进入变动说明，复用实际异常形态。
    raw += '\n本报告期末 上年度末\n总资产 100 90\n' + explanation
    result = parse('', '2026-04-30', pages=[raw])
    assert result['status'] == 'matched_template'
    assert len(result['financial_proposals']) == 8


def test_actual_q3_not_applicable_variance_explanation_is_not_second_table():
    raw = (ROOT / 'cninfo-600900-2025-q3-accounting.txt').read_text(encoding='utf-8')
    raw += '\n本报告期末 上年度末\n总资产 100 90\n（三） 主要会计数据、财务指标发生变动的情况、原因\n□适用 √不适用\n二、股东信息\n'
    result = parse('', '2025-10-31', pages=raw.split('\f'))
    assert result['status'] == 'matched_template'
    assert len(result['financial_proposals']) == 8


@pytest.mark.parametrize('word', ['更正', '取消', '撤销', '修订', '更新', '补充', '变更', '延期', '终止', '英文', '摘要', 'English', 'Summary'])
def test_special_report_versions_do_not_emit_suggestions(word):
    raw = (ROOT / 'cninfo-600900-2026-q1-accounting.txt').read_text(encoding='utf-8')
    raw = raw.replace('2026 年第一季度报告', f'2026 年第一季度报告（{word}）')
    result = parse('', '2026-04-30', pages=[raw])
    assert result['status'] == 'pending_verification'
    assert result['financial_proposals'] == []


def test_actual_q1_page_footer_and_eps_rows_do_not_contaminate_cash_amounts():
    raw = (ROOT / 'cninfo-600900-2026-q1-accounting.txt').read_text(encoding='utf-8')
    raw += '\f中国长江电力股份有限公司2026 年第一季度报告\n2 / 17\n基本每股收益（元/股） 0.2763 0.2117 30.50\n稀释每股收益（元/股） 0.2763 0.2117 30.50\n加权平均净资产收益率\n（%）\n3.01 2.43\n增加 0.58 个百分点\n本报告期末 上年度末\n'
    result = parse('', '2026-04-30', pages=raw.split('\f'))
    assert result['status'] == 'matched_template'
    cash = next(row for row in result['financial_proposals'] if row['metric'] == 'operating_cash_flow' and row['comparison_basis'] == 'current')
    assert cash['amount_yuan'] == '11710663090.9'


def test_actual_q3_eps_after_cash_row_is_not_cash_column():
    raw = (ROOT / 'cninfo-600900-2025-q3-accounting.txt').read_text(encoding='utf-8')
    raw += '\n基本每股收益（元/股） 0.6186 0.6810 0.6810 -9.16 1.1522 1.1454 1.1454 0.60\n稀释每股收益（元/股） 0.6186 0.6810 0.6810 -9.16 1.1522 1.1454 1.1454 0.60\n本报告期末 上年度末\n'
    result = parse('', '2025-10-31', pages=raw.split('\f'))
    assert result['status'] == 'matched_template'
    cash = next(row for row in result['financial_proposals'] if row['metric'] == 'operating_cash_flow' and row['comparison_basis'] == 'current')
    assert cash['amount_yuan'] == '42895214451.84'


def test_conflicting_unit_or_currency_declarations_do_not_choose_first():
    raw = (ROOT / 'cninfo-600900-2026-q1-accounting.txt').read_text(encoding='utf-8')
    for extra in ('单位：万元 币种：人民币', '单位：元 币种：美元', '单位：千元 币种：人民币'):
        altered = raw.replace('单位：元 币种：人民币', '单位：元 币种：人民币\n' + extra)
        result = parse('', '2026-04-30', pages=[altered])
        assert result['status'] == 'pending_verification'
        assert result['financial_proposals'] == []


def test_identical_repeated_unit_declarations_are_consistent():
    raw = (ROOT / 'cninfo-600900-2026-q1-accounting.txt').read_text(encoding='utf-8')
    raw = raw.replace('单位：元 币种：人民币', '单位：元 币种：人民币\n单位：元 币种：人民币')
    assert parse('', '2026-04-30', pages=[raw])['status'] == 'matched_template'
