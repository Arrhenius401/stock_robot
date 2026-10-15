# 评估档案与成员草案使用说明

日期2026-10-15。此功能是本地研究留档及关注变更准备，尚未接入正式管理页面和成员发布。使用同一个研究SQLite数据库，新增独立stocks表，不改变ETF记录。

## 计算并留档

```powershell
stock-robot radar stock-evaluation-capture --db PATH --as-of 2026-10-15
stock-robot radar stock-evaluation-list --db PATH
stock-robot radar stock-evaluation-list --db PATH --evaluation-id ID --output evaluation.json
```

capture始终由服务端读取候选/证据、重新计算共同及两池风格，不接受外部passed报告。可加 `--symbol CODE` 只研究一家；市场窗口通过可选 `--market-window PATH.json` 提供，规则同stock-style-evaluate。输入输出禁止覆盖数据库、其日志或市场窗口输入。

每份档案包含完整报告、当时的公司和证据原文资料、最新审核身份及决定、资料修订号、规则版本、研究截点和市场窗口来源。相同内容重复归档复用同一ID和首次保存时间；新证据/新审核不会覆盖旧档案。

`status=complete` 仅表示这一批计算执行完整，不表示股票合格。部分失败保留为独立partial档案，不覆盖旧complete，也不能用于成员草案。`freshness.current=true` 只表示研究来源/日期/规则仍适用，不是入池批准。公司资料、全范围名录、规则、研究日期或审核90天有效期变化，会动态标示失效；历史原文仍保留。该功能不重建过去时点的审核历史，不构成股票回测。

## 保存新增关注草案

准备JSON文件：

```json
{
  "evaluation_id": "替换为实际档案ID",
  "changes": [
    {"pool_id": "dividend_value", "symbol": "600900", "action": "add", "reason": "填写自己的关注理由"}
  ]
}
```

以上仅为字段示例，不是对该公司资格或入选的认定。

```powershell
stock-robot radar stock-member-draft-create --db PATH --input changes.json --request-id my-request-1 --actor 本地用户
stock-robot radar stock-member-draft-list --db PATH
stock-robot radar stock-member-draft-list --db PATH --draft-id ID --output draft.json
stock-robot radar stock-member-draft-cancel --db PATH --draft-id ID --expected-revision 1 --actor 本地用户 --reason 撤销关注
```

股票池目录缺省 `config/radar_stock_pools`，可用 `--pool-dir PATH` 指向实际定义。池必须存在且启用，并有对应风格计算结果；新增一个池名字不会自动获得规则。草案导出不能写入读取中的池配置目录，避免覆盖定义。

创建时要求引用当前、完整的研究档案。每项change只允许pool_id/symbol/action/reason，当前action只支持add；同池同公司不能重复。任意一项无效则整批不保存。相同request-id及相同规范内容重放不会重复创建；同request-id异内容报冲突。资料变化后重放只读回原草案和当前失效标记，不恢复资格。

草案revision从1开始，状态blocked，保存逐项资料版本、理由和阻碍（例如共同条件待核验、风格未配置）。撤销须携带当前revision，追加操作人、时间和理由，历史不会删除。旧revision不能再次撤销。操作人字段是本地审计署名，不是身份认证或入池权限。

当前始终 `publication_enabled=false`、`members_published=0`，没有发布命令，也没有写正式成员的入口。草案保存不会把公司放入股票池。成员发布、暂停、退出、恢复、后台更新及对应管理页面仍是后续任务。
