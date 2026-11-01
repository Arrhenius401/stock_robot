# 指数报告重构实施计划

## 状态与边界

用户已确认设计及网页预览，最新内容为七节、无评分/仓位/置信度、PE/PB统一名、四列质量汇总及异常列表。设计：同月2026-10-29-index-report-refinement.md。基线1f934eb；工作树codex/index-report-refinement。只在本工作树修改，主仓.venv只复用运行时；PYTHONPATH必须指向当前工作树src。无新依赖，无付费AI验收。原始探针留tmp/index-report-refinement。

## 已核实接口与共享约定

- IndexPipeline.run(targets,on_progress)返回reports/compare/errors，LLMBackend.generate(prompt,**kwargs)->str。Registry.get_llm_backend(provider)，Config.get提供配置。API/CLI/工具各有IndexPipeline构建路径，须一致注入配置与模型。
- IndexReport新增字段已由主会话声明：report_version默认1、generated_at、data_quality、commentary、commentary_status、commentary_retry_id、disclaimer。新build设置version=2，position_coeff=None，composite_comment仅作兼容性受限文字，禁止再投票。旧字段保留。
- 各section保持{status,summary,metrics}。报告层可增加display={rows:[{label,value,note}],...}供Markdown与网页共用。section_performance.metrics新增windows列表，每项period(1m/3m/12m)、label、start_date、end_date、sample_count、return_pct、max_drawdown_pct、annualized_volatility_pct、notes；默认12m保留旧指标用于兼容。技术保留旧键，新增adx_14、atr_20、atr_pct、price_position_pct、sample_start/end/count、direction_short/medium、trend_strength及methodology/notes，不要用旧tag作最终结论。
- RawSentimentItem.publish_date为必填date，不修改个股类型含义。指数新闻新增NewsData.index_items和IndexAnalysisContext.news_items（list[dict]，真实发布时间可为空），collector透传；旧raw_sentiment只为旧接口保留，不向新闻项伪造日期。资金和宏观可在模型新增notes与指标日期，不改变旧必填参数。
- 数据quality采用个股compact_quality输入格式dimensions:[{key,title,status,fields:[{label,value,reference_only?}]}]，复用四列normalization及异常去重。状态ok/partial/unavailable/not_applicable。行情技术和表现归同一数据维度；估值、资金、宏观、新闻各独立行，策略按真实适用性额外行。保留原fields，summary_rows/limitations用于展示；不把AI作为采集维度、不计算计分覆盖率。
- 序列必须同代码、有效日期、有限正close、排序去重；异常OHLC不能进入ADX/ATR，重复交易日后值优先并披露。日历月窗口起点取边界当日或之后首交易日，披露不足，不能偷偷外借旧价格。相邻日收益存在缺口须披露，不冒充无缺口年化波动。
- 官方000300日频1211条，peg口径未核实不能冒充TTM，单张20260930 PE13.15/PB1.36；399006中证历史空而国证单张PE31.28/PB5.20（未声明TTM）。将来源差异作为回归样本。PE/PB各自分位有效性独立，未知口径不生成高低估。
- 冻结事实重试存储30分钟/64项，锁、会话关联与原报告路径绑定。重试仅生成AI，不能重新采集。新API可使用/api/v1/index/commentary/retry，现有个股重试不受影响。单指数一次生成任务，底层既有重试；批量串行也满足最多2并发，不引入无必要并发。

## 任务与所有权

### Task 1 数据和分析（S级，data代理）

先写失败回归；拥有src/data/akshare.py指数段、src/index/collector.py、src/index/analysis/*、src/index/valuation_data.py及必要新增指数数据模块、data/index_mapping.csv、数据Schema中除IndexReport外必要字段，以及相关tests/data/test_akshare_index.py与tests/index/analysis、数据层测试。

1. 指数目录提供方和单张地址准确映射；000300及399006用已验证地址，其余不得臆造全部成功。普通估值按目录提供方选择，复用官方缓存/解析，未知PE口径保留为快照或独立披露，不能将peg直接标TTM；PE/PB指标独立有效，快照不拼历史。海外无匹配来源明确不可用。不要调用不存在的funddb。断网/陈旧/空响应保留具体notes。新增网络调用连接5/读10、采集总预算60秒、失败5分钟缓存与成功产物保留规则按设计落地；API事实不支持时向主会话报告，不能偷改范围。
2. 技术Wilder ADX14/ATR20和短中期方向；ADX<20不足、20至<25过渡、>=25强，仍区分方向；ATR点数和百分比，价格位置非分位；无样本不默认震荡。
3. Performance复用所有类型，近1/3/12个月窗口及样本阈值、共同交易日基准，价格和ETF区分。collector仅行业/策略在预算内补基准，海外不新造宏观来源；保留策略专项。
4. 资金不再将无法核实的北向0判负；移除行业代码前三位与中文名匹配，未核实映射显示原因。宏观按日期和PMI扩张收缩，不打分；海外禁用国内宏观冒充当地环境。新闻保留真实字段，不强制近1日，取消关键词投票，最多10条详情和真实来源链接。
5. 使用用户截图/官方原始异常样本的相关回归，fixture剪裁到tests/resources/index/，源和采样范围在测试注释及本计划记录。新增资源通知主会话添加verify映射。相关单文件Ruff/Pyright和相关pytest，先保留失败，修后只复核影响范围。不得修改报告模板/pipeline/前端/正式文档。

### Task 2 报告结构与覆盖（S级，report代理）

先写回归；拥有src/index/build_single.py、build_compare.py、新增index/report_quality.py或presentation.py、src/report/templates/index_report.jinja2、相关tests/index报告测试。消费上述Schema/分析字段，必要协调而不覆盖其他代理文件。

七节及适用策略专项；共用display rows防Markdown/网页漂移，PE/PB命名日期说明分列；无仓位、加权评分、置信度或默认整体中性。新报告report_version=2，旧字段兼容但不作新判断。质量表复用compact_quality/_quality_table，异常来源/过期/限制保留、未知原因未记录。第七节复用个股免责声明文本并去掉不存在的评分措辞。模型状态在研判节，不进质量表。临时无AI时三段确定性摘要且不假称AI生成，无真实价格不作整体判断、<60交易日受限；提供函数供commentary代理在生成后更新markdown。输出生成时间冻结，render不要重写时间。

比较表保留最新点位及真实行情日期、可比较日期/口径，移除综合评级和资金正负标签；同口径才比较，不把无值作为0。与旧比对测试不一致处按确认规则更新。保留策略指标、成分覆盖、ETF独立收益。结构化原始metrics保留。

### Task 3 模型与入口（A级实现，commentary代理）

先写回归；拥有src/index/pipeline.py、新增src/index/commentary.py和prompt模板、API指数及新增重试端点、src/api/bootstrap.py、CLI及agent指数工具必要接线、相关测试。不要修改build_single/schema/collector/前端，需接口协调。

通过配置/后端注入，所有入口一致；关闭、未配置、失败、数据不足状态明确。三段提示词只解释冻结确定性事实，不重算、不输出评分/仓位/价格建议，不放未转义外部指令。正文建议800—1200中文，非硬截断。每指数一次生成任务；复用模型预算；无额外比较调用。重试TTL/容量/锁/会话身份/路径，不能重采集，成功更新当前及存档，不串批次。API给报告身份和重试token，工具结果及会话抽屉沿用现有快照绑定；若现有架构不能安全关联，报告主会话，不独自删减。失败不导致量化报告不可用。

### Task 4 网页与集成（主会话）

先补有意义DOM/行为回归，修改indexview新版渲染分支，旧存档仍可读。复用个股qualitySection或提取共用质量渲染，七节结构和display rows，新闻原生details默认3条、链接安全，策略专项保留。AI失败可基于冻结报告重试，配置入口正确；加载/切页/错误可见，报告库/会话抽屉同步适用。新CSS延续现有变量、卡片、24px/980px/窄屏12px；更新静态缓存版本。verify.ps1映射新源码/资源到相关测试。不做无意义重复全量。

## 测试与验收

所有pytest使用D:/code/stock_robot/.venv/Scripts/python -m pytest，PYTHONPATH本树src；tmp/pytest/<scope>-<PID>-<GUID>每次唯一，缓存同目录。Ruff/Pyright复用主仓.venv工具。代理只运行自己相关范围，主会话最终按改动范围整合验证，必要时Changed -Files，无自动Full。

独立规格审查和质量审查（S级）+修复复核。正式入口使用D:/code/stock_robot/.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765且PYTHONPATH指本树，隔离配置/缓存/输出。浏览器真实提交000300/399006，验证返回与真实来源说明、库/抽屉、历史兼容、错误/重试；390px与宽屏测量，无页面溢出。前端/会话变更覆盖真实发送、切换/返回与可见错误，AI真实付费调用不增加，用关闭模式真实验收并测试失败分支。截图日志tmp，记录未覆盖的真实模型质量等风险。

最后同事项源/测试/设计/计划一笔中文本地提交，git diff --cached --check。未授权push/merge/release。主仓不改、预览不提交。

## 实施记录

- 2026-10-29 已确认设计，独立工作树建立；开始并行实现（按仓库S/A级规则）。

## 官方测试样本

- `tests/resources/index/csi-000300-valuation-factsheet-20260930.txt`：2026-10-29采集，中证官方沪深300单张 https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000300factsheet.pdf ，以pypdf layout提取PDF各页文本；报告日期2026-09-30，解析滚动PE和PB，保留原布局用于回归。完整PDF留tmp。
- `tests/resources/index/cni-399006-valuation-factsheet-20260930.txt`：2026-10-29采集，国证官方创业板指单张 https://www.cnindex.com.cn/html2pdf/preview/jj_399006.pdf ，以pypdf layout提取PDF各页文本；报告日期2026-09-30，PE未声明TTM，解析PE/PB，保留原布局用于回归。完整PDF留tmp。
- 测试资源的 `tests/resources/index/` 在verify.ps1按目录映射 `tests/index`；指数请求缓存及采集预算模块额外映射报告库、agent和网页定向测试。

## 集成与审查记录

- 规格审查和质量审查各一次，修复未知口径PE、OHLC异常、采集状态/来源、缓存与预算、策略采集优先级、分位独立性、网页披露、会话重试与同秒存档等问题；各审查者复审通过。
- 2026-10-29真实CLI入口在8765隔离配置启动，真实浏览器查询000300、399006；399006官方单张PE31.28、PB5.20，截至2026-09-30，PE未声明TTM。报告库打开结构化视图；真实会话发送399006，抽屉读取报告，切换另一会话再返回成功。000300单张当次HTTP读取超时，报告保留原因；不以其失败断言来源永久不可用。
- 跨模块测试276 passed、3 skipped、1依赖弃用警告；该次测试与数据代理收尾存在并行，最终以提交前门禁为准。用户自有付费模型未调用，模型生成质量与真实云端插件图片未验收。
- 自动执行策略拒绝递归清理tmp/pytest/report-pb-37708-3ecd04ea-0ffb-40d8-ad69-4aefb2ff87ee，保留忽略目录；不调整ACL或重复尝试。

- `tests/resources/index/csi-000016-valuation-factsheet-20260930.txt`：2026-10-29采集中证官方上证50单张 https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000016factsheet.pdf ，pypdf layout提取各页文本，报告日期2026-09-30；核对指数代码、滚动PE及PB。其余000001、000688、000905、000852、399001、399673官方单张同日HTTP200并核对代码、日期、PE/PB，完整探针留tmp。
- 宽屏1280px各功能页标题与首个主要组件x=234px、内容宽980px；390px页面标题与组件x=12px，无页面水平溢出，配置页因滚动容器占位宽351px。真实截图保留tmp/acceptance/index-report-refinement。
- 提交范围门禁初次运行：Ruff、Pyright及采集入口检查通过，pytest 2099 passed、3 skipped、16 failed；失败清单及完整异常保存在tmp/index-report-refinement/final-verify-2.log。资金零值真实缺陷已修，其他旧断言按已确认新版契约更新并保留旧归档兼容检查；后续按失败模块和实际修改范围复核，不重复全量。
- 门禁失败清单复核：资金、估值日期、CLI和网页兼容断言修复后，最终受影响模块149 passed；本次状态对应Ruff通过、相关Pyright 0 errors、数据入口检查通过，git diff --cached --check通过。未重跑全量；16项原失败不视作通过，按最终受影响范围结果收敛。
- 最终代码状态重启真实服务后再次发送399006：页面保留官方PE31.28/PB5.20及对应日期，资金缺失明确说明，新闻、宏观和行情来源可见；未出现“净流出0”。
