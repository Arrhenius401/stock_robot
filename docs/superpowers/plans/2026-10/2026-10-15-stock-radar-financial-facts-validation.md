# 第一阶段年报财务事实接通验收记录

日期2026-10-15，基于8f04ebc。四阶段中的第一阶段子项，不是新增交付阶段，也不是第一阶段全部完成。复用独立工作树/共享venv，无新增依赖、无网络采集、无UI或会话改动。

## 交付及不变量

新增financial_facts纯准备器：读取数据库financial_proposal，按三完整年度/指标整理规范利润与现金流组，保留逐项原文/页码/PDF URL/SHA256/原始金额和审核版本。等值合并、冲突None、不选有利版本；TTM始终null，现金parent_profit_scope_reconciled始终false，issuer未知不当普通企业。科学计数在展开超100字符时保留精确表示，仍可被下游_amount解析。

StockResearchStore新增add_financial_fact_batch。保存事务内CAS、整批原子、同内容复用且不增加review；通用add/confirmed review/list均对保留的_financial_dependencies按as_of从当前数据库重建整个事实，精确比较规范金额/年度/披露/TTM/口径/全部来源。上游新增建议或review变化、驳回/自然到期使旧组needs_evidence/stale；不相关research_hint不使该组失效。既有无保留依赖的手工事实沿用独立来源审核，不给派生组借原文改金额的入口。

新CLI stock-financial-facts-prepare默认只读，--save必须显式expected-revision，由服务器同次读取/准备再原子保存待审。不接受外部passed JSON，不自动确认、不发布成员，DB及日志输出保护。

## 检查与审查

测试先行：存储新行为11项失败复现缺口，新CLI注册前7失败2通过；实现并增加完整准备到保存、派生金额/TTM/披露/现金scope篡改、冲突值、上游失效及科学计数回归。

执行 `scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：Ruff通过，Pyright 0错误，PowerShell语法检查通过，pytest **324项通过（56.83秒）**，验证脚本成功清理临时目录。无未映射文件，覆盖新准备器48项、来源存储17项、新CLI10项、年报提取、共享研究存储/CLI、评估档案、成员草案、ETF存储及verify/CLI相关范围；不是全量回归。

规格与质量双审查最终PASS。关闭两项实质接通问题：部分年度field_sources=[]可表示未知并正常保存；不再仅检查引用而遗漏规范实值，改为事务内完整重建以拒绝金额/TTM/口径篡改。质量另做348次坏类型动态变异无未捕获异常，并修复1e-100/1e-1000合法源值展开后下游拒绝的问题。使用说明及检查映射已复审，无剩余审查问题。

首次CLI实现命令的自动审批发生一次超时，确认未执行后按允许范围重试成功；没有安全审批阻断或权限扩大。

## 实际资料只读验收

正式stock-robot.exe入口、PYTHONPATH指向工作树，执行 `radar stock-financial-facts-prepare --db tmp/stock-research-acceptance/final.db --symbol 600900 --as-of 2026-10-15 --output tmp/stock-research-acceptance/financial-facts-cypc.json`。

读取24条上游财务建议，2023/2024/2025各年归母、扣非归母、OCF及收入共12项available，形成admission_profitability/style_cash_flow两待审组，saved=null、requires_review=true。14条未解决/未采用诊断包含非目标年份建议，不能解读成14项经营风险。库仍685公司、772证据、0审核、3批次，无实际数据保存、确认或入池。available仅表示建议可整理，不代表财务已审核。

## 正式命令合成保存验收

独立tmp/stock-research-acceptance/financial-facts-executable-probe合成库，通过正式exe --save+expected-revision保存2个待审事实组。仅在该合成库显式确认利润组后，annual_parent_profit_2023=passed，ttm_parent_profit=pending_verification，最终admission_status=pending_verification。随后把一条上游原文审核改needs_evidence，派生组立即review_status=needs_evidence、lineage_status=stale，成员发布始终0。合成数值不是实际公司事实或用户首批关注选择。

研究/合成DB、导出及失败诊断保留忽略tmp目录，不提交。最新季度/TTM、行情/行业接通、未确认选股阈值、正式发布/风险生命周期、后台更新及管理UI仍待后续；本轮未完成第一阶段。未验证全市场正式达标数量、完整采集耗时或内存峰值，未重复无改动的浏览器验收。不推送、合并或发布。
