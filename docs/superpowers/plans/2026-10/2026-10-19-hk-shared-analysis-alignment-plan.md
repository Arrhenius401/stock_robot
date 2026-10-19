# 港股公共分析对齐实施计划

依据：同日已确认 specs/2026-10-19-hk-shared-analysis-alignment-design.md。全部正式实现局限于四项；名称、大盘、币种期间模型、插件队列不改。复用现有独立工作树，原 c19dd7b 与主工作区日志保留，未获授权合并/推送/上线。

## 环境与接口事实

- 主仓 .venv/Scripts/python.exe 是唯一验证 Python；独立工作树通过 PYTHONPATH=本树/src 复用它。pytest 每次创建主仓 tmp/pytest 下 GUID 目录，传 --basetemp 和 cache_dir；保留失败产物。不能边跑测试边改相同源码。
- 来源选择旧行为：>=60 条立即选取；失败/较短继续备源；均不足保留行数最多的一份，相等取后者；不拼接不同来源。A 股 _ak_daily 实际调用新浪 stock_zh_a_daily，不改采集顺序或 SDK 兼容签名。
- 腾讯港股 https://web.ifzq.gtimg.cn/appstock/app/fqkline/get，param=hk00700,day,2025-10-19,2026-10-19,370,；响应 code=0/data.hk00700.day，行顺序 date,open,close,high,low,volume；data 中含 qt，公开探针 245 行。按标的、日期和未复权 day 键验证，不能误读 qfqday。
- 百度参数和响应已由同日调查探针验证，response.Result[0].DisplayData.resultData.tplData.result，code=00700，chartInfo[0].header=[市盈率(TTM)] 或 [市净率]，body=[日期,值]。releaseDate 为上市日期，不当指标日期；最多两个并发请求、10 秒，无内部重试。
- 现有 FinancialData 无币种/期间类型，本次不迁移；ValuationData 单个日期，按已确认共同日期规则合并。报告数据依据通过 DataCollectionRecord 共享；缓存中私有 _data_source 不单独恢复但 record.source 会恢复。
- 技术计算已在 analysis/scorers/general.py：calculate_macd 及 MA/量比，保留旧 import 与 A 股打分边界；公开完整指标 MA5/20/60、量比25日、MACD34日门槛。

## 任务与文件所有权

1. 数据实现（独立代理）：tests/data 新增失败回归，随后修改 data/akshare.py、新的窄范围来源选择/港股请求模块、相关 tests/data。验证两市场共享同一 selector；HK 腾讯→东财、估值东财→百度；短历史保留、不拼接；错代码/坏行/坏日期/不完整响应拒收；同日估值合并与单项失败保留；错误与真实来源记录、缓存调用后仍可见。不得修改 core/pipeline、report、技术模块、verify.ps1 或正式文档。旧 A 股来源标注更正为新浪。
2. 技术实现（独立代理）：tests/analysis 新增失败回归，提取 analysis/technical_indicators.py（或等价公共模块）、修改 technical.py 与 scorers/general.py。构建同一序列 A 股/HK 指标一致断言；0/5/20/25/26/33/34/60 样本边界、零成交量、MACD 旧入口兼容、旧 A 股评分结果不变，HK 不计分。只拥有 analysis 相关源码测试，不改数据、管道、report。
3. 主会话质量与缓存：先补 report/core/API 回归；新增 data/validation.py（字段/有限数值事实共识）并改 Pipeline 的财务缓存健康检查，保留 TTL 和刷新。港股 quality 适用三维、0/1/2/3 可用比例、维度状态 partial 与 coverage 独立，不适用维度不加入待补齐；A 股权重/评分规则保持。同步共享报告展示文本、指标标签与 JS 缓存版本；更新 verify.ps1 新文件测试映射。
4. 集成：合并相关改动到同一工作区后冻结源码，运行相关 Ruff/Pyright/pytest；复用通过范围，保留首个异常清单。复杂公共提取按 S 级做独立规格/质量审查，修复后复审，不重复派发实现。
5. 真实验收：测试与审查稳定后打包隔离候选；复用云端既有依赖，/tmp 独立端口/数据目录，推送与 AI 起初关闭，分别分析 00700.HK/09988.HK/01211.HK，核对三维指标、源、指标日期和耗时。候选进程结束即停止，在线25618/health、active-runtime 保持原样。随后真实本地 .venv/Scripts/stock-robot.exe run --host127.0.0.1 --port8765 浏览器实际提交，检查指标、覆盖及不适用展示。AI/QQ 结果单独记录，未真正执行不得称通过。
6. 交付：同一完整事项的源码、测试、资源、规格/计划/更新日志一笔本地提交。复用同代码状态相关检查并 git diff --cached --check；不自动合并或全量发布门禁。失败产物、截图、探针响应、凭据不提交。结论明确线上未变与未覆盖风险。

## 跨任务接口不变量

- 主会话新增 data.validation，数据代理不要写这个文件。财务核心实值字段集合用于缓存及港股质量判断，合法零/负值有效，全空和非有限无效。数据代理所有实际采集模型先校验 symbol。
- 技术 metrics 键：latest_close,ma_5,ma_20,ma_60,volume_ratio,macd_dif,macd_dea,macd_bar；不足用 None，score=None 的 HK 不得泄漏 A 股分数。主会话负责报告标签，不让代理改静态文件。
- 港股质量新增 available_dimensions、applicable_dimensions、coverage_basis（可读语义可同义），coverage 百分比仍保留；不适用项用明确状态和来源说明，但不进入缺失警告。保持原 has_scored_data=False/has_analysis_data 含义及 signal_allowed=False。
- 报告只改现有位置的指标、文字和状态，不做布局或交互设计；无新运行依赖、配置、DB 或后台服务。

## 最终实现与验收记录（2026-10-19）

- 四项实现已落地：A 股/港股共享 `select_history` 选源（A 股新浪→东财，港股腾讯→东财，短历史保留且不拼接）；港股估值东财快照→百度 PE(TTM)/PB，保留主源失败与指标日期；`analysis/technical_indicators.py` 提供 MA/MACD/量比并被 A 股评分与港股技术分析共用，港股不计分；`data/validation.py` 统一财报缓存有效性判据；港股覆盖率改为可用维度/3，行业与舆情标记不适用且不进入缺失提示。
- 相关集成回归：`tests/data tests/analysis tests/core tests/report tests/api tests/test_verify_script.py` **863 passed**；Ruff 全过；Pyright **0 errors/0 warnings**。静态资源与版本链断言通过。
- 本地真实浏览器（隔离服务 `127.0.0.1:8765`，独立数据目录，llm/push 关闭）：真实提交 `00700.HK` 得到 HTTP 200，可用维度 **3/3（100%）**；技术为腾讯港股不复权 245 个交易日（至 2026-10-16），MA5/20/60、MACD、量比齐全；估值为百度 2026-10-19 PE(TTM) 14.01、PB 2.90，东财快照失败原因保留；财务为东财 96 期（2026-06-30）真实值；行业/舆情显示不适用；无综合分/操作信号；390px 无水平溢出，页面脚本错误为零。截图、渲染文本与响应留在忽略目录。
- 验收中发现并修复：技术指标网格显示原始浮点精度（如 443.2266666666667）改为 2/3 位小数展示；静态缓存版本链升至 `20261019-hk-shared-analysis-2`，并补充渲染层回归断言。
- 云端隔离候选（AstrBot 容器内 `/tmp`，独立端口与数据目录，llm/push 关闭）：`00700.HK`、`09988.HK`、`01211.HK` 全部 **3/3（100%）**，单标的耗时 0.76–0.98 秒，行情/财报/估值均为真实来源；在线 25618 健康 `ok`、`version=0.2.0`，受保护配置哈希未变；候选进程已停止，候选临时目录已清理，验收 summary 留在容器 `/tmp`。
- 仍未覆盖：真实 AI 生成与 QQ 投递未执行；东财港股估值在本地与云端均失败并由百度降级，腾讯/百度单次成功不代表长期稳定或所有港股可用；未部署、未合并、未推送、未发布。
