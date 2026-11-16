# 两池已确认风格条件研究使用说明

日期：2026-10-15。共同准入入口保持原样；新入口：

```powershell
stock-robot radar stock-style-evaluate --db PATH --as-of 2026-10-15 --output styles.json
```

可用 `--symbol CODE` 只计算一家，或用 `--market-window window.json` 提供已核验的60个市场交易日研究上下文。数据库和输出保护、审核有效期、修订竞争检查与共同准入入口一致；该命令仍然不发布成员。

## 事实准备

用 `stock-evidence-add` 加入下面的规范事实，再用 `stock-evidence-review` 显式审核。证据必须有来源URL、原文SHA256、披露日期、公司归属；输入内自带verified或clear不能代替审核。来源/审核过期、同组冲突、期间或口径缺失均待核验，不填0。

| kind | 主要内容 |
| --- | --- |
| style_cash_flow | 普通企业三个完整年度合并经营现金流，已核对归母利润比较口径 |
| style_cash_review | 现金及分红支撑专项依据，issuer_type须与证券事实一致；金融单独核验 |
| style_dividend_history | 三个完整财政年度实际已实施全股类现金分红人民币汇总及完整覆盖 |
| style_liquidity | A股原始日成交额，逐日匹配60个市场交易日 |

详细字段见[实施计划](../../plans/2026-10/2026-10-15-stock-radar-approved-styles.md)。现金比率的分母只取同公司已审核 `admission_profitability` 的年度归母利润，不能另填一个有利分母；原始报告解析建议仍须人工核验规范化。

示例现金流payload（合成字段示例，不是实际公司事实）：

```json
{
  "issuer_type": "ordinary",
  "currency": "CNY",
  "scope": "company_consolidated",
  "parent_profit_scope_reconciled": true,
  "annual": [
    {"year": 2023, "operating_cash_flow_yuan": "80", "disclosed_on": "2024-04-01"},
    {"year": 2024, "operating_cash_flow_yuan": "80", "disclosed_on": "2025-04-01"},
    {"year": 2025, "operating_cash_flow_yuan": "80", "disclosed_on": "2026-04-01"}
  ]
}
```

## 如何理解结果

- 普通企业红利价值池：三年经营现金流合计/三年归母利润合计>=0.8为硬条件，另须现金专项核验；金融不套该比率，用类型匹配的专项事实。
- 成长趋势池：同一比率只是质量指标，低于0.8提示关注，不因此淘汰。未确认的应收/存货/偿债数值触发条件不启用；共同资格里的真实财务风险仍独立检查。
- 红利分支：三个完整财政年度均有已实施现金分红，不要求逐年增加。没有实施分红必须有完整覆盖证据，未知不当作零。价值分支不强制连续分红，红利分支失败不会自动淘汰价值分支。
- 60日日均原始成交额低于1亿元：红利价值显示提醒；成长趋势的当前机会继续观察。均不直接取消公司资格，资料不足则机会暂不可判断。

`pools` 包含 dividend_value 与 growth_trend，分别返回检查、已确认条件状态、风格状态、资格状态、机会状态及待配置规则。红利价值还保留 dividend/value 两分支。`approved_conditions_status=passed` 只代表本阶段已确认条件完成，不代表完整风格合格或允许发布。

未确认的支付率、股息率、估值、成长及趋势参数保持待配置；共同通过、现金和流动性单项通过都不等于当前机会成立。`formal_screening_enabled=false`、`members_published=0` 始终保持。版本变化或单家公司异常时嵌套结果全部失效，旧数值仅供排查。该命令没有自动采集、自动成员轮换或生产发布功能，也不重建过去时点的审核历史。
