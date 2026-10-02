# 策略指数两期实施计划（2026-10-01）

## 已确认设计
第一期：策略指数目录、名称/别名检索、ETF 跟踪指数识别、通用风险收益分析。
第二期：红利与自由现金流专项分析、成分/行业集中度、财务覆盖率与收益口径。
沿用现有页面视觉及布局，保留宽基/行业/海外逻辑。此次不发布、不提交；在独立工作树完成后将经过验证的改动同步回当前项目。

## 已验证接口事实
AkShare index_hist_cni(symbol,start_date,end_date) 返回日期/开高低收/成交额/成交量，涨跌幅为小数，国证980092真实取得263行；index_detail_cni 返回日期/样本代码/样本简称/所属行业/总市值(亿元)/权重(百分数)，取得100只。
index_stock_cons_weight_csindex(symbol) 返回日期/成分券代码/成分券名称/权重(百分数)，930740取得50只，不含行业。
stock_zh_index_daily_em(symbol=csi930740)与csi932365当前网络失败，必须有独立回退并展示数据失败。stock_a_indicator_lg 不存在。
stock_financial_analysis_indicator_em(symbol=600036.SH,indicator=按报告期) 返回 REPORT_DATE/NOTICE_DATE/ROEJQ/PARENTNETPROFIT/MGJYXJJE 等，财务报表累计口径；不能用这些指标冒充自由现金流。
官方核验：515300为嘉实沪深300红利低波动ETF，跟踪930740；不是930955。红利低波H30269包含字母数字，需要新增严格代码校验。

## 接口与所有权
### 任务1：主会话目录/API/CLI
拥有 data/index_mapping.csv、data/etf_index_mapping.csv、src/data/index_mapping.py、src/utils/symbols.py、src/api/app.py、src/stock_robot/cli.py 及相关入口测试。
IndexStyle增加strategy。IndexMappingEntry新增 provider(csi/cni/sse)、strategy_kind(dividend/dividend_low_volatility/free_cash_flow)、aliases、source_url、price_symbol、base_index，旧CSV可加载。
IndexMapping.resolve(query)按代码/唯一名称/别名返回entry，search(query)返回entries；ambiguous抛ValueError。
ETFIndexMapping.lookup(code)->ETFMappingEntry(symbol,name,index_symbol,source_url)。
GET /api/v1/indices?q= 返回 {indices:[{symbol,name,index_style,strategy_kind,provider,source_url}],etfs:[{symbol,name,index_symbol}]}；POST /api/v1/index 接受名称或ETF，解析成指数target；报告增加 requested_instrument 记录ETF映射，ETF表现使用独立ETF复权序列，不冒充指数收益。
第一批000015上证红利、000922中证红利、H30269中证红利低波动、930955中证红利低波动100、930740沪深300红利低波动、980092国证自由现金流、932365中证全指自由现金流。

### 任务2：数据与策略模块（实现代理）
拥有 src/index/strategy_data.py、src/index/analysis/strategy.py、src/index/analysis/performance.py、src/data/schemas.py、src/index/collector.py、src/index/pipeline.py、src/index/build_single.py、src/report/templates/index_report.jinja2、src/data/akshare.py 及对应测试。
新增 AnalysisResult.dimension 的 index_strategy/index_performance Literal，AnalysisTarget.index_style增加strategy。
IndexAnalysisContext新增 strategy_data: StrategySnapshot|None、benchmark_prices:list[IndexPriceData]、etf_prices:list[IndexPriceData]、requested_instrument:dict|None。
StrategySnapshot/ConstituentSnapshot 定义在 data.schemas：source/as_of/members/coverage/errors/strategy_kind；成员 symbol/name/weight(百分数)/industry/annual_financials，年度记录report_date/available_date/operating_cash_flow/capital_expenditure/net_profit/market_cap/total_debt/cash/dividend_per_share，缺失为None。依赖官方权重+现有行业分类；不得硬编码冒充实时财务。
StrategyDataProvider.collect(target)->StrategySnapshot：目录metadata来自IndexMapping；真实CSIndex/CNI成分权重；公开年度现金流、资产负债、利润和分红接口需探针，按年和公告日选最新可用3年，现金流自由现金流=经营现金流-资本开支、企业价值=同日市值+债务-现金；至少覆盖样本权重80%才可表述为指数估算，否则显示“已覆盖样本估算”。按权重采集全成分、缓存公开数据1天、单请求超时、有限并发，缺失及错误明确呈现，不能每次重复100请求。
collect仅strategy采集专项数据/基准行情，benchmark基准默认000300；performance为区间收益/年化波动/最大回撤/同日基准超额，相同日期交集、不能把价格口径标成全收益。
strategy分析计算 top10集中度/行业覆盖率与行业权重，红利分红连续性/稳定性(年度每股分红非负、缺年不算)/股息率，现金流收益率/连续性/OCF净利润比；各指标独立权重覆盖，公开口径说明、样本日期、财报年度、不可用原因。
两个section由builder写入 IndexReport.section_strategy/section_performance 可选默认None，visible_sections增加strategy/performance；Markdown下载同时支持，旧报告兼容。
策略价数据新增专用fetch分支，不改宽基旧接口；优先可实测源、超时、独立回退；无真实价格pipeline返回errors而不生成全空成功报告。
测试先行验证代码路由、CNI百分数单位、数据排序去重、异常源降级、权重不重归一化冒充完整、NaN缺失、年度公告日/累计口径、样本不足、同日基准交集、价格/ETF复权口径。

### 任务3：Web（实现代理）
拥有 src/api/static/js/indexview.js、src/api/static/js/api.js、src/api/static/js/report.js（只ETF跳转）、src/api/static/js/report-renderer.js、src/api/static/js/app.js、src/api/static/index.html、src/api/static/css/*（确需时）及前端测试。
在指数表单下面用现有panel/details展示“指数目录”，本地过滤名称/代码/别名，按类别分组，点击填入指数代码，支持名称和ETF代码输入；读取 GET /api/v1/indices。
为两个新section添加中文标签和单位/覆盖率/时间字段；不要裸渲染对象/数组。requested_instrument显示识别到ETF及跟踪指数，提供跳转ETF配置雷达入口（cn_hk_etf/515300需核对真实hash）；如果后端有ETF独立表现section标明复权口径。
个股入口输入已知ETF须引导或转指数入口，不能继续旧validate_symbol拒绝515300；沿用动态导入避免循环。
更新CSS/JS缓存版本，遵循共享content坐标与一个h1；不重绘页面。
前端测试检查目录过滤/选中/错误/重复初始化、数据缺失和旧报告兼容。

## 执行与验收
1. 先写失败测试，再实现，主会话整合。
2. 单文件Ruff/Pyright、相关index/data/api/cli/push tests；所有pytest使用项目.venv python、tmp/pytest/<范围>-PID-GUID>独立basetemp与cache。
3. 真实路径 .venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765；PYTHONPATH指向工作树src，确认实际导入路径。真实浏览器发送515300与现金流名称，验证响应、目录、ETF/指数口径、专项指标/覆盖缺失、宽屏与390px、截图与hover（若有曲线）。页面布局改动逐页检查规定页面，无水平溢出。
4. S级独立spec与quality双审查，修复后复审。只跑受影响检查；同步回主项目前完整范围核验，合并前全量门禁显式执行并报告。
5. 保留探针/失败清单和验收记录；没有真实数据的指标不能称为已验收可用。


## 实施期间核验与调整
- 515300官方基金资料核验为嘉实沪深300红利低波动ETF，跟踪930740。修正雷达笼统名称，独立维护跟踪关系。
- 七只新增指数官方源均取得266条日线，2025-08-27至2026-09-30。中证使用官方perf JSON，国证使用官方行情JSON；东财为独立回退。腾讯批量报价和ETF qfq作为独立公开源，通过真实探针。
- 财务改用东方财富公开批量API、有限并发、请求12秒超时、一天SQLite缓存。至少预取四年，按公告/修订日选择可用年度，统一年度窗口避免混年加权。
- 权重采样日与财务截止日、报价日期分别展示。中证当前公开权重2026-08-31，已保留日期及陈旧提示；原始官方权重合计可能因舍入略超100%，不重归一化冒充完整。
- 参考企业价值口径采用同日总市值+年报总负债-货币资金。报告明确其为参考估算，不能声称复现官方因子；市值口径同时独立展示。
- 腾讯市场总市值字段45为亿元，转换为元；每股年度实施派息以每10股派息除10，历史送转股未调整，在方法说明公开。
- 已完成独立需求与质量双审查，修复目录sector键、重复ETF身份、中文日期标签、最新可用年度、Markdown未采集章节标签及同日指数收益对照。
- 最终验收与全量结果另见本目录验收记录。失败产物保留在tmp，成功产物待全部测试停止后清理；不提交缓存与截图。
