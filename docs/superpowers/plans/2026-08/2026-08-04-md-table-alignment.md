# 报告表格对齐优化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Jinja2 自定义 filter `md_table`，生成列对齐的 Markdown 表格，终端和文件输出均可读。

**Architecture:** 在 `ReportBuilder.__init__` 中注册 Jinja2 filter，filter 函数预计算列宽并做空格填充。模板中用 `{{ data | md_table }}` 替换手动 `{% for %}` 拼表逻辑。

**Tech Stack:** Python 3.11+, Jinja2

---

### Task 1: md_table filter 核心实现

**Files:**
- Modify: `src/report/builder.py:1-15`
- Create: 无（filter 函数内置在 builder.py 内，保持简单）

- [ ] **Step 1: 实现 `md_table` filter 函数**

在 `src/report/builder.py` 文件顶部（class 之前）添加 filter 函数：

```python
"""报告构建器 — 将分析结果组装为 Markdown 报告"""
from datetime import datetime
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from data.schemas import AnalysisResult


def _display_width(s: str) -> int:
    """计算字符串显示宽度，CJK 字符计 2，ASCII 计 1"""
    w = 0
    for ch in s:
        # Unicode 全角范围：CJK、全角标点、全角字母数字
        if (
            '一' <= ch <= '鿿'    # CJK 统一汉字
            or '　' <= ch <= '〿'  # CJK 标点
            or '＀' <= ch <= '￯'  # 全角形式
            or '⺀' <= ch <= '⻿'  # CJK 部首补充
            or '⼀' <= ch <= '⿟'  # 康熙部首
            or '︰' <= ch <= '﹏'  # CJK 兼容形式
        ):
            w += 2
        else:
            w += 1
    return w


def _md_table(data, headers=None):
    """Jinja2 filter：将 dict 或 list[dict] 转为对齐的 Markdown 表格"""
    rows = []

    if isinstance(data, dict):
        # 过滤掉 list 类型字段（如 headlines, peers），它们在表格外处理
        filtered = {k: v for k, v in data.items() if not isinstance(v, list)}
        if not filtered:
            return ""
        headers = ["指标", "数值"]
        for k, v in filtered.items():
            rows.append([str(k), str(v) if v is not None else "N/A"])

    elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        if headers is None:
            headers = list(data[0].keys())
        for item in data:
            row = []
            for h in headers:
                val = item.get(h, "")
                row.append(str(val) if val is not None else "N/A")
            rows.append(row)
    else:
        return ""

    if not rows:
        return ""

    # 计算每列最大宽度
    col_widths = [_display_width(str(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], _display_width(cell))

    def pad_cell(cell: str, width: int) -> str:
        cw = _display_width(cell)
        return cell + " " * (width - cw)

    lines = []
    # 表头
    header_line = "| " + " | ".join(pad_cell(str(h), w) for h, w in zip(headers, col_widths)) + " |"
    lines.append(header_line)
    # 分隔线
    sep_line = "|-" + "-|-".join("-" * w for w in col_widths) + "-|"
    lines.append(sep_line)
    # 数据行
    for row in rows:
        line = "| " + " | ".join(pad_cell(cell, w) for cell, w in zip(row, col_widths)) + " |"
        lines.append(line)

    return "\n".join(lines)
```

- [ ] **Step 2: 在 `ReportBuilder.__init__` 中注册 filter**

修改 `ReportBuilder.__init__`，在创建 `self._env` 后添加 filter 注册：

```python
class ReportBuilder:
    def __init__(self):
        template_dir = Path(__file__).parent / "templates"
        self._env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._env.filters["md_table"] = _md_table
```

- [ ] **Step 3: 验证 filter 可导入**

```bash
python -c "from report.builder import _md_table; print(_md_table({'a': 1, 'b': 2}))"
```

预期输出：对齐的两列表格

- [ ] **Step 4: Commit**

```bash
git add src/report/builder.py
git commit -m "feat(报告): 新增 md_table Jinja2 filter 实现列对齐 Markdown 表格"
```

---

### Task 2: md_table filter 单元测试

**Files:**
- Create: `tests/report/test_md_table.py`

- [ ] **Step 1: 创建测试文件**

```python
import pytest
from report.builder import _md_table


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
        # 分隔线长度应匹配
        assert len(lines[0]) == len(lines[1])  # header 和 sep 等长
        assert len(lines[0]) == len(lines[2])  # 每行等长

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
```

- [ ] **Step 2: 运行测试确认通过**

```bash
pytest tests/report/test_md_table.py -v
```

预期：全部 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/report/test_md_table.py
git commit -m "test(报告): 新增 md_table filter 单元测试"
```

---

### Task 3: 更新报告模板

**Files:**
- Modify: `src/report/templates/report_v2.jinja2`

- [ ] **Step 1: 更新五大维度表格（第二部分）**

将第 26-44 行替换为使用 `md_table` filter：

```jinja2
{% for dim in ["financial", "technical", "valuation", "industry", "sentiment"] %}

### {{ {"financial": "1. 财务量化数据", "technical": "2. 技术面量化数据", "valuation": "3. 估值量化数据", "industry": "4. 行业对比量化数据", "sentiment": "5. 舆情量化数据"}[dim] }}

{% set r = results_map.get(dim) %}
{% if r and r.status != "unavailable" %}
{{ r.metrics | md_table }}
{% if r.metrics.get("headlines") %}
{% for headline in r.metrics["headlines"] %}
- {{ headline }}
{% endfor %}
{% endif %}
{% else %}
> 该维度数据不足，已跳过
{% endif %}

{% endfor %}
```

- [ ] **Step 2: 更新打分表格（第三部分）**

将第 50-56 行替换为使用 `md_table` filter：

```jinja2
## 三、五大维度标准化打分

{{ score_rows | md_table }}

**综合得分（扣风险前）：** {{ base_score }}/10
{% if risk_deduction > 0 %}
**风险扣分：** -{{ risk_deduction }}
{% endif %}
**最终综合得分：** {{ final_score }}/10
```

- [ ] **Step 3: 检查完整模板**

确认模板剩余部分不变，最终模板结构：
- 标题和基础信息
- 一、标的基础概况（手动表格，字段少且固定，保持不变）
- 二、五大维度量化数据（用 `md_table` + 保留 headlines 循环）
- 三、打分表格（用 `md_table`）
- 四、AI 解读
- 五、多风格视角
- 六、免责声明

- [ ] **Step 4: Commit**

```bash
git add src/report/templates/report_v2.jinja2
git commit -m "feat(报告): 模板改用 md_table filter 生成对齐表格"
```

---

### Task 4: 修复现有测试适配新表格格式

**Files:**
- Modify: `tests/report/test_builder.py:63-71`

- [ ] **Step 1: 更新 `test_table_header_and_rows_are_contiguous`**

原测试断言了未经对齐的精确字符串，对齐后分隔线和单元格宽度会变化，改为检测结构合法性：

```python
    def test_table_header_and_rows_are_contiguous(self):
        results = [
            AnalysisResult(dimension="valuation", status="partial", summary="",
                           metrics={"pe_ttm": 7.5, "pb": 0.85, "ps_ttm": 1.2}),
        ]
        report = ReportBuilder().build("600350", "山东高速", results, commentary={})
        # 表头、分隔线、首行之间无空行，Markdown 表格才能正确渲染
        # 对齐后验证结构：表头行 → 分隔行 → 数据行 连续
        lines = report.split("\n")
        # 找到表格起始位置：包含 "指标" 和 "数值" 的行
        for i, line in enumerate(lines):
            if "指标" in line and "数值" in line:
                # 下一行应是分隔线
                assert lines[i + 1].startswith("|-"), f"期望分隔线，得到: {lines[i + 1]}"
                # 再下一行应是数据行，且不以空行开头
                assert lines[i + 2].startswith("| "), f"期望数据行，得到: {lines[i + 2]}"
                # 数据行包含 pe_ttm
                assert "pe_ttm" in lines[i + 2]
                return
        pytest.fail("未找到表格结构")
```

同时需要在文件顶部添加 `import pytest`：

```python
from datetime import datetime
import pytest
from report.builder import ReportBuilder
from data.schemas import AnalysisResult
```

- [ ] **Step 2: Commit**

```bash
git add tests/report/test_builder.py
git commit -m "test(报告): 适配 md_table 对齐表格的断言格式"
```

---

### Task 5: 回归验证

**Files:**
- 不修改任何文件

- [ ] **Step 1: 运行报告相关测试**

```bash
pytest tests/report/ -v
```

预期：全部 PASS

- [ ] **Step 2: 运行完整测试套件**

```bash
pytest -v
```

预期：全部 PASS

- [ ] **Step 3: 实际生成报告验证效果**

```bash
python -m stock_robot.cli analyze 000001 --no-llm
```

预期：终端表格列对齐，可读

- [ ] **Step 4: 检查保存的 Markdown 文件**

```bash
cat reports/000001_*.md
```

预期：Markdown 文件中表格列对齐
