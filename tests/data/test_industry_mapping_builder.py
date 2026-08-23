"""行业映射表构建器单元测试"""
import pytest

from data.industry_mapping_builder import (
    IndustryMappingError,
    _get_with_retry,
    fetch_taxonomy,
)

# 真实页面结构精简 fixture（一级无 parent，二级/三级带 parent span）
OVERVIEW_HTML = """
<div id="801010.SI" class="lg-industries-item">
  <div class="lg-industries-item-title-code"><div class="lg-industries-item-chinese-title">801010.SI</div></div>
  <div class="lg-sw-industries-item-title">
    <div class="lg-industries-item-number">农林牧渔(104)</div>
  </div>
</div>
<div id="801980.SI" class="lg-industries-item">
  <div class="lg-sw-industries-item-title">
    <div class="lg-industries-item-number">银行(42)</div>
  </div>
</div>
<div id="801015.SI" class="lg-industries-item">
  <div class="lg-sw-industries-item-title">
    <div class="lg-industries-item-number">种植业(20)<span class="parent-industry-name">[农林牧渔]</span></div>
  </div>
</div>
<div id="801016.SI" class="lg-industries-item">
  <div class="lg-sw-industries-item-title">
    <div class="lg-industries-item-number">渔业(6)<span class="parent-industry-name">[农林牧渔]</span></div>
  </div>
</div>
<div id="850111.SI" class="lg-industries-item">
  <div class="lg-sw-industries-item-title">
    <div class="lg-industries-item-number">种子(8)<span class="parent-industry-name">[种植业]</span></div>
  </div>
</div>
<div id="850121.SI" class="lg-industries-item">
  <div class="lg-sw-industries-item-title">
    <div class="lg-industries-item-number">海洋捕捞(3)<span class="parent-industry-name">[渔业]</span></div>
  </div>
</div>
"""


class TestFetchTaxonomy:
    @pytest.fixture(autouse=True)
    def relax_limits(self, mocker):
        """fixture 容器数低于生产阈值（400/100/300），放低校验门槛"""
        mocker.patch("data.industry_mapping_builder.MIN_TAXONOMY_COUNT", 5)
        mocker.patch("data.industry_mapping_builder.MIN_LEVEL2_COUNT", 2)
        mocker.patch("data.industry_mapping_builder.MIN_LEVEL3_COUNT", 2)
        # 清空模块级缓存，避免 test_parse_tree 填充后被本类后续用例复用
        mocker.patch("data.industry_mapping_builder._TAXONOMY_CACHE", None)

    def test_parse_tree(self, mocker):
        mocker.patch("data.industry_mapping_builder._get_with_retry",
                     return_value=OVERVIEW_HTML)
        level2_map, level3_map = fetch_taxonomy()
        # 二级 → 一级
        assert level2_map == {"种植业": "农林牧渔", "渔业": "农林牧渔"}
        # 三级码 → (三级名, 二级名)
        assert level3_map["850111.SI"] == ("种子", "种植业")
        assert level3_map["850121.SI"] == ("海洋捕捞", "渔业")

    def test_parse_failure_aborts(self, mocker):
        """结构变化（容器数过少）必须中止，不能产出不可信数据"""
        mocker.patch("data.industry_mapping_builder._get_with_retry",
                     return_value="<html>no industry tree here</html>")
        with pytest.raises(IndustryMappingError):
            fetch_taxonomy()

    def test_retry_then_fail(self, mocker):
        """重试 3 次后仍失败 → IndustryMappingError"""
        from requests import RequestException

        mocker.patch("data.industry_mapping_builder.requests.get",
                     side_effect=RequestException("boom"))
        with pytest.raises(IndustryMappingError):
            _get_with_retry("https://example.com")
