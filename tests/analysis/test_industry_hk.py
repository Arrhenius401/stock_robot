from analysis.industry import IndustryAnalyzer
from data.schemas import AnalysisContext


def test_hk_industry_explicitly_skips_a_share_peer_comparison():
    context = AnalysisContext(symbol="00700.HK", name="腾讯控股", market="hk")

    result = IndustryAnalyzer().analyze(context)

    assert result.status == "unavailable"
    assert result.summary == "港股暂不支持 A 股申万同行比较"
    assert result.score is None
