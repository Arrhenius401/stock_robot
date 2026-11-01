# 个股报告内容与展示收敛实施计划

## 基线与验证运行约定

工作树 codex/stock-report-refinement，基线 fbd9513。设计见同月2026-10-28-stock-report-refinement.md。复用 D:/code/stock_robot/.venv/Scripts/python.exe，显式设置 PYTHONPATH 为本工作树 src，禁止裸python；pytest每次唯一 tmp/pytest/<范围>-<PID>-<GUID>，失败保留，成功后仅清理本次目录。无新依赖。

## 接口与任务划分

1. 报告展示与数据副本（S级实现）：先补回归，再修改src/report及相关tests/report。新增report/presentation.py承载compact_quality(quality)->dict（summary_rows与limitations）和compact_score_detail(detail)->str，原始fields不改。summary_rows每项key/title/status/sample/cutoff/source；limitations每项dimension/label/value。信号展示对象level/label/basis，不返回action/position，依据实际阈值生成。构建器和payload共用质量汇总与摘要，API仍保留commentary_status。不改signal.py、业务计算及网络。
2. 模型提示词（A级实现）：三个batch_analysis模板统一三标题：## 1.核心判断、## 2.关键依据与分歧、## 3.后续观察重点。原安全/港股约束保留；无新增调用。核对pipeline没有固定四段解析器，只更新旧注释/测试。所有新模型结论须已有事实支撑，风险跟随证据，禁止重算分数/仓位/买卖建议。修改src/llm/prompt_templates、必要pipeline注释及对应tests/llm/tests/core的提示词断言。
3. 主会话网页实现：先写DOM行为回归。report-renderer按基础/五维/评分/研判/质量/免责声明顺序，保留共享卡片，移除pendingSection调用，信号在commentarySection尾部，保留重试/配置。qualitySection消费summary_rows/limitations，旧payload从原fields安全回退；保留异常/参考原因；新闻details由DOM生成。合并评分解释，隐藏ROE历史/空限制，财务口径放表下。markdown.js安全识别角括号链接；不放开HTML。旧Markdown归档不重写，支持新综合研判章节，不损坏黄金专用路径。更新相关静态缓存版本。
4. 集成验证：执行相关tests/report、tests/llm、API静态与report API测试以及受影响pipeline测试；只针对失败及改动扩大复核。保留首个异常和失败清单。Changed计划可能选择大范围时审阅计划，必要时明确-Files覆盖，复用同代码检查结果。
5. 真实验收：隔离tmp配置/缓存/输出，复用项目真实CLI入口8765，查端口归属只结束本任务服务。浏览器实际提交A股代码并观察加载/完整响应，核对新报告、新闻折叠/链接、配置/重试入口、数据异常原因、报告库旧/新兼容。390px无水平页面溢出、表格允许局部滚动。输出记录入本计划，日志截图tmp，不写额外交接文档。
6. 双审查：规格审查与质量审查独立，修复并由审查者复核。暂存完整事项，diff --cached --check，复用相关验证或Changed Staged，中文本地提交。没有授权push/merge/release或改写历史。

## 进度

- 设计与Markdown/网页预览已由用户确认；工作树建立，开始测试先行。

## 实施与验证记录（2026-10-29）

- 已实现六章报告、三段解读提示词、真实阈值信号依据、无AI摘要、精简质量汇总与异常原因；历史估值算法和回测契约未改。
- 规格与质量审查发现阈值精度、代码内链接、旧摘要/财务包原因、风险及评分重复等问题，已补回归并修复；异常原因复核通过。
- 相关报告、模型和网页测试416项通过；修复边界后定向23项通过。最终 Changed 检查 Ruff、Pyright、数据入口检查通过，pytest 1619 passed、13条依赖弃用警告。
- 真实 CLI 以隔离配置启动8765，浏览器提交600519并收到 HTTP 200；实际展示六章、量化摘要、信号依据和舆情未评估原因。390px页面宽度与滚动宽度均为390，截图确认侧栏收起后主要内容可见；1280px个股标题与输入组件左侧均为234px、宽980px。未进行真实AI模型调用。
- 上次成功测试目录 tmp/pytest/prompt-green-25500-26b8dd4eff014ac9a1acab6d12a98cb0 的递归清理被自动执行策略拒绝，保留在忽略目录；未进行ACL修改。
