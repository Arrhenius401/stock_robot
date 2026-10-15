# 股票共享候选与证据审核使用说明

日期2026-10-15。此入口把研究资料存进可持续审核的本地SQLite；候选不是正式成员，证据确认不是公司准入通过。本阶段没有成员发布命令，不改变现有ETF或空股票池页面。

## 导入与查看

使用项目.venv，在开发工作树需要设置PYTHONPATH为当前工作树src和根目录。db显式指定，可与现有radar.db共存stocks_前缀表；首次验收建议使用独立tmp数据库，不直接批准研究样本。

```powershell
stock-robot radar stock-records-import --kind candidates --input tmp/market-candidates.json --db tmp/stock-research.db --as-of 2026-10-14
stock-robot radar stock-records-import --kind review_packet --input tmp/dividend-workflow/review-packet.json --db tmp/stock-research.db --as-of 2026-10-14
stock-robot radar stock-records-list --db tmp/stock-research.db --search 长江 --page 1
stock-robot radar stock-records-list --db tmp/stock-research.db --symbol 600900 --output tmp/company-review.json
```

candidates接受公司对象列表或包含companies的对象；review_packet接受已有审核资料包。每条资料保留来源输入SHA、数据截点及原始字段，已有financial_verified等标志不赋予审核权。非有限原始金额保存为文字线索，不填零。所有候选admission_status仍pending_verification，不自动成为任何池成员。

重复同批导入幂等；新批补证与旧批留档共存。旧截点不会覆盖新截点公司展示资料；既有审核保留历史，不能把原文或字段变化后的新证据当成已审核。全库搜索默认每页15家，单公司详情包含证据ID、当前版本和审核历史。

## 补证与逐条审核

stock-evidence-add输入为：

```json
{
  "symbol": "600900",
  "kind": "financial",
  "payload": {"metric": "parent_net_profit", "fiscal_year": 2025, "amount_yuan": "需核对的原文金额", "scope": "reported_parent"},
  "source_url": "https://公司官方来源/原文.pdf",
  "disclosed_on": "2026-04-30",
  "content_sha256": "64位小写原文SHA256"
}
```

以上为格式说明，金额及hash占位符不能作为实际证据。应从真实留档材料复制来源、披露日、摘要及字段，不以每股分红或供应商总股本推算未明确的公司派息总额。

```powershell
stock-robot radar stock-evidence-add --input tmp/new-evidence.json --db tmp/stock-research.db --expected-revision 公司当前版本
stock-robot radar stock-evidence-review --db tmp/stock-research.db --symbol 600900 --evidence-id 证据ID --expected-revision 公司当前版本 --decision confirmed --reviewer 审核人 --reason 已核对的事实及口径
```

confirmed表示显式确认这一条原文事实；rejected表示驳回；needs_evidence表示仍需补证。确认必须有完整来源、披露日及原文摘要；只有供应商研究提示或失败文档不能空确认。每次审核保存审核人、理由、服务端时间和证据绑定，不覆盖旧记录。

每次写入使用公司revision校验；版本过期会拒绝，需重新查看最新资料，不能覆盖另一页面已经保存的审核。确认满90天显示expired；源披露日不能冒充审核日，接口恢复或重新导入不能刷新旧审核时间。

这些决定不会修改数值硬门槛，也不会将reported_parent自动变为company_ordinary。后续共同准入、风格及机会引擎仍须独立检查数据与规则；首批正式关注成员仍通过用户选择和发布形成。

## 数据安全与边界

SQLite使用stocks_专表和独立迁移版本，不更改ETF表或数据库全局user_version；写事务整批提交，错误资料不能半批留存。证据和审核长期保留，无自动删除。输入／导出文件不能覆盖数据库及其日志文件。

本阶段不采集新的行情、不发送外部通知、不注册自动任务；没有浏览器／数据库服务等新运行组件。真实审核资料可能包含过期资料或未核实口径，导入成功仅表示留档成功。
