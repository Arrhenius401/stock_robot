"""实际筛选规则的中文只读目录；与规则版本一同交付。"""
from radar.stocks.admission import _CAPITALIZATION_FLOOR
from radar.stocks.formal_rules import FORMAL_RULE_VERSION


def rule_catalog(pool: str, leadership: str = "当前来源尚未绑定排名阈值和同业最小数，不能判断排名通过") -> dict:
    """仅展示已执行条件；机会参考明确标记，不授予正式成员资格。"""
    capital = f'{_CAPITALIZATION_FLOOR / 100000000:g}亿元'
    common = [
        ('listing_age', '上市年限', 'A股上市完整三周年；2月29日上市的非闰年周年按2月末。'),
        ('capitalization_current', '当前公司总市值', f'公司全部股类总市值不低于{capital}，必须与交易窗口一致。'),
        ('capitalization_average_60d', '60日平均公司总市值', f'60个市场交易日均值不低于{capital}；缺失不填零。'),
        ('annual_parent_profit', '年度归母净利润', '最近三个完整年度逐年严格大于0，逐年核验报告期和披露日。'),
        ('annual_deducted_profit', '年度扣非净利润', '最近三个完整年度逐年严格大于0，缺年度保留待核验。'),
        ('ttm_parent_profit', 'TTM归母净利润', '最近十二个月归母净利润严格大于0。'),
        ('ttm_deducted_profit', 'TTM扣非净利润', '最近十二个月扣非净利润严格大于0。'),
        ('audit', '审计意见', '标准无保留通过；保留、否定、无法表示意见失败；强调或其他事项保留待核验。'),
        ('governance', '治理风险', '交易所评价A/B且无公司对象硬失败；C/D失败；近三年纪律处分失败、监管措施待核验；近五年严重事项失败。'),
        ('leadership', '行业地位', '可追溯第三方/协会份额排名，或候选池内申万二级总市值排名；' + leadership),
        ('financial_quality', '分类财务质量', '普通企业复用盈利与审计；银行总资本充足率≥10.5%、拨备覆盖率≥150%、不良率≤5%；证券风险及流动性覆盖率≥100%；保险综合偿付能力≥100%。'),
    ]
    style = [
        ('cash_ratio', '现金流质量', '普通企业三年累计经营现金流/累计归母利润≥0.8；红利为硬条件，成长低于阈值仅作质量提示；金融按适用分支。'),
        ('cash_review', '现金专项', '三年经营现金流覆盖分红及利息、利息保障≥1、期末现金非负；资本开支超标仅提示。金融按对应类型适用。'),
        ('liquidity', '成交活跃度', '近60个市场交易日日均原始成交额1亿元；低于时红利提示、成长观察；资料缺失不能当作零成交。'),
    ]
    if pool == 'dividend_value':
        style.insert(2, ('dividend_history', '连续分红', '最近三个完整年度A股已实施现金分红逐年为正；缺失不能直接判不分红，须完整双源或明确负证据。'))
    else:
        style.append(('opportunity', '成长路径与机会参考', '现金比率低仅提示，现金专项未核清保留待核验；成长路径、估值及趋势为研究参考，不冒充正式机会通过。'))
    return {'rule_version': FORMAL_RULE_VERSION, 'items': [
        {'code': code, 'label': label, 'description': description, 'group': group}
        for group, rows in [('common', common), ('style', style)] for code, label, description in rows]}
