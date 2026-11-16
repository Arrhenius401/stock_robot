# 股票共享候选与证据审核持久化验收

日期2026-10-15。对应实施计划 `2026-10-15-stock-radar-research-persistence.md`。用户选择完整股票池并授权持续推进；本阶段把已有离线研究资料接入持久化共享候选／证据审核，不代表完整股票池交付。

## 完成范围

新增StockResearchStore和四个radar CLI：stock-records-import、stock-records-list、stock-evidence-add、stock-evidence-review。stocks_专表保存公司、来源批次、不可变证据和追加审核；独立schema版本、不变更ETF表或PRAGMA user_version；WAL、5秒锁超时、BEGIN IMMEDIATE及公司revision CAS。导入整批原子、同批幂等、旧截点不覆盖新资料；缺名称补证保留已知名称，当前名称优先。

来源／字段变化产生新证据，旧审核不转移；资料自带financial_verified/status不具批准权。明确跨公司归属整批拒绝，空建议和失败文档不能确认。确认需完整来源、日期、摘要及显式审核人／理由；满90天过期，未来审核不生效。公司准入仍pending_verification，未添加成员发布入口或自动任务。CLI输入／输出保护db及其WAL／SHM／journal（含真实路径）。

## 真实验收

从已有market-candidates.json导入685家公司。首次真实导入因302132中航成飞被旧前缀拒绝，整批无资料写入。随后从[深交所披露公告](https://disc.static.szse.cn/disc/disk03/finalpage/2025-04-29/e7ca3d43-4a5a-4c76-bb42-ad4f95dddb40.PDF)确认有效代码并修复校验，新增回归。这只确认身份，不代表符合准入。

最终新建独立tmp/stock-research-acceptance/final.db，经真实项目CLI导入685候选、三公司分红审核包、长江年报审核包，结果公司685、证据772、来源批次3、审核0；全体admission_status=pending_verification。未替用户确认实际股票事实。旧验收库重复相同候选及分红资料包，新增公司／证据均0、reused=true。全库搜索长江返回2家公司，600900详情含47条证据，JSON导出成功。数据和原始库都留忽略tmp，不提交。

StockPoolRepository检查红利价值池及成长趋势池items均为空；研究资料不是成员。合成证据审核、stale revision、90天和并发竞争由真实SQLite回归覆盖，不在实际股票研究库伪造批准记录。

## 审查和检查

独立规格及质量审查复核通过；修复跨公司entry/origin/fields归属检查、空建议确认、大整数分页、连接PRAGMA初始化失败关闭等边界。最后名称保留缺陷由独立合成数据复现（新包仅代码曾将长江电力名称替换为600900），主会话局部修复并补两类导入／当前名三回归。

范围门禁命令 `.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：Ruff通过、Pyright 0 errors／0 warnings／0 informations，149个相关用例通过（29.32秒）。名称修复后仅复核受影响store/CLI，48个用例通过（2.57秒），源及测试Ruff、共享运行时Pyright通过；其余同状态104项复用，最终相关回归合计152项通过。`git diff --cached --check`通过。不运行全量、不重复无关浏览器验收。

先前检查异常已区分：新增CLI导入排版I001经Ruff修复；首次类型检查暴露可空get_company和故意异常JSON测试推断过窄，使用断言helper和诚实Any标注修复；真实302代码失败未发生部分写入。失败诊断留tmp；成功唯一测试目录按规范清理。无前端／SSE变更，无新运行依赖。

## 后续边界

股票准入和风格／机会计算、全市场正式发现服务、成员审核发布、暂停／退出／恢复、任务及正式管理页面仍需接入。已确认数值与未确认数值继续区分，正式筛选保持关闭。个人逐条确认事实不替代确定性硬条件，不产生可入池结论。本阶段数据可供接下来的引擎与管理接口使用。
