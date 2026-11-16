# 两池已确认风格条件实施计划

日期：2026-10-15。承接共同准入提交10ad057及已确认完整股票池方案。用户已授权持续执行、不逐小项确认。本次沿用design-before-implementation技能执行已确认规则，不批准未确认阈值，不新增运行依赖、不改UI。

## 范围与接口

新增radar.stocks.style.evaluate_stock_styles(symbol,evidence,as_of,*,market_window=None)，返回共同准入和dividend_value/growth_trend两个独立研究结果。新增stock-style-evaluate CLI，复用stock_admission.evaluate_store的来源修订校验、单公司隔离、文件保护、JSON输出。原stock-admission-evaluate行为保持不变。所有计算只消费StockResearchStore已审核证据，不改成员，不写最终公司资格，不隐式自动更新。

共享源验证复用admission._groups，将kind前缀及名称参数化，默认原七组不变；数值、日期验证复用现有纯函数。金额求和使用dividend_payout._sum_amounts扩展Decimal精度；比较用交叉相乘，不依赖四舍五入后的显示比率。研究市场窗口仍只是研究上下文，不能用作正式发布。

## 规范事实

七组admission_*仍按已有约定。新增style_*仅消费显式审核、归属一致、来源完整、截点有效、审核90天内且不冲突的组：

- style_cash_flow：issuer_type=ordinary、currency=CNY、scope=company_consolidated、parent_profit_scope_reconciled=true；annual恰为最近三个完整年度，每行year、operating_cash_flow_yuan、disclosed_on。分母只能来自同公司已审核admission_profitability中的三个完整年度parent_profit_yuan，currency=CNY、scope=company_parent、每年严格正值且披露在年度结束后/截点前。不得另填一个有利分母，不能把经营现金流负当利润负。缺失或重复年度、口径未核实均不计算阈值。
- style_cash_review：issuer_type、verdict=clear/failed/pending、basis非空。普通企业review核验现金恶化、应收存货、资本开支债务及支撑；金融与已审核security issuer_type一致，专项核验，不套0.8。普通企业数值阈值passed不替代专项review。成长cash review的非clear仅提示继续核验，不依据未确认量化风险线自动淘汰；共同财务专项硬失败仍由共同准入处理。
- style_dividend_history：currency=CNY、scope=company_all_shares、share_classes_complete=true、coverage_through=研究截点；annual恰为三个完整年度，每行year、implemented_cash_yuan、coverage_complete=true、disclosed_on，正金额须有latest_paid_on且在该财政年度开始后/截点前。记录为按财政年度归属、已实施、全股类汇总并换成人民币的实际现金；0必须是完整覆盖核验的没有实施分红，未知不填0；负值无效。连续分红只验证每年实际额>0，不强制逐年增长，不将该组作为已核实支付率。支付率仍待配置。
- style_liquidity：currency=CNY、scope=a_share_raw_turnover；daily恰为market_window的60个唯一市场交易日{date,amount_yuan}。原始成交金额（元）、非负有限值、缺失不补0，不以个股最近60条替换市场窗口。

## 规则及结果

1. 普通红利价值：三年OCF合计/同期间归母利润合计>=0.8为已确认硬条件；等于通过，低于失败。另现金专项review clear才完成已确认现金核验。金额或范围未知则pending，不允许clear覆盖硬数值。金融不计算该比率，采用类型匹配的现金专项review；未审核不通过。
2. 成长：显示同一现金比率但不以0.8作资格硬线，低于为attention，保留研究资格。现金专项未clear为attention/pending，不因该提示替代共同风险核验。不新增未确认的应收、存货或债务数值。
3. 红利分支：三年均有已实施分红为已确认条件，零分红则该分支失败；缺证据待核验。不强制金额逐年增长。价值分支不要求连续分红；任何红利分支失败不自动导致整个红利价值池失败。两分支仍因支付率/估值等未配置保持pending_configuration，已确认现金失败可令两分支failed。
4. 流动性日均>=1亿元为已确认参考。红利价值低于仅warning不取消资格；成长低于机会observe，高于只代表该单项passed，整体机会仍unavailable（其他阈值未确认）；缺失为unavailable。不因流动性改变共同准入。
5. 每池返回checks（code/outcome/reason/evidence_ids/values/role），approved_conditions_status、style_status、admission_status、opportunity_status、unconfigured_rules；分支有独立结果。common硬失败作用两池；否则未配置最终仍pending_verification。formal_screening_enabled=false、members_published=0。没有任何ready/publish路径。
6. 计算中revision变动/单公司失败时所有嵌套池资格、style与机会结果均失效，不只修改顶层共同状态。不能返回混合时点有效结果。保留来源IDs用于排查。

## 实施及验证

先写引擎边界测试：0.8等于/上下、现金与利润符号区分、分母与共同事实绑定、普通/金融分支、现金review不覆盖失败、成长低比率只提醒、三个年度分红不递增通过、零与未知/未实施/未来/重复、红利失败但价值保留、60日完整/缺失/重复/偏早、1亿元精确边界、来源审核/冲突/过期、全已确认单项通过但正式仍关闭。

实现代理只改style.py及test_stock_style.py；主会话改共享组接口、CLI及集成测试、检查映射、使用文档。S级双审查，修复后定向复审。CLI合成真实SQLite验证现金硬失败、两池分歧与计算期间失效；685真实库仍应全部待核验且无入池。最终相关范围verify及git diff --cached --check后整体本地提交，不推送、不重复全量回归和UI验收。
