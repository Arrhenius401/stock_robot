# 个股研报实用性实施计划

## 2026-10-20 P0 实施增补

### 资金流报告层修复计划

用户已确认零/微小非零/缺失三种展示行为，继续复用 codex/report-p0 工作树（起点 c813bad）。先测试，再新增独立报告展示转换，保留 section 原始 summary/metrics/tag，新增 display 文案及徽标元数据；综合研判仍使用原分析标签。网页、Markdown 与多指数对比使用 display，终端比较优先取展示标签。公共数值格式只为资金字段避免将微小非零值显示为零，保持既有亿单位或单位未记录提示。验证 raw tag、原始结果、composite/position 不变，覆盖零值与另一个非零字段混合、None/NaN/Inf、正负小值及正常值。按改动范围 Ruff/Pyright/pytest；本地真实服务提交指数分析并核对展示，不改变布局，不做云端验收，不推进 P1 或合并发布。

用户已确认完整规则及金额自动单位。工作树 D:/code/stock_robot/tmp/worktrees/report-p0，分支 codex/report-p0，起点 dev 7f45655；复用主仓 .venv 并显式 PYTHONPATH 指向工作树 src。原主题文档收敛于本设计和本计划。

1. 先补股票估值回归：688326 负 PE 不产生折价；有限正数公共判定和分位区间检查；行业充实、三种 scorer、估值分析器逐层防御。保留正常得分、权重、银行/周期专项规则和市值排名。无有效估值时无维度评分，不重分权重。
2. 补指数回归：普通指数样本无效不能泄露分位/低估标签至报告、综合判断或 LLM；复用有效性判定但保留策略指数独立口径。验证正常路径不变。
3. 补数值表达回归：公共字段口径格式化、列表表格、评分及概览模板；网页复用同一口径，不重排界面；金额缩放、百分比单位、非有限数、零值、微小价格、币种未知均有断言。原始 API/归档数字不变，旧报告不重写。
4. 主会话整合实际模型输入：OpenAI/Claude/HK/指数提示词消费可用性约束与带口径的展示事实；保留必要原始披露但禁止非适用推论。捕获实际调用提示词验证，而非只测模板。
5. 按受影响文件 Ruff/Pyright、唯一临时目录的模块 pytest；完成代码审查及修复复核。使用 .venv/Scripts/stock-robot.exe 的真实启动路径检查报告发送响应、宽窄屏与 Markdown 下载一致性；真实网络或模型未覆盖须如实记录。不默认全量、不推送合并或发布。

代码职责：股票估值（analysis/scorers、analysis/valuation、industry_enricher 及相关测试）；指数（index 分析/构建及相关测试）；主会话（公共 utils 数值判定、report 格式化/模板、Web、模型输入与整合）。先测试后实现，审查及验证摘要收敛于本节。

### 资金流报告层修复验收结果

- 新增共享资金展示转换，单指数、比较表、Markdown、CLI 和网页统一使用展示标签；原始 summary/metrics/tag、综合研判及仓位系数不回写、不重算。零值显示“无净流入或净流出”，缺失显示“暂无数据”，微小非零值保留有效精度；单位沿用既有口径，不猜测缩放。
- 回归覆盖负零、零与非零混合、None/NaN/Inf、正负微小值、普通值和不可用状态；验证原始分析结果、标签及综合结果不变。Python/JavaScript 数值契约包含科学计数法边界。
- 合并规格/质量审查发现 Markdown 综合表和不可用摘要两处仍走旧文案，已修复、增加断言并复审关闭。
- 最终执行 `scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：Ruff 通过，Pyright 0 错误/警告，pytest **490 passed, 3 skipped, 4 warnings**（30.48 秒；既有 Starlette/LangGraph 弃用警告）。未运行 Full 门禁；合并/发布前仍需全量验证。
- 使用工作树 PYTHONPATH 与主仓 `.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765` 启动真实服务，浏览器提交 000300、000905；卡片、摘要与比较列均正确显示零资金流，主力资金缺失保持“暂无数据”。1280px 和 390px 页面无水平溢出，窄屏文案正常换行。截图保存主仓忽略目录 `tmp/acceptance/capital-display/`。沪深 300 本次行情缺失在页面明确显示，不属于本轮修复范围。
- 微小值和非有限值为离线回归覆盖，并非真实网络样本；未做云端、QQ 或付费模型验收。历史归档不重写，数据源、计算规则、插件与版本号不变。用户已发版，本轮与此前 P0 修复留作下一版本；不改写发布标签，不推送、合并或发布。

### P0 实施与验收结果

- 股票逐项有效性、行业充实与三种 scorer 的残留数据防御已落实；688326 原始异常数值回归保留。有效 PB/PS 不随无效 PE 清空；无有效项不输出估值评分。正常盈利、银行及周期反转规则回归保留。
- 指数普通/策略分支、概览、比较表均阻断无效历史分位；策略空快照的提前返回也清除残留分位。摘要与表格复用格式化，零涨跌幅显示 0.00%。
- 公共格式化覆盖字典、列表表格、模型事实、网页及概览；金额按基本单位万/亿缩放。审查发现北向资金已有“亿”单位，因此单独保留原单位，其他单位未记录的资金字段明确说明、不猜测缩放。财报币种缺失明确原币，不进行换汇。
- 双只读规格/质量审查指出策略提前返回分位、资金单位、指数摘要精度三项；已修复并复核关闭。银行旧断言已同步两位百分数；未变更评分档位、权重或 P1 同行池。
- 最终门禁：`scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`，46 个暂存文件，Ruff 通过、Pyright 0 错误/警告，pytest **929 passed, 3 skipped, 4 warnings**（84.90 秒；既有 Starlette/LangGraph 弃用警告）。最终检查目录由脚本清理；失败诊断目录保留。未执行全项目 Full 门禁，合并/发布前仍须执行。
- 真实启动：工作树 PYTHONPATH + 主仓 `.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765`。浏览器实际提交 688326、00700.HK、000300 并收到响应；未替换网络数据源或挂载 Mock 页面。A 股真实报告展示 26.23 亿、-3.82 亿、-9.56%，负 PE 不作折价；港股明确原币且历史分位不足，评分不适用；指数快照可用但无分位时不判断高低估。1280px 与 390px 无页面水平溢出，个股窄屏卡片 x=12/width=366。
- 报告库打开真实归档通过；浏览器下载事件等待超时，随后使用页面提供的真实下载路由核对 HTTP 正文（9,833 字符），金额/百分比/无效 PE 完整且无错误 -150.6%。不能据此声称浏览器落盘路径已验收。截图在主仓忽略目录 `tmp/acceptance/report-p0/`，不提交。
- 原始数值、历史归档未批量改写，插件代码与版本未改；本地服务已停止。运行自动刷新的 `data/industry_mapping.csv` 已留诊断副本并还原，不混入本事项。
- 未覆盖：真实付费模型生成与 QQ 转图发送（本地无模型配置，已用实际 Pipeline 捕获提示词分别验证 OpenAI/Claude/HK）；云服务器本轮未变更。另发现既有 `tag_capital(0, None)` 返回 negative、摘要称零流量“流出”；属于独立资金方向判断问题，本轮未改变其规则，发布前应另行处理。
- 手动成功测试的三处旧临时目录清理被环境策略拒绝，未执行且不重试；仅保留忽略目录内的测试产物，不影响已通过的检查。最终门禁自身创建的成功目录已由脚本正常清理。

日期：2026-10-08。用户已确认最终预览及正式实现，包含 Markdown 与 AstrBot 插件兼容；不再请求重复确认。
工作树：C:/Users/25618/.codex/worktrees/report-usability/stock_robot。基础 dev。复用 D:/code/stock_robot/.venv 的工具，显式 PYTHONPATH 指向工作树 src；真实启动时通过工作目录和 PYTHONPATH 载入工作树。

## 最终范围与不变量
- 保留全部原有指标、章节、风险；数据缺失不是公司风险；四个计分维度完整才允许操作信号/仓位。原评分权重保持。
- 新增待验证事项独立卡片、末尾数据覆盖与时效；标题独占行，下一行左样本范围、右数据来源，再下一行右数据截止。窄屏单列，无采集重试。
- 有效短行情缓存；不足20条不打技术分，20–59部分，60以上完整；历史估值分位遵循实际样本门槛；不推断上市日期。
- 全部主要数据不足：无评分/信号/仓位/模型结论；指标快照仍显示。LLM失败保留报告，可单独重试AI；不重新采集。
- 新增信息同步 Markdown、网页、会话工具、推送；下载/列表接口和原始缓存列表格式保持，旧记录未知来源显示未记录。
- 暂缓配置恢复、股票比较和历史变化。不改指数/雷达分析规则，不推送/合并。

## 已核对接口与共享契约
原 CacheManager.get/put 存 JSON列表；build_report 返回 Markdown字符串；/api/v1/analyze 返回原始报告字段；AstrBot latest_report_id -> download_report -> text_to_image，无章节解析。

数据任务新增 data.schemas.DataCollectionRecord：source:str=''；collected_at:datetime|None=None；cache_status:Literal['live','cache','stale','unknown']='unknown'；error_kind:str=''；error_message:str=''（仅安全归类信息，不放 SDK 原始异常）。AnalysisContext.collection_records:dict[str,DataCollectionRecord]默认空，键 price/financial/valuation/industry/news。缓存元信息独立保存，与列表同步生命周期，不污染既有值。时间用带时区。

报告任务新增 report.quality.build_data_quality(ctx,results)->dict，统一返回：
- status: 'ok'/'partial'/'unavailable'；coverage:百分数0–100；signal_allowed:bool；has_scored_data:bool。
- dimensions:list[dict]，每项 key(分析维度 financial/technical/valuation/industry/sentiment)、title(财务/技术/估值/行业/舆情)、status(上述三值)、fields:list[dict(label:str,value:str)]。固定样本范围、数据来源、数据截止，其余缓存状态/缺失原因/分析限制/完整来源按事实补充。未知显示未记录，缺失说明不得编造。
- pending_verifications:list[str]，复核提醒与事实分开，确定性生成，保留报告内依据。

主入口把质量字典挂 data_quality；把 pending_verifications 挂报告根；score.final 无评分为null；overview 补 price_start/price_end(ISO日期)；根 commentary_status:'ok'/'failed'/'disabled'/'insufficient'/'config'（依据事实）。保留原字段。主入口整合共享API payload函数，避免网页与会话漂移。

## 阶段和文件所有权
1. 数据：schemas/cache/pipeline、analysis/technical.py financial.py valuation.py、相关enrichers和数据分析测试。先写边界/缓存来源回归，再实现。验证旧缓存、过期缓存、多源、0/19/20/59/60条、财务/估值门槛、无依据不调用LLM。
2. 报告：report/quality.py scoring.py builder.py templates/report_v2.jinja2、push/summary.py及报告/推送测试。共享质量规则、无评分、信号门槛、实际日期、Markdown新增章节，旧报告仍读。
3. 网页：report-renderer.js app.css及对应静态测试。复用现有卡片，16/15/13/12px字号，价格20；AI圆角12；风险赭红；来源与截止对齐；兼容旧payload；无采集按钮。AI按钮通过 options.onRetryCommentary 回调，配置通过 options.onConfigure，不在渲染器请求服务。
4. 主会话：API/会话报告适配、AI重试（只使用已保存的结构化报告中的指标/维度，不引入采集恢复快照；校验数据字段/尺寸，不信任客户端信号/分数）、缓存版本、相关接口测试；AI迟到响应不覆盖当前报告。按范围审查并修复后本地提交。

## 验证与提交
先红后绿；工具使用 D:/code/stock_robot/.venv/Scripts/python -m pytest，每轮 tmp/pytest/<范围>-PID-GUID/basetemp和cache。各所有权任务只改自身文件，不独自提交；主会话按完整阶段验证后提交。单文件 Ruff/Pyright，相关pytest；门禁 -Scope Changed -Source Staged，无法归类显式文件。不默认全量。
真实 .venv/Scripts/stock-robot.exe run --host127.0.0.1 --port8765，浏览器真实发送并收到响应；报告/配置/日志/推送/雷达/指数在宽屏和390px无溢出。覆盖AI重试、切换会话与回返、Markdown报告库下载和AstrBot客户端契约；真实转图如无法联网如实报告，不冒称通过。保存截图。S级整合进行规格/质量审查与修复复审。未授权推送或发布。

AI重试实现明确为服务端短期报告上下文，容量64、过期30分钟，只保留解读所需报告和原Markdown上下文；不接受客户端旧评分，也不构建采集恢复。成功后更新本次Markdown，原列表/下载契约保留。

## 实施验收记录（2026-10-08）

- 数据层已验证短行情、财报期数、估值 29/30/119/120 样本、原缓存兼容、来源元信息和无主要依据跳过模型。相关测试首轮 124 项通过，修复后的受影响范围 73 项通过；对应源码 Ruff/Pyright 通过。
- 报告层 68 项相关测试通过，保留原六章、指标快照和风险，新增独立待验证事项及末尾数据覆盖；无可评分依据时分数为空，不完整数据无操作信号。
- 集成与界面最终执行 `.venv/Scripts/python -m pytest tests/agent tests/api tests/report -q`（工作树源码通过 PYTHONPATH 指定，唯一 basetemp/cache）：615 项通过，12 条既有依赖弃用警告。改动文件 Ruff 通过，集成文件 Pyright 为 0 错误；`git diff --check` 通过。未运行全项目全量回归。
- 真实启动路径为 `.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765`。完成会话发送、完整成果抽屉、切换会话后回返、个股页面生成/刷新、报告库 Markdown 详情与下载接口验收。行情来源为腾讯前复权，162 个有效交易日；测试桩残留的工作树行情缓存已移除，未修改主项目缓存。
- 1280px 与 390px 检查报告及指数入口，无水平溢出；功能页主标题坐标宽屏 x=234/y=82，窄屏 x=12/y=70。数据卡片宽屏样本和来源同高，来源与截止右列对齐，窄屏统一单列。
- 规格复核、质量审查及修复复核未发现剩余阻断问题。会话提取保留新增字段，AI 迟到响应只更新原批次缓存，失败前缀不会误标成功，模型异常返回可见错误。
- AstrBot 原客户端连接真实服务，按原列表/下载接口获取 6,335 字符 Markdown，新增章节与真实来源均完整；仅替换框架日志依赖，HTTP 和客户端业务代码未经替换。真实 AstrBot 长报告转图/发送和真实模型重试恢复未联调，不能据此宣称图片效果或模型调用已验收。
- 本地提交：`60fb80d` 设计与计划；`94a3c74` 数据来源和短历史；`2e805bc` Markdown 与信号门槛；`9c624ab` 共享报告和 AI 重试；界面及此验收记录另笔提交。分支 `codex/report-usability`，保留独立工作树，未推送或合并。
