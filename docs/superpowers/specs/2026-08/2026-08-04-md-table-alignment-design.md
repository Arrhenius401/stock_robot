# 报告表格对齐优化设计

## 问题

当前报告中的 Markdown 表格单元格未做宽度对齐处理，数据值长度差异大时，终端和 Markdown 文件中表格列参差不齐，阅读困难。

## 目标

终端（rich 渲染）和保存的 Markdown 文件均呈现列对齐的表格。

## 方案

**方案 A：智能对齐 Markdown**

在 Jinja2 模板层注册自定义 filter `md_table`，渲染时预计算列宽并做空格填充，生成原生对齐的 Markdown 表格。

## 架构

- `src/report/builder.py` — `ReportBuilder.__init__` 中注册 Jinja2 自定义 filter `md_table`
- `src/report/templates/report_v2.jinja2` — 模板中用 filter 替换手动拼表逻辑

数据流不变：`pipeline.run()` → `builder.build()` → `template.render()`

## `md_table` filter 行为

### 输入类型

| 输入 | 行为 |
|------|------|
| `dict`（如 `r.metrics`） | 转成两列表 `| 指标 | 数值 |`，自动滤掉嵌套 list 字段 |
| `list[dict]`（如 `score_rows`） | 从 dict keys 生成表头 |

### 对齐逻辑

1. 遍历所有行，计算每列最大显示宽度
2. 中文字符（Unicode 全角）计 2 宽，ASCII 计 1 宽
3. 每列右侧填空格补齐到最大宽度
4. 分隔行 `|------|` 同样按宽度填充

### 嵌套字段

`headlines`、`peers` 等 list 类型字段不在表格内渲染，由模板用 `{% for %}` 在表格外处理。

## 模板变更

原有手动遍历模式：
```jinja2
| 指标 | 数值 |
|------|------|
{% for key, value in r.metrics.items() %}
| {{ key }} | {{ value }} |
{% endfor %}
```

替换为：
```jinja2
{{ r.metrics | md_table }}
```

## 测试

- `md_table` filter 单元测试：校验宽度计算、对齐输出、嵌套字段过滤
- 更新现有模板测试确保输出仍为合法 Markdown
- 手动验证终端渲染效果

## 改动文件

| 文件 | 变更 |
|------|------|
| `src/report/builder.py` | 新增 `md_table` filter 并注册到 Jinja2 环境 |
| `src/report/templates/report_v2.jinja2` | 用 filter 替换手动拼表 |
| `tests/report/test_builder.py` | 新增 filter 单元测试 |
