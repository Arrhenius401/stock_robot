# 共同准入研究使用说明

本阶段承接共享候选及证据审核。入口为 `stock-robot radar stock-admission-evaluate --db PATH --as-of 2026-10-15 --output REPORT.json`。可用 `--symbol 600900` 只计算单家公司；不传则计算全部共享候选。输出独立研究报告，不修改数据库中的公司资格，不发布成员。

## 如何提供事实

先按《共享候选与证据审核使用说明》通过 `stock-evidence-add` 添加明确来源、SHA256及披露日期的规范事实，再以 `stock-evidence-review` 显式审核。导入候选、自带 verified 字段、PDF模板解析建议均不能代替这一步；审核表示事实可信，不表示符合投资标准。

规范事实分为七种 kind（字段完整约定见共同准入实施计划）：

| kind | 核验内容 |
| --- | --- |
| admission_security | SSE/SZSE普通A股、首次A股上市日期、企业类型 |
| admission_capitalization | 人民币公司全股类市值，当前及60个市场交易日完整序列 |
| admission_profitability | 最近三个完整年度归母及扣非归母利润、独立最新TTM |
| admission_audit | 三个完整年度审计意见与整改核验 |
| admission_governance | 治理专项结论及依据 |
| admission_leadership | 一级/二级行业、业务范围及龙头证据 |
| admission_financial_quality | 与企业类型匹配的财务专项，金融单独核验 |

示例规范payload（仅展示字段，不是实际公司的事实）：

```json
{"exchange":"SSE","security_type":"ordinary_a","a_listed_on":"2010-01-01","issuer_type":"ordinary"}
```

资料来源、披露时点、审核时点、90天有效期及归属均须有效。同一组存在相互矛盾的有效事实则待核验，不选择有利的一份。人工给出 clear 也不能绕过市值、上市年限和利润数值；任何确定性硬条件失败保留失败，缺失项另列待核验，不填零。

## 市场窗口与结果

可选 `--market-window PATH.json` 输入60个严格递增、唯一、已经核验的市场交易日ISO日期列表。不能把任意60条个股行情或自然工作日当交易日历。未提供窗口时市值时点与60日覆盖待核验。报告记录输入路径及SHA256，此文件只是研究上下文；后续正式发布还须绑定生产交易日历和行情版本。

报告每家公司保留 `source_revision`、`as_of`、`rule_version`、来源证据ID及逐项 checks。计算期间公司资料revision变化会失效并提示重算；单家公司异常不丢失其他公司的结果。

`common_status` 为 passed/failed/pending_verification。已确认硬条件失败时研究报告的 `admission_status` 为 excluded；即使共同条件全部通过，最终仍为 pending_verification，因为风格及机会规则尚未完整配置。`formal_screening_enabled=false`、`members_published=0` 始终保持。报告中的 excluded 不等于监管认定或数据库成员淘汰操作。

本阶段不启用尚未确认的股息率、估值、成长或趋势数值，不将流动性1亿元或成长池现金流0.8变成共同淘汰条件。不负责自动抓取七组规范事实，也不构成历史时点回测：当前审核状态不是过去审核历史的重建。
