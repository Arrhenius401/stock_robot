# 已确认共同准入条件计算实施计划

日期2026-10-15。承接完整股票池已确认设计及持续推进授权，紧接持久化提交798c21e。只实现已确认共同条件的确定性计算，不替用户批准未确认风格／机会阈值，不发布成员。

## 一、输入与来源约束

新增radar.stocks.admission纯计算模块和stock_robot.stock_admission CLI服务。输入来自StockResearchStore的当前证据及逐条审核，研究提示/输入自带verified不具效力。只消费明确kind的规范事实组：admission_security、admission_capitalization、admission_profitability、admission_audit、admission_governance、admission_leadership、admission_financial_quality。其他kind忽略，不把解析建议自动拼成已核实事实。

每组证据须review_status=confirmed、latest_review为confirmed且reviewed_at不晚于评估日结束、距评估日不足90天；source disclosed_on不晚于评估日，HTTPS+hash完整，symbol一致，confirmable=true。服务只从数据库读这些状态，CLI不能传入任意passed结论。无有效组为待核验，失效/驳回不能当零。多个有效不同payload同组冲突为待核验，不擅自挑一个；相同payload多来源可合并。

CLI `stock-admission-evaluate --db PATH --as-of YYYY-MM-DD [--symbol CODE] [--market-window JSON] [--output PATH]`。无symbol评估全共享候选。market-window为显式研究用市场日期列表（60个已核验市场交易日，严格递增且最后日不晚于截点），不推断工作日；未提供则市值窗口待核验。该文件/研究上下文不是正式发布授权，后续生产任务需绑定官方交易日历版本，不能以任意JSON作为发布时行情时点。输入／输出保护DB及日志，输出JSON不可覆盖market-window。

服务读取每公司revision前后校验，资料变化则该公司返回待核验并提示重试，不保存错误的稳定结论。此阶段输出独立研究报告，保留source_revision、as_of、evidence_ids与rule_version，不覆盖StockResearchStore.admission_status或成员；风格未配置时，即使共同条件passed，最终admission_status仍pending_verification，formal_screening_enabled=false。共同硬条件failed可在研究报告表示excluded，不能贴无依据造假标签。

## 二、规范事实及计算

所有金额为人民币元，可接受有限数值字符串，bool/非有限值/空值/不合理数量级为未知，不填0。只用Decimal，求和精度复用已验证支付率模块工具。规范组payload即该组数值与判断，来自显式事实审核，不允许用verdict覆盖下述硬数值。

1. admission_security：exchange=SSE/SZSE、security_type=ordinary_a、a_listed_on=YYYY-MM-DD、issuer_type=ordinary/bank/securities/insurance；未知型待核验，明确其他证券或市场排除。上市按完整3周年，2月29日目标非闰年采用2月末；未来上市日期为无效资料，不贴失败经营标签。
2. admission_capitalization：currency=CNY、scope=company_all_shares、share_classes_complete=true；current={date,amount_yuan}，daily=[{date,amount_yuan}]。current日期必须等于所供市场窗口末日，daily必须逐日唯一完整匹配60个窗口日期；负市值无效。当前和60日平均均>=20000000000才pass，任何有效已核实项低于则fail，即使另一项缺失也保留确定性失败；缺窗口不以更早60条代替。
3. admission_profitability：currency=CNY、scope=company_parent，annual=[{year,parent_profit_yuan,deducted_parent_profit_yuan,disclosed_on}]覆盖as_of.year-3..as_of.year-1唯一完整年度；ttm={period_end,disclosed_on,parent_profit_yuan,deducted_parent_profit_yuan,latest_announced:true}。已披露的每项均>0，0或负值fail；未知/重复冲突/未来披露不能放行。TTM必须提供独立明确记录，不能把年度利润当TTM；latest_announced需已审核，period_end<=disclosed_on<=as_of，年度也须在结束后披露。三完整年度与最新TTM盈利均为独立条件。
4. admission_audit：verdict=clear/failed/pending及非空basis，years恰为三个完整年度；非空basis说明意见与整改核验，缺期间不能通过。该组是明确的事实专项核验，不用标准意见替代治理。
5. admission_governance：verdict=clear/failed/pending、basis非空。failed仅表述治理核验未通过，不自动对外标监管造假。来源真实性仍须证据审核。
6. admission_leadership：verdict=clear/failed/pending，basis、industry_level1、industry_level2、business_scope非空。不按市值排名自动给龙头标签，不设未经确认的排名阈值。
7. admission_financial_quality：verdict=clear/failed/pending、basis、issuer_type必须与security一致，金融不可由普通企业事实替代。银行/证券/保险专项内容保留在basis及payload，不擅加监管数字。

结果checks为稳定code/outcome(passed/failed/pending_verification)/reason/evidence_ids/可选values。common_status任一有效硬fail为failed，无fail但未知为pending，全部条件pass才passed；audit/governance/leader/quality专项failed亦fail。common条件不包含1亿元流动性淘汰线，也不把成长池现金流0.8作为共同硬线；两池现金／风格／机会继续独立，正式筛选关闭。这一阶段不把普通股利润口径要求错误加到共同持续盈利，支付率普通股口径仍独立。

## 三、实现与验收

纯引擎由实现代理新增src/radar/stocks/admission.py、tests/radar/test_stock_admission.py，主会话写CLI/服务、集成测试与映射。接口 `evaluate_common_admission(symbol:str, evidence:list[dict[str,Any]], as_of:date, *, market_window:list[date]|None=None)->dict[str,Any]`。组从证据kind精确匹配，values与来源IDs入结果。evidence列表结构按StockResearchStore.list_evidence：symbol/kind/payload/source_url/content_sha256/disclosed_on/confirmable/latest_review/review_status；内部真实hash字段名以当前存储为准，切勿猜测。rule_version固定 `common-approved-2026-10-15-v1`，独立formal_screening_enabled=false，admission_status排除或待核验。

测试先行：200亿精确边界、60窗口缺失重复/更早日期/金额缺失、不用float，上市周年闰年，三个年度和TTM0/负值/缺失/非有限/未来/重复/币种口径，证明人工clear不能覆盖数字fail，过期/未来审核/证据归属冲突，定性组三年及金融分支、全pass仍不能直接入池、同组三份冲突不选优、未知无0。主会话CLI真实研究库685家公司无审核应全部待核验；合成完整核验数据验证共同pass、边界fail但不发布。双审查及相关门禁后独立本地提交，继续后续风格/发布/管理服务，不把本阶段称完整系统。

<a id="historical-2026-10-15-stock-radar-common-admission-validation"></a>

## 共同准入计算验收记录

> 2026-11-24 文档整理：本节合并自 `2026-10-15-stock-radar-common-admission-validation.md`，保留原历史记录；以下结果仅适用于所述日期和代码状态，不代表当前验收结果。

日期：2026-10-15。工作树：`codex/stock-radar-pools`，承接持久化提交798c21e。

### 交付范围

新增纯计算 `radar.stocks.admission.evaluate_common_admission`，只消费七类有效且显式审核的规范事实，逐项计算上市满三周年、当前及60市场交易日均值200亿元、三个完整年度及最新TTM归母与扣非归母利润为正，以及审计、治理、龙头、对应企业类型的财务专项。

新增 `stock-robot radar stock-admission-evaluate` 及SQLite服务：独立研究报告保留事实来源、规则版本和公司修订号；公司资料计算中变化则失效，单公司读取/计算失败隔离。未确认风格及机会参数仍待配置，正式筛选关闭，不写入成员或覆盖公司资格。

代码、命令、检查映射及使用说明按同一事项收敛提交。没有UI或会话改动，本阶段不重复既有真实浏览器/会话验收。

### 测试与审查

执行 `scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：Ruff通过、Pyright 0错误、PowerShell语法检查通过、pytest **237项通过（54.36秒）**，临时目录由验证脚本成功清理。检查计划无未映射文件，覆盖：

- tests/test_verify_script.py
- tests/radar/test_stock_admission.py（80项纯引擎断言）
- tests/radar/test_stock_admission_cli.py（16项真实SQLite/CLI集成）
- tests/radar/test_stock_research_store.py
- tests/test_cli.py、tests/test_cli_index.py、tests/radar/test_collector_cli.py

规格及质量双审查最终PASS。审查修复覆盖302代码、同日全股类市值相互矛盾、极端数值指数、单公司首次读取隔离、失败结果的日期/版本/证据字段。已审核事实内的clear不会覆盖数字失败；已知独立失败不因其他缺失项消失；矛盾事实本身不能选择有利值通过。质量审查还做3864次坏类型动态探针，没有未捕获异常。

### 685家公司真实研究库验收

使用现有 `tmp/stock-research-acceptance/final.db`，通过共享venv正式CLI入口执行：

```powershell
$env:PYTHONPATH="$PWD/src;$PWD"
D:/code/stock_robot/.venv/Scripts/stock-robot.exe radar stock-admission-evaluate --db tmp/stock-research-acceptance/final.db --as-of 2026-10-15 --output tmp/stock-research-acceptance/common-admission.json
```

前后数据库统计相同：685家公司、772条证据、0条审核、3个导入批次。研究报告：total685、common_passed0、common_failed0、pending_verification685、errors空、eligible_members0、members_published0、formal_screening_enabled=false。公司尚未完成规范事实审核，所以不能把候选提示当合格成员；此结果不是“市场没有公司达标”的统计。

报告及数据库留在忽略的tmp目录，不提交原始资料。实际筛选通过比例、完整批量采集耗时、内存峰值、正式风格/机会引擎、发布及成员生命周期、完整管理UI均未由本阶段验证。研究用日期文件不替代未来发布所需的生产交易日历和行情版本；当前审核状态不构成历史审核回测。
