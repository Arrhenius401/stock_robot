"""行业映射表构建器单元测试"""
import pytest

from data.industry_mapping_builder import (
    IndustryMappingError,
    _get_with_retry,
    fetch_constituents,
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


# 真实表结构 fixture：列序 0序号 1代码 2简称 3纳入时间 4申万2级 5细分概念
# 6价格 7市盈率 8市盈率ttm 9市净率 10ROE 11股息率 12市值（亿元）
# th 含 JSON-LD 注入（真实格式，解析不依赖列名）
COMPOSITION_HTML = """
<table id="tableID">
<tr>
  <th>序号</th><th>股票代码</th><th>股票简称</th><th>纳入时间</th><th>申万2级</th><th>细分概念</th>
  <th>价格\n{"@context":"https://schema.org","name":"渔业成分股数据"}</th>
  <th>市盈率\n{"@context":"https://schema.org"}</th>
  <th>市盈率ttm\n{"@context":"https://schema.org"}</th>
  <th>市净率\n{"@context":"https://schema.org"}</th>
  <th>ROE(%)</th><th>股息率</th><th>市值（亿元）</th>
</tr>
<tr>
  <td>1</td>
  <td><a href="/s/600097">600097.SH</a></td>
  <td style="text-align: center;"><a href="/s/600097">开创国际</a></td>
  <td>1997-06-19</td>
  <td><a href="/stockdata/sw-industry-2021?industryCode=801015.SI">渔业</a></td>
  <td class="tonghuashunConceptItem">-</td>
  <td>12.34</td><td>-</td><td>25.6</td><td>1.8</td><td>3.5</td><td>2.1</td><td>46.2</td>
</tr>
<tr>
  <td>2</td>
  <td><a href="/s/600097">600097.SH</a></td>
  <td><a href="/s/600097">ST开创国际</a></td>
  <td>1997-06-19</td>
  <td><a href="/stockdata/sw-industry-2021?industryCode=801015.SI">渔业</a></td>
  <td>-</td><td>1.0</td><td>-</td><td>-</td><td>-</td><td>-</td><td>-</td><td>10</td>
</tr>
<tr>
  <td>3</td>
  <td><a href="/s/000998">000998.SZ</a></td>
  <td><a href="/s/000998">隆平高科</a></td>
  <td>2000-12-01</td>
  <td><a href="/stockdata/sw-industry-2021?industryCode=850111.SI">种植业</a></td>
  <td>-</td><td>18.9</td><td>88.5</td><td>6.2</td><td>8.8</td><td>0.5</td><td>0</td><td>310.4</td>
</tr>
</table>
"""


class TestFetchConstituents:
    def test_parse_table(self, mocker):
        mocker.patch("data.industry_mapping_builder._get_with_retry",
                     return_value=COMPOSITION_HTML)
        stocks = fetch_constituents("801015.SI")
        assert len(stocks) == 2  # ST 行被过滤
        first = stocks[0]
        assert first["symbol"] == "600097"
        assert first["name"] == "开创国际"
        assert first["level2"] == "渔业"
        assert first["pe_ttm"] == 25.6
        assert first["pb"] == 1.8
        assert first["market_cap"] == 46.2
        assert stocks[1]["symbol"] == "000998"
        assert stocks[1]["level2"] == "种植业"

    def test_parse_empty(self, mocker):
        mocker.patch("data.industry_mapping_builder._get_with_retry",
                     return_value="<table></table>")
        assert fetch_constituents("801015.SI") == []
