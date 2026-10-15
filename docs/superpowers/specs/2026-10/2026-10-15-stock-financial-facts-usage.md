# 年报建议到待审规范财务事实

日期2026-10-15，属于四阶段中的第一阶段数据接通。此功能整理已有年报建议，不增加网络采集或投资门槛。

## 准备与保存

```powershell
stock-robot radar stock-financial-facts-prepare --db PATH --symbol 600900 --as-of 2026-10-15 --output prepared.json
```

默认只读。命令从研究库读取原始financial_proposal，整理最近三个完整财政年度的归母利润、扣非归母利润、经营现金流和收入。每项都保留数据库证据ID、实际PDF URL/SHA256、页码、原文、单位及审核身份；原始金额格式与规范Decimal金额分开保存。

输出facts可包含admission_profitability和style_cash_flow；annual_summary供逐年度查看覆盖情况。等值来源合并，不同数值冲突保持None；不挑最新或有利的一份。不适用年份/无效来源会列未采用诊断，不能把诊断数量当作公司经营风险数量。整理结果仍requires_review=true、financial_verified=false，尚无原始建议时不会伪造事实。

如需保存，用准备结果里的source_revision作版本：

```powershell
stock-robot radar stock-financial-facts-prepare --db PATH --symbol 600900 --as-of 2026-10-15 --save --expected-revision N
```

该命令重新读取并准备，再在同一事务校验版本和来源。它不接受外部passed JSON；版本变化或任何组无效整批失败，不留下半份组。保存只增加待审证据，不增加审核记录或正式成员。相同内容重存复用证据，但仍须当前版本。

## 审核与失效

保存结果saved.evidence_ids提供可查看的证据ID；使用现有stock-records-list查看完整来源，再通过stock-evidence-review显式审核。saved.revision只代表该次保存后的公司版本，每次审核后版本继续递增，应重新读取后再操作下一条。

利润年度规范映射须核对归母范围；TTM始终为null，不用年度利润替代最新TTM。现金组parent_profit_scope_reconciled始终false，发行人类型只从有效审核过的证券事实取得，否则未知。简单确认年报组不会自动完成现金与归母利润口径核验。完整TTM及额外口径事实须按实际证据独立准备，不能沿用年报来源填造。

payload._financial_dependencies为服务器保留字段，含准备截点as_of、该公司全部上游财务建议及其审核版本、实际采用的证券来源。保存、确认和读取时从当前数据库原文重新准备并精确核对整个结果；保留原文引用却修改金额、披露日、TTM或现金口径会被拒绝。既有无该保留字段的手工事实仍按独立来源及审核流程管理。

上游新增建议、审核变化、驳回或自然到期，均可能使旧组lineage_status=stale、review_status=needs_evidence。旧审核历史不删除，但不再供规则放行；需重新准备并复核。不相关的普通提示不会使财务组无条件失效。

命令输出禁止覆盖SQLite数据库及其日志。实际研究数据和导出文件留本地，不自动审核、发送或发布。formal_screening_enabled=false、members_published=0始终保持。本项未完成最新季度/TTM采集、完整行情及行业接通，也未完成第一阶段全部工作。
