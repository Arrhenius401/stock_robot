# 第一阶段：年报建议到规范财务事实的接通计划

日期2026-10-15，基于8f04ebc，属于已确认四阶段中的第一阶段数据/规则补齐，非新增交付阶段。继续执行design-before-implementation技能和用户无需逐小项确认的授权。本次完成保留来源的三年财务规范组准备/待审保存，不增加投资门槛、不批准来源、不把年报当TTM。

## 输入与输出

新增radar.stocks.financial_facts.prepare_financial_facts(symbol,evidence,as_of)->dict。输入为StockResearchStore.list_evidence真实结构。只消费本公司financial_proposal：confirmable=true、公开HTTPS/domain/SHA64、披露<=截点、fields与origin公司/URL/披露/file_sha一致、origin/fields.status=matched_template，fiscal_year为最近3完整年度、CNY，parent/deducted scope=reported_parent，OCF/revenue scope=consolidated，金额admission._amount校验且不丢Decimal精度；年度披露须在年度结束后。明确rejected/needs_evidence/expired或审核未来/无效者不采用；无审核建议可以整理，但不能因此自动确认。保留负值/零，不把未知填0。

按年度/metric合并数值相同来源，Decimal等值不同格式为相同；不同数值冲突则该值None并列全部来源，不选最新/有利的一份，也不自动解释重述。每项保留evidence_ids、PDF URL/hash、页码、excerpt、原始单位和审核身份；最新年度与比较年度分别归属，不依赖proposal_id而取数据库evidence_id。财务field声明不替代用户核对范围。

结果含version/as_of/symbol/facts/annual_summary/unresolved/requires_review=true/financial_verified=false/formal_screening_enabled=false/members_published=0。facts仅在至少有一项相应年度有效值/冲突时产生：
- admission_profitability payload currency=CNY、scope=company_parent、三年annual{year,parent_profit_yuan,deducted_parent_profit_yuan,disclosed_on}、ttm=null；TTM明确未提供，不以年报代替。reported_parent到company_parent是待审规范映射，不构成机器确认。
- style_cash_flow payload issuer_type仅从有效已审核admission_security取得（否则null）、currency=CNY、scope=company_consolidated、parent_profit_scope_reconciled=false、三年annual{year,operating_cash_flow_yuan,disclosed_on}。金融已知类型不伪装ordinary；cash ratio不会因简单确认而通过口径条件。

fact外层kind/payload/source_url/disclosed_on/content_sha256。顶层URL/hash确实对应依赖中披露最新的一份原PDF，不拿合成JSON hash冒充PDF；各字段真实原文在payload来源映射中完整保存。若无合法原文则不产生fact。

## 来源依赖与存储

每个payload保留保留字段 `_financial_dependencies`={version:1,as_of:研究截点,inventory:[{evidence_id,review_id}]}，inventory覆盖该公司全部financial_proposal（包括未用/驳回/未知），按ID排序。新增同类建议或任一审核变化即失效，不让旧组绕过后来的修订/驳回。现金事实另保存使用的证券来源快照（若有）；验证不允许跨公司ID。依赖来源字段仅使用数据库真实ID/latest_review.review_id，无review记null。

StockResearchStore新增add_financial_fact_batch(symbol,facts,*,expected_revision)，在同一BEGIN IMMEDIATE事务中验证kind只限上述两种、每种唯一、严格保留依赖、inventory与本公司数据库当前集合/审核ID一致，source URL/hash须属于列出的合法原文；组资料由服务端准备，CLI不导入外部passed JSON。原文字段及来源映射仍在payload，保存调用现有_evidence/insert方法。所有新增组为待核验，绝不添加review。整个batch要么成功要么失败；新增时公司revision一次递增，同内容再次保存复用不重复新证据（仍要求当前expected_revision）。

现有add_evidence遇到该保留依赖字段也要进行相同验证，不能通过通用入口绕过。save_review在confirmed前核对依赖仍有效，失效不能确认。list_evidence对已经审核的派生组动态检查依赖；若inventory/审核变化，review_status=needs_evidence并附lineage_status=stale，保留旧审核历史但不给引擎使用；未审核的组也标明失效。不因非财务提示新增而无条件失效，不改变普通手工证据无依赖时的现有行为。字段名保留给服务器来源绑定，不作为用户verified权限。

## CLI

新增stock_robot.stock_financial_facts，命令stock-financial-facts-prepare --db --symbol --as-of [--save --expected-revision N] [--output]。默认只读准备；save必须显式带expected-revision，由同一次服务端读取/准备产生fact批次，再原子保存待审。输出公司revision、事实组/未解决事项及可审核证据ID。DB家族文件保护复用既有帮助，不覆盖源文件；此入口不接任意fact JSON、不发网络请求。

## 验证与审查

实现代理只写financial_facts.py及test_stock_financial_facts.py，父写research_store依赖校验、批次和CLI/集成测试、verify映射、使用说明。测试先行，S级规格+质量双审查与定向复审。覆盖来源/身份/日期/币种/口径/坏类型/负零/Decimal/比较年度、重复等值合并/冲突None、未审核仍待审、TTM不回填、cash口径false、跨公司与原文伪造拒绝、依赖新review/新增建议失效、原子CAS、旧手工证据兼容。

真实库685家公司仍0审核，不代用户确认实际财务；对已有长江电力年报资料先只读准备并报告覆盖/冲突。若需保存验收，用独立合成库验证待审核保存、显式确认后仅年度检查可用而TTM/现金口径仍待核验、上游审核变化失效。相关范围verify Changed Staged及diff --cached --check通过后本地提交，研究产物留tmp，不推送、无UI修改不重复浏览器验收。此项完成仍不代表第一阶段全部完成：最新季度/TTM、行情/行业等接通与完整风格阈值尚待后续。

补充审查收敛：保留来源依赖的事实须按dependencies.as_of从当前数据库原始建议和证券来源重新生成，并精确核对完整规范payload及主原文元数据；仅核对field_sources不足以防金额/TTM篡改。无依赖的既有手工事实保持原行为，派生组不允许沿用年报依赖补造TTM或自动勾选现金口径。

<a id="historical-2026-10-15-stock-radar-financial-facts-validation"></a>

## 第一阶段年报财务事实接通验收记录

> 2026-11-24 文档整理：本节合并自 `2026-10-15-stock-radar-financial-facts-validation.md`，保留原历史记录；以下结果仅适用于所述日期和代码状态，不代表当前验收结果。

日期2026-10-15，基于8f04ebc。四阶段中的第一阶段子项，不是新增交付阶段，也不是第一阶段全部完成。复用独立工作树/共享venv，无新增依赖、无网络采集、无UI或会话改动。

### 交付及不变量

新增financial_facts纯准备器：读取数据库financial_proposal，按三完整年度/指标整理规范利润与现金流组，保留逐项原文/页码/PDF URL/SHA256/原始金额和审核版本。等值合并、冲突None、不选有利版本；TTM始终null，现金parent_profit_scope_reconciled始终false，issuer未知不当普通企业。科学计数在展开超100字符时保留精确表示，仍可被下游_amount解析。

StockResearchStore新增add_financial_fact_batch。保存事务内CAS、整批原子、同内容复用且不增加review；通用add/confirmed review/list均对保留的_financial_dependencies按as_of从当前数据库重建整个事实，精确比较规范金额/年度/披露/TTM/口径/全部来源。上游新增建议或review变化、驳回/自然到期使旧组needs_evidence/stale；不相关research_hint不使该组失效。既有无保留依赖的手工事实沿用独立来源审核，不给派生组借原文改金额的入口。

新CLI stock-financial-facts-prepare默认只读，--save必须显式expected-revision，由服务器同次读取/准备再原子保存待审。不接受外部passed JSON，不自动确认、不发布成员，DB及日志输出保护。

### 检查与审查

测试先行：存储新行为11项失败复现缺口，新CLI注册前7失败2通过；实现并增加完整准备到保存、派生金额/TTM/披露/现金scope篡改、冲突值、上游失效及科学计数回归。

执行 `scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：Ruff通过，Pyright 0错误，PowerShell语法检查通过，pytest **324项通过（56.83秒）**，验证脚本成功清理临时目录。无未映射文件，覆盖新准备器48项、来源存储17项、新CLI10项、年报提取、共享研究存储/CLI、评估档案、成员草案、ETF存储及verify/CLI相关范围；不是全量回归。

规格与质量双审查最终PASS。关闭两项实质接通问题：部分年度field_sources=[]可表示未知并正常保存；不再仅检查引用而遗漏规范实值，改为事务内完整重建以拒绝金额/TTM/口径篡改。质量另做348次坏类型动态变异无未捕获异常，并修复1e-100/1e-1000合法源值展开后下游拒绝的问题。使用说明及检查映射已复审，无剩余审查问题。

首次CLI实现命令的自动审批发生一次超时，确认未执行后按允许范围重试成功；没有安全审批阻断或权限扩大。

### 实际资料只读验收

正式stock-robot.exe入口、PYTHONPATH指向工作树，执行 `radar stock-financial-facts-prepare --db tmp/stock-research-acceptance/final.db --symbol 600900 --as-of 2026-10-15 --output tmp/stock-research-acceptance/financial-facts-cypc.json`。

读取24条上游财务建议，2023/2024/2025各年归母、扣非归母、OCF及收入共12项available，形成admission_profitability/style_cash_flow两待审组，saved=null、requires_review=true。14条未解决/未采用诊断包含非目标年份建议，不能解读成14项经营风险。库仍685公司、772证据、0审核、3批次，无实际数据保存、确认或入池。available仅表示建议可整理，不代表财务已审核。

### 正式命令合成保存验收

独立tmp/stock-research-acceptance/financial-facts-executable-probe合成库，通过正式exe --save+expected-revision保存2个待审事实组。仅在该合成库显式确认利润组后，annual_parent_profit_2023=passed，ttm_parent_profit=pending_verification，最终admission_status=pending_verification。随后把一条上游原文审核改needs_evidence，派生组立即review_status=needs_evidence、lineage_status=stale，成员发布始终0。合成数值不是实际公司事实或用户首批关注选择。

研究/合成DB、导出及失败诊断保留忽略tmp目录，不提交。最新季度/TTM、行情/行业接通、未确认选股阈值、正式发布/风险生命周期、后台更新及管理UI仍待后续；本轮未完成第一阶段。未验证全市场正式达标数量、完整采集耗时或内存峰值，未重复无改动的浏览器验收。不推送、合并或发布。
