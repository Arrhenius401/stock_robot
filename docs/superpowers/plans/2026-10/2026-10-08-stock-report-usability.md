# 个股研报实用性实施计划

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
