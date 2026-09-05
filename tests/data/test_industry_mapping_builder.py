"""行业映射表构建器单元测试"""
import pytest

from data.industry_mapping_builder import (
    IndustryMappingError,
    _get_with_retry,
    fetch_constituents,
    fetch_taxonomy,
    migrate_placeholder_rows,
    publish_candidate,
    rebuild_all,
    update_symbol,
    validate_sample,
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
        mocker.patch("data.industry_mapping_builder.time.sleep")
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


LEVEL2_MAP = {"种植业": "农林牧渔", "渔业": "农林牧渔", "银行": "银行"}
LEVEL3_MAP = {"850111.SI": ("种子", "种植业"), "850121.SI": ("海洋捕捞", "渔业")}
STOCK_A = {"symbol": "000998", "name": "隆平高科", "level2": "种植业",
           "pe_ttm": 88.5, "pb": 6.2, "market_cap": 310.4}
STOCK_B = {"symbol": "600097", "name": "开创国际", "level2": None,
           "pe_ttm": 25.6, "pb": 1.8, "market_cap": 46.2}


# 模拟现网旧表（2 只占位股），作为覆盖率分母
OLD_CSV = (
    "symbol,sw_level1,sw_level2,style_category\n"
    "000001,综合,,高端制造\n"
    "600036,综合,,高端制造\n"
)


class TestRebuildAll:
    def test_rebuild_writes_csv(self, mocker, tmp_path):
        old_csv = tmp_path / "industry_mapping.csv"
        old_csv.write_text(OLD_CSV, encoding="utf-8")
        mocker.patch("data.industry_mapping_builder._get_with_retry")
        mocker.patch("data.industry_mapping_builder.fetch_taxonomy",
                     return_value=(LEVEL2_MAP, LEVEL3_MAP))
        mocker.patch("data.industry_mapping_builder.fetch_constituents",
                     side_effect=[[STOCK_A], [STOCK_B]])
        # 行内 level2 缺失时用容器 parent 兜底
        mocker.patch("data.industry_mapping_builder._csv_path",
                     return_value=old_csv)
        mocker.patch("data.industry_mapping_builder.time.sleep")

        mocker.patch("data.industry_mapping_builder._candidate_path",
                     return_value=tmp_path / "industry_mapping.candidate.csv")
        mocker.patch("data.industry_mapping_builder._checkpoint_path",
                     return_value=tmp_path / "industry_mapping.candidate.state.json")
        result = rebuild_all(delay=0.0)
        assert result["stock_count"] == 2
        assert result["failed_industries"] == []
        assert result["coverage_pct"] == 100.0

        import csv as _csv
        assert old_csv.read_text(encoding="utf-8") == OLD_CSV
        with open(tmp_path / "industry_mapping.candidate.csv", encoding="utf-8") as f:
            rows = list(_csv.DictReader(f))
        assert len(rows) == 2
        by_symbol = {r["symbol"]: r for r in rows}
        assert by_symbol["000998"]["sw_level1"] == "农林牧渔"
        assert by_symbol["000998"]["sw_level2"] == "种植业"
        assert by_symbol["000998"]["style_category"] == "必选消费"
        # level2 缺失 → 容器 parent（渔业）兜底
        assert by_symbol["600097"]["sw_level1"] == "农林牧渔"
        assert by_symbol["600097"]["sw_level2"] == "渔业"
        assert result["valid_classification_rate"] == 100.0

    def test_rebuild_skips_failed_industry(self, mocker, tmp_path):
        old_csv = tmp_path / "industry_mapping.csv"
        old_csv.write_text(OLD_CSV, encoding="utf-8")
        mocker.patch("data.industry_mapping_builder.fetch_taxonomy",
                     return_value=(LEVEL2_MAP, LEVEL3_MAP))
        mocker.patch("data.industry_mapping_builder.fetch_constituents",
                     side_effect=IndustryMappingError("网络失败"))
        mocker.patch("data.industry_mapping_builder._csv_path",
                     return_value=old_csv)
        mocker.patch("data.industry_mapping_builder._candidate_path",
                     return_value=tmp_path / "industry_mapping.candidate.csv")
        mocker.patch("data.industry_mapping_builder._checkpoint_path",
                     return_value=tmp_path / "industry_mapping.candidate.state.json")
        mocker.patch("data.industry_mapping_builder.time.sleep")

        with pytest.raises(IndustryMappingError, match="候选映射为空"):
            rebuild_all(delay=0.0)

    def test_progress_callback(self, mocker, tmp_path):
        seen = []
        old_csv = tmp_path / "industry_mapping.csv"
        old_csv.write_text(OLD_CSV, encoding="utf-8")
        mocker.patch("data.industry_mapping_builder.fetch_taxonomy",
                     return_value=(LEVEL2_MAP, LEVEL3_MAP))
        mocker.patch("data.industry_mapping_builder.fetch_constituents",
                     side_effect=[[STOCK_A], [STOCK_B]])
        mocker.patch("data.industry_mapping_builder._csv_path",
                     return_value=old_csv)
        mocker.patch("data.industry_mapping_builder._candidate_path",
                     return_value=tmp_path / "industry_mapping.candidate.csv")
        mocker.patch("data.industry_mapping_builder._checkpoint_path",
                     return_value=tmp_path / "industry_mapping.candidate.state.json")
        mocker.patch("data.industry_mapping_builder.time.sleep")

        rebuild_all(delay=0.0, on_progress=lambda i, t, n: seen.append((i, t, n)))
        assert seen == [(1, 2, "种子"), (2, 2, "海洋捕捞")]

    def test_invalid_reported_level2_falls_back_to_container(self, mocker, tmp_path):
        old_csv = tmp_path / "industry_mapping.csv"
        old_csv.write_text(
            "symbol,sw_level1,sw_level2,style_category\n000998,农林牧渔,种植业,必选消费\n",
            encoding="utf-8",
        )
        mocker.patch("data.industry_mapping_builder.fetch_taxonomy",
                     return_value=(LEVEL2_MAP, {"850111.SI": ("种子", "种植业")}))
        malformed = {**STOCK_A, "level2": "种植业（旧口径）"}
        mocker.patch("data.industry_mapping_builder.fetch_constituents", return_value=[malformed])
        mocker.patch("data.industry_mapping_builder._csv_path", return_value=old_csv)
        mocker.patch("data.industry_mapping_builder._candidate_path",
                     return_value=tmp_path / "industry_mapping.candidate.csv")
        mocker.patch("data.industry_mapping_builder._checkpoint_path",
                     return_value=tmp_path / "industry_mapping.candidate.state.json")
        result = rebuild_all(delay=0.0)
        assert result["level2_fallback_count"] == 1
        rows = _read_csv(tmp_path / "industry_mapping.candidate.csv")
        assert rows["000998"]["sw_level1"] == "农林牧渔"

    def test_publish_rejects_placeholder_candidate(self, mocker, tmp_path):
        current = tmp_path / "industry_mapping.csv"
        candidate = tmp_path / "industry_mapping.candidate.csv"
        state = tmp_path / "industry_mapping.candidate.state.json"
        current.write_text(OLD_CSV, encoding="utf-8")
        candidate.write_text(OLD_CSV, encoding="utf-8")
        state.write_text(
            '{"version": 1, "completed": ["850111.SI"], "failed": [], '
            '"expected_codes": ["850111.SI"], "level2_map": {"种植业": "农林牧渔"}}',
            encoding="utf-8",
        )
        mocker.patch("data.industry_mapping_builder._csv_path", return_value=current)
        mocker.patch("data.industry_mapping_builder._candidate_path", return_value=candidate)
        mocker.patch("data.industry_mapping_builder._checkpoint_path", return_value=state)
        with pytest.raises(IndustryMappingError, match="有效分类率"):
            publish_candidate()

    def test_publish_rejects_unresolved_failed_industries(self, mocker, tmp_path):
        current = tmp_path / "industry_mapping.csv"
        candidate = tmp_path / "industry_mapping.candidate.csv"
        state = tmp_path / "industry_mapping.candidate.state.json"
        current.write_text(OLD_CSV, encoding="utf-8")
        candidate.write_text(
            "symbol,sw_level1,sw_level2,style_category\n"
            "000998,农林牧渔,种植业,必选消费\n"
            "600097,农林牧渔,渔业,必选消费\n",
            encoding="utf-8",
        )
        state.write_text(
            '{"version": 1, "completed": ["850111.SI", "850121.SI"], '
            '"failed": ["850121.SI"], "expected_codes": ["850111.SI", "850121.SI"], '
            '"level2_map": {"种植业": "农林牧渔", "渔业": "农林牧渔"}}',
            encoding="utf-8",
        )
        mocker.patch("data.industry_mapping_builder._csv_path", return_value=current)
        mocker.patch("data.industry_mapping_builder._candidate_path", return_value=candidate)
        mocker.patch("data.industry_mapping_builder._checkpoint_path", return_value=state)
        with pytest.raises(IndustryMappingError, match="失败行业"):
            publish_candidate()

    def test_publish_rejects_candidate_without_complete_checkpoint(self, mocker, tmp_path):
        current = tmp_path / "industry_mapping.csv"
        candidate = tmp_path / "industry_mapping.candidate.csv"
        current.write_text(OLD_CSV, encoding="utf-8")
        candidate.write_text(
            "symbol,sw_level1,sw_level2,style_category\n000998,农林牧渔,种植业,必选消费\n",
            encoding="utf-8",
        )
        mocker.patch("data.industry_mapping_builder._csv_path", return_value=current)
        mocker.patch("data.industry_mapping_builder._candidate_path", return_value=candidate)
        mocker.patch("data.industry_mapping_builder._checkpoint_path",
                     return_value=tmp_path / "missing.state.json")
        with pytest.raises(IndustryMappingError, match="状态文件不存在"):
            publish_candidate()

    def test_publish_rejects_invalid_hierarchy(self, mocker, tmp_path):
        current = tmp_path / "industry_mapping.csv"
        candidate = tmp_path / "industry_mapping.candidate.csv"
        state = tmp_path / "industry_mapping.candidate.state.json"
        current.write_text("symbol,sw_level1,sw_level2,style_category\n000998,农林牧渔,种植业,必选消费\n",
                           encoding="utf-8")
        candidate.write_text("symbol,sw_level1,sw_level2,style_category\n000998,错误行业,种植业,必选消费\n",
                             encoding="utf-8")
        state.write_text(
            '{"version": 1, "completed": ["850111.SI"], "failed": [], '
            '"expected_codes": ["850111.SI"], "level2_map": {"种植业": "农林牧渔"}}',
            encoding="utf-8",
        )
        mocker.patch("data.industry_mapping_builder._csv_path", return_value=current)
        mocker.patch("data.industry_mapping_builder._candidate_path", return_value=candidate)
        mocker.patch("data.industry_mapping_builder._checkpoint_path", return_value=state)
        with pytest.raises(IndustryMappingError, match="非法行业层级"):
            publish_candidate()

    def test_publish_rejects_incomplete_checkpoint(self, mocker, tmp_path):
        current = tmp_path / "industry_mapping.csv"
        candidate = tmp_path / "industry_mapping.candidate.csv"
        state = tmp_path / "industry_mapping.candidate.state.json"
        current.write_text("symbol,sw_level1,sw_level2,style_category\n000998,农林牧渔,种植业,必选消费\n",
                           encoding="utf-8")
        candidate.write_text("symbol,sw_level1,sw_level2,style_category\n000998,农林牧渔,种植业,必选消费\n",
                             encoding="utf-8")
        state.write_text(
            '{"version": 1, "completed": ["850111.SI"], "failed": [], '
            '"expected_codes": ["850111.SI", "850121.SI"], '
            '"level2_map": {"种植业": "农林牧渔"}}', encoding="utf-8")
        mocker.patch("data.industry_mapping_builder._csv_path", return_value=current)
        mocker.patch("data.industry_mapping_builder._candidate_path", return_value=candidate)
        mocker.patch("data.industry_mapping_builder._checkpoint_path", return_value=state)
        with pytest.raises(IndustryMappingError, match="尚未完成"):
            publish_candidate()

    def test_publish_rejects_low_coverage_candidate(self, mocker, tmp_path):
        current = tmp_path / "industry_mapping.csv"
        candidate = tmp_path / "industry_mapping.candidate.csv"
        state = tmp_path / "industry_mapping.candidate.state.json"
        current.write_text(OLD_CSV, encoding="utf-8")
        candidate.write_text("symbol,sw_level1,sw_level2,style_category\n000998,农林牧渔,种植业,必选消费\n",
                             encoding="utf-8")
        state.write_text(
            '{"version": 1, "completed": ["850111.SI"], "failed": [], '
            '"expected_codes": ["850111.SI"], "level2_map": {"种植业": "农林牧渔"}}',
            encoding="utf-8",
        )
        mocker.patch("data.industry_mapping_builder._csv_path", return_value=current)
        mocker.patch("data.industry_mapping_builder._candidate_path", return_value=candidate)
        mocker.patch("data.industry_mapping_builder._checkpoint_path", return_value=state)
        with pytest.raises(IndustryMappingError, match="覆盖率"):
            publish_candidate()

    def test_resume_requires_candidate_and_checkpoint_pair(self, mocker, tmp_path):
        current = tmp_path / "industry_mapping.csv"
        current.write_text(OLD_CSV, encoding="utf-8")
        mocker.patch("data.industry_mapping_builder.fetch_taxonomy",
                     return_value=(LEVEL2_MAP, LEVEL3_MAP))
        mocker.patch("data.industry_mapping_builder._csv_path", return_value=current)
        mocker.patch("data.industry_mapping_builder._candidate_path",
                     return_value=tmp_path / "missing.candidate.csv")
        mocker.patch("data.industry_mapping_builder._checkpoint_path",
                     return_value=tmp_path / "missing.state.json")
        with pytest.raises(IndustryMappingError, match="配套"):
            rebuild_all(delay=0.0, resume=True)

    def test_validate_sample_rejects_partial_failure(self, mocker):
        mocker.patch("data.industry_mapping_builder.fetch_taxonomy",
                     return_value=(LEVEL2_MAP, LEVEL3_MAP))
        mocker.patch("data.industry_mapping_builder.fetch_constituents",
                     side_effect=[[STOCK_A], IndustryMappingError("网络失败")])
        with pytest.raises(IndustryMappingError, match="抓取失败"):
            validate_sample(sample_size=2, delay=0.0)


def _read_csv(path):
    import csv as _csv
    with open(path, encoding="utf-8") as f:
        return {row["symbol"]: row for row in _csv.DictReader(f)}


# 真实个股页行业区块结构（I/II/III 前缀标记层级）
STOCK_PAGE_HTML = """
<div class="lg-stock-info-basic-title clearfix">
  <span class="industry">
    <a class="industry-name" href="/stockdata/sw-industry-2021?industryCode=801010.SI" >I农林牧渔</a>
    <i class="fa fa-angle-right arrow" aria-hidden="true"></i>
    <a class="industry-name" href="/stockdata/sw-industry-2021?industryCode=801015.SI" >II渔业</a>
    <i class="fa fa-angle-right arrow" aria-hidden="true"></i>
    <a class="industry-name" href="/stockdata/sw-industry-2021?industryCode=850121.SI" >III海洋捕捞</a>
  </span>
</div>
"""


class TestUpdateSymbol:
    def test_update_existing_row(self, mocker, tmp_path):
        old_csv = tmp_path / "industry_mapping.csv"
        old_csv.write_text(
            "symbol,sw_level1,sw_level2,style_category\n"
            "600097,综合,,高端制造\n"
            "000001,银行,,大金融\n",
            encoding="utf-8",
        )
        fetch = mocker.patch("data.industry_mapping_builder._get_with_retry",
                             return_value=STOCK_PAGE_HTML)
        mocker.patch("data.industry_mapping_builder.fetch_taxonomy",
                     return_value=({"渔业": "农林牧渔"}, {}))
        mocker.patch("data.industry_mapping_builder._csv_path",
                     return_value=old_csv)

        result = update_symbol("600097")
        assert result["action"] == "updated"
        assert result["sw_level1"] == "农林牧渔"
        assert result["sw_level2"] == "渔业"
        assert result["style_category"] == "必选消费"
        assert fetch.call_args.kwargs == {"retries": 3, "timeout": 15}

        import csv as _csv
        with open(old_csv, encoding="utf-8") as f:
            rows = {r["symbol"]: r for r in _csv.DictReader(f)}
        assert rows["600097"]["sw_level1"] == "农林牧渔"
        assert rows["600097"]["sw_level2"] == "渔业"
        assert rows["000001"]["sw_level1"] == "银行"  # 其他行不受影响
        assert not (tmp_path / "industry_mapping.csv.tmp").exists()

    def test_insert_new_row(self, mocker, tmp_path):
        old_csv = tmp_path / "industry_mapping.csv"
        old_csv.write_text("symbol,sw_level1,sw_level2,style_category\n000001,银行,,大金融\n",
                           encoding="utf-8")
        mocker.patch("data.industry_mapping_builder._get_with_retry",
                     return_value=STOCK_PAGE_HTML)
        mocker.patch("data.industry_mapping_builder.fetch_taxonomy",
                     return_value=({"渔业": "农林牧渔"}, {}))
        mocker.patch("data.industry_mapping_builder._csv_path",
                     return_value=old_csv)

        result = update_symbol("600097")
        assert result["action"] == "inserted"
        import csv as _csv
        with open(old_csv, encoding="utf-8") as f:
            rows = list(_csv.DictReader(f))
        assert len(rows) == 2

    def test_no_industry_block_raises(self, mocker, tmp_path):
        old_csv = tmp_path / "industry_mapping.csv"
        old_csv.write_text("symbol,sw_level1,sw_level2,style_category\n",
                           encoding="utf-8")
        mocker.patch("data.industry_mapping_builder._get_with_retry",
                     return_value="<html>未上市新股无行业区块</html>")
        mocker.patch("data.industry_mapping_builder._csv_path",
                     return_value=old_csv)
        with pytest.raises(IndustryMappingError):
            update_symbol("600097")

    def test_rejects_invalid_industry_hierarchy_without_writing(self, mocker, tmp_path):
        csv_path = tmp_path / "industry_mapping.csv"
        original = "symbol,sw_level1,sw_level2,style_category\n600097,综合,,高端制造\n"
        csv_path.write_text(original, encoding="utf-8")
        mocker.patch("data.industry_mapping_builder._get_with_retry",
                     return_value=STOCK_PAGE_HTML)
        mocker.patch("data.industry_mapping_builder.fetch_taxonomy",
                     return_value=({"渔业": "银行"}, {}))
        mocker.patch("data.industry_mapping_builder._csv_path", return_value=csv_path)

        with pytest.raises(IndustryMappingError, match="行业层级无效"):
            update_symbol("600097")

        assert csv_path.read_text(encoding="utf-8") == original


class TestPlaceholderMigration:
    PLACEHOLDER_CSV = (
        "symbol,sw_level1,sw_level2,style_category\n"
        "600097,综合,,高端制造\n"
        "000001,银行,,大金融\n"
    )

    def test_migrate_placeholder_rows(self, mocker, tmp_path):
        """旧版占位行迁移为显式缺失，已确认申万分类不变。"""
        csv_path = tmp_path / "industry_mapping.csv"
        csv_path.write_text(self.PLACEHOLDER_CSV, encoding="utf-8")
        mocker.patch("data.industry_mapping_builder._csv_path", return_value=csv_path)

        result = migrate_placeholder_rows()
        assert result == {"stock_count": 2, "migrated_count": 1}

        import csv as _csv
        with open(csv_path, encoding="utf-8") as f:
            rows = {r["symbol"]: r for r in _csv.DictReader(f)}
        assert rows["600097"]["sw_level1"] == ""
        assert rows["600097"]["sw_level2"] == ""
        assert rows["600097"]["style_category"] == ""
        assert rows["600097"]["mapping_status"] == "missing"
        assert rows["000001"]["sw_level1"] == "银行"  # 其他行不受影响
