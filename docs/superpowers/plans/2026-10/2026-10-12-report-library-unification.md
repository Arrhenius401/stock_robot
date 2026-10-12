# 报告库个股报告统一呈现实施计划

用户已确认 2026-10-08 报告库统一预览，2026-10-12 授权继续实施。本事项收敛为一笔本地提交，不推送或合并。

## 已确认行为

- 新个股报告默认复用股指分析的共享卡片组件，保留评分依据、多风格观察、工具局限性、独立待验证事项与末尾数据覆盖。
- 报告库保存生成时的快照，只读展示，不提供重新分析或 AI 重试。顶部保留返回、Markdown 下载和原文切换。
- 历史 Markdown 报告继续完整展示；快照缺失、损坏、不匹配时回退原文，不迁移或重新采集历史数据。
- 个股报告移除回测指标，回测报告保留原指标、曲线和成交记录。指数模板不变。
- Markdown 文件名、列表标识及下载接口保持兼容插件。

## 接口与实现步骤

1. ReportFormatter.save 新增可选 stock_payload 参数，个股保存同名 .report.json，包装 schema_version=1、markdown_sha256、payload。快照清理 commentary_retry_id；有限数值保留，非有限值转 null；原 Markdown 保存行为兼容。新增 update_stock_snapshot(markdown,path,payload) 同步更新 AI 重试后的原 Markdown 和快照；文件通过临时文件替换，内容摘要防止并发或中断导致错误配对。
2. ReportDetail 新增 stock_payload: dict|None。仅个股读取 .report.json，校验摘要、版本、标的、结构与路径范围。异常记录日志，原 Markdown 仍能打开；指数、回测不读取它。
3. 网页、会话适配器、CLI 的个股保存入口传入共享 build_stock_payload；AI 重试更新同一快照。存档不持久化临时重试令牌。Markdown 原文继续保留全部内容。
4. report-library.js 默认 stock_payload 存在则研报视图，另提供 Markdown 原文；旧报告用原章节卡片。只在回测显示回测指标。共享 renderStockReport 增加只读存档选项与 archiveMarkdown 补充章节；显示 score_detail，保留多风格观察和工具局限性卡片，数据覆盖最后。未知原文内容仍通过完整原文入口保留。
5. 回归：存档 JSON 与 Markdown 一致、缺损/摘要失配/符号失配回退、NaN 和临时令牌清理、三个保存入口、AI 重试同步、个股只读与原文切换、旧报告完整、指数回测行为不变、插件下载契约。
6. 相关 Ruff/Pyright/pytest；真实 stock-robot.exe run 服务，真实发送和报告库新报告查看、历史回退、宽屏及390px无溢出。跨文件/API 改动进行规格和质量审查，修复后复核。代码稳定、验收通过后源码测试文档一并提交。

## 文件所有权

- 存档任务：src/report/formatter.py、src/api/report_library.py 及对应 tests/report、tests/api/test_report_library.py。
- 界面任务：report-renderer.js、report-library.js、app.css、缓存版本引用及界面测试；不得修改业务 Python。
- 主会话：app.py、agent/pipeline_tools.py、stock_robot/cli.py 与入口测试、集成审查、真实浏览器验收和最终提交。

## 实施与验收结果（2026-10-12）

- 三个生成入口保存共享快照，AI 重试同步原文和快照。快照失败降级为已更新的 Markdown；原文写失败仍报错。网页原文和快照统一使用配置中的 AI 开关。
- 报告库复用研报组件，只读呈现评分依据、原第五/六章节、独立待验证事项和末尾数据覆盖。增加原文切换，移除个股回测指标；指数和回测展示保持原入口。
- 按 `verify.ps1 -Scope Changed -PlanOnly` 输出的范围执行 `.venv/Scripts/python -m pytest tests/api tests/agent tests/report tests/test_cli.py tests/test_cli_index.py tests/test_cli_report_archive.py tests/radar/test_collector_cli.py -q`：704 passed；使用唯一临时目录。12 条既有框架弃用提示不影响结果。
- 改动 Python 文件 Ruff 通过；工作树专用配置 Pyright 0 errors。规格与质量复审通过，两项 AI 开关/快照写入一致性问题已修复并纳入回归。
- 使用项目 `.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765` 启动真实服务；浏览器发送个股报告请求，生成归档并验证新卡片、原文切换与 10 月 8 日历史报告回退。1390px 和390px检查无页面级横向溢出，功能页标题沿用234px/12px左对齐线，股指个股与指数入口一致。
- 现有 `astrbot_plugin_stock_robot.client.StockRobotClient` 在真实服务完成分析、定位最新报告和下载 Markdown，6147字符，包含待验证事项及数据覆盖。只替换 AstrBot 日志依赖，不修改插件客户端业务代码。
- 未验证真实 AI 调用（环境无可用模型）、AstrBot 内部图片渲染、项目全量门禁。指数模板未重新设计；本次只执行统一事项。合并或发布前仍需显式全量检查。
- 预览、截图、运行日志、快照和测试产物均保留本地忽略目录，不随源码提交。本事项源码、测试和文档合为一笔本地提交，不推送或合并。
