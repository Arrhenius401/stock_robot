"""个股报告使用的市场规则和展示口径。"""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class MarketProfile:
    key: str
    label: str
    industry_scheme: str
    industry_note: str
    period_basis: Literal["quarterly_ttm", "report_period", "ttm"]
    currency_guard: bool
    sentiment_policy: Literal["scored", "news_or_assessed", "not_collected"]
    business_profile: Literal["ths", "f10_hk", "f10_us"]
    annual_industry_policy: Literal["scored", "reference_only"]
    financial_special_policy: Literal["none", "facts_only"]
    disclaimer: tuple[str, ...]
    prompt_rules: str
    limitation: str
    signal_policy: Literal["data_gated"] = "data_gated"

    def payload(self) -> dict[str, str | bool]:
        return {
            "key": self.key,
            "label": self.label,
            "industry_scheme": self.industry_scheme,
            "period_basis": self.period_basis,
            "currency_guard": self.currency_guard,
            "sentiment_policy": self.sentiment_policy,
            "signal_policy": self.signal_policy,
            "business_profile_source": self.business_profile,
        }

    def coverage_basis(self, extra_analysis: bool) -> str:
        if self.sentiment_policy == "not_collected":
            return "五维原权重数据覆盖（美股舆情本期未计分），不表示评分置信度"
        return ("五维原权重数据覆盖，不表示评分置信度" if extra_analysis else
                "五维原权重数据覆盖，不表示字段完整度")


def _disclaimer(*, hk: bool = False, us: bool = False) -> tuple[str, ...]:
    """Markdown 与结构化报告共用免责声明，避免两端文案漂移。"""
    paragraphs = [
        "本报告仅供信息参考与研究使用，不构成投资建议或收益保证，不应作为投资决策的唯一依据。",
        "报告基于公开数据生成，数据可能存在延迟、缺失或误差，实际来源与覆盖范围请参阅「数据覆盖与时效」。",
    ]
    return tuple(paragraphs)


_LIMITATION = "历史估值仅代表本次采样区间；同行按同报告期比较；舆情仅覆盖已采集样本。"
_PROFILES = {
    "a-shares": MarketProfile(
        key="a-shares", label="A 股", industry_scheme="申万一级行业", industry_note="",
        period_basis="quarterly_ttm", currency_guard=False, sentiment_policy="scored",
        business_profile="ths", annual_industry_policy="scored", financial_special_policy="none",
        disclaimer=_disclaimer(), prompt_rules='A 股规则：申万行业口径；按已记录的财报原币、会计准则及报告期间解读，缺失不推断。',
        limitation=_LIMITATION,
    ),
    "hk": MarketProfile(
        key="hk", label="港股", industry_scheme="恒生HSICS",
        industry_note="分类口径：恒生HSICS二级（东财F10转载；HSCI PDF校验）",
        period_basis="report_period", currency_guard=True, sentiment_policy="news_or_assessed",
        business_profile="f10_hk", annual_industry_policy="scored", financial_special_policy="none",
        disclaimer=_disclaimer(hk=True), prompt_rules='港股规则：行业使用恒生HSICS，不套用申万分类；只解读程序已计算的行业分和综合分。财务金额按数据源返回值展示，目录列报币种不能证明金额币种，不得声称上游未换汇。金额币种未核实时明确说明，不作金额同比、现金流趋势或与港币行情混算。按报告期分析，不能把半年度/年度数据当季度或推导未经验证的 TTM、历史分位。',
        limitation=_LIMITATION + "财报按报告期解读，金额币种未核实时不作金额比较。",
    ),
    "us": MarketProfile(
        key="us", label="美股", industry_scheme="东财美股 F10 行业（BELONG_INDUSTRY）",
        industry_note="分类口径：东财美股 F10 行业（BELONG_INDUSTRY），不是 GICS/申万分类；经营指标为 TTM 派生",
        period_basis="ttm", currency_guard=True, sentiment_policy="not_collected",
        business_profile="f10_us", annual_industry_policy="reference_only", financial_special_policy="facts_only",
        disclaimer=_disclaimer(us=True), prompt_rules='美股规则：上市报价为 USD；财报按财季和已记录的美国会计准则证据解读，缺少币种或会计准则证据时明确未核实，不能以上市地推断。行业经营比较仅 TTM 最新期，不虚构年度同行经营分；只解读程序给出的两项经营指标及样本数，财务 partial 必须说明可用子项和缺项。行业经营比较为 TTM 派生，未经核实的营收和净利率不得补造，不能混作年度或最新财报期。完整年度仅供参考，不参与行业评分；银行专项仅展示已核实事实。舆情本期未采集可评估样本，未计分。统一使用中文‘自由现金流’。',
        limitation=_LIMITATION + "美股评分尚未做历史回测校准。",
    ),
}
_PROFILES_BY_LABEL = {profile.label: profile for profile in _PROFILES.values()}


def profile_for(market: str) -> MarketProfile:
    try:
        return _PROFILES[market]
    except KeyError:
        raise ValueError(f"不支持的个股市场：{market}") from None


def profile_for_label(label: str) -> MarketProfile:
    try:
        return _PROFILES_BY_LABEL[label]
    except KeyError:
        raise ValueError(f"不支持的个股市场标签：{label}") from None


def build_disclaimer(profile: MarketProfile) -> list[str]:
    return list(profile.disclaimer)
