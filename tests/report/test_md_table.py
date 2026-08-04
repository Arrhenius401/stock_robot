import pytest
from report.builder import _md_table, _display_width


class TestMdTableDict:
    def test_simple_dict(self):
        data = {"revenue": 100, "roe": 0.12}
        result = _md_table(data)
        lines = result.split("\n")
        assert len(lines) == 4  # header + sep + 2 data rows
        assert "指标" in lines[0]
        assert "数值" in lines[0]
        assert "revenue" in result
        assert "100" in result
        assert "roe" in result
        assert "0.12" in result

    def test_with_none_value(self):
        data = {"ps_ttm": None}
        result = _md_table(data)
        assert "N/A" in result

    def test_filters_list_values(self):
        data = {"peers": ["000001", "600036"], "roe": 0.12}
        result = _md_table(data)
        assert "peers" not in result  # list 字段被过滤
        assert "roe" in result
        assert "0.12" in result

    def test_empty_dict(self):
        result = _md_table({})
        assert result == ""

    def test_only_list_values_dict(self):
        result = _md_table({"headlines": ["新闻1", "新闻2"]})
        assert result == ""


class TestMdTableColumnAlignment:
    def test_columns_are_aligned(self):
        data = {"a": 1, "long_key_name": 2}
        result = _md_table(data)
        lines = result.split("\n")
        # 第一列宽度应一致：表头 "指标" 和 "long_key_name" 中较宽者
        # 分隔线长度应匹配（CJK 字符显示宽为 2，需按显示宽度比较）
        widths = [_display_width(line) for line in lines]
        assert widths[0] == widths[1]  # header 和 sep 等宽
        assert widths[0] == widths[2]  # 每行等宽

    def test_chinese_headers(self):
        data = {"revenue": 35277000000.0, "net_profit": 14523000000}
        result = _md_table(data)
        # 中文表头 "指标"、"数值" 各占 4 显示宽
        assert "指标" in result
        assert "数值" in result


class TestMdTableListOfDicts:
    def test_list_of_dicts(self):
        data = [
            {"label": "财务健康", "score": "8.5", "weight": "30%"},
            {"label": "技术趋势", "score": "7.0", "weight": "20%"},
        ]
        result = _md_table(data)
        lines = result.split("\n")
        assert len(lines) == 4  # header + sep + 2 rows
        assert "label" in result
        assert "score" in result
        assert "weight" in result
        assert "财务健康" in result
        assert "8.5" in result

    def test_with_custom_headers(self):
        data = [
            {"维度": "财务", "得分": "8.0"},
            {"维度": "估值", "得分": "9.5"},
        ]
        result = _md_table(data)
        lines = result.split("\n")
        # 表头从 keys 生成
        assert "维度" in lines[0]
        assert "得分" in lines[0]

    def test_empty_list(self):
        result = _md_table([])
        assert result == ""

    def test_non_list_non_dict(self):
        result = _md_table("invalid")
        assert result == ""
