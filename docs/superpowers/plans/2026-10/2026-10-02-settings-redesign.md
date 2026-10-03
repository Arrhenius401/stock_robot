# 配置重构实施计划

设计依据：../../specs/2026-10/2026-10-02-settings-redesign.md。用户已确认，可直接执行。

## 接口事实

现有 GET/PUT /api/v1/config 为受控字段接口，凭据为 configured/masked 对象；Config 在 cwd/.stock_robot/config.yaml 加载默认值后深度合并。CollectorStore.settings/update_settings 目前读取 SQLite，revision 为整数并检查冲突；关闭开关会取消自动任务。CollectorWorker.coordinate 与执行取消回调读取 store.settings。登录启动项为独立系统接口。

## 任务与边界

1. 后端（S 级）：先补回归测试，再实现配置原子持久化、版本冲突检测、完整 YAML 读取/预览/保存 API 和旧采集设置迁移。负责 src/utils/config.py、src/api/configuration.py、src/radar/collector_store.py 及必要采集 API 和对应测试。完整编辑应安全解析顶层对象、拒绝重复键/无效类型/非有限数字，验证已知配置与交叉约束，允许未展示字段并保留；错误不得回显秘密。默认空邮件字段须能从完整文件正常保存。GET 文件仅本机同源允许。
2. 前端（S 级）：负责 settings.js、collector-settings.js、api.js、app.js/index.html 缓存版本、app.css 和前端相关测试。六模块导航和右侧操作栏，AI 内分连接与生成卡片；采集开关/时间由普通表单草稿保存，运行状态与系统启动操作继续保留。编辑器用本地实现的行号/高亮，避免外部 CDN 依赖。
3. 接口契约：GET /api/v1/config 增加 revision；PUT 原接口可选 revision，前端必须带。GET /api/v1/config/file 返回 source/revision。POST /api/v1/config/file/preview 接受 source 和可选 update，返回 source/config（安全表单对象）且不写盘。PUT /api/v1/config/file 接受 source/revision 和可选 update，返回原保存结果。update 为表单后续修改，服务端合入源文件；无 update 时尽量保留原文件文本。预览用于打开编辑器合入当前表单草稿、应用编辑器到表单。前端需保留服务器 revision，文件草稿为独立脏状态，不能因 renderSettings 清空。
4. 整合与审查（主会话）：验证两个实现接口一致。S 级要求规格与质量两轮独立审查；修复后复审受影响范围。使用独立工作树，不提交用户运行数据。
5. 验证：复用 D:/code/stock_robot/.venv 的工具，PYTHONPATH 显式指向工作树 src；相关测试的 basetemp/cache 放工作树 tmp/pytest/<范围>-<PID>-<GUID>。配置与采集回归优先，必要 API/静态资源检查。真实服务和浏览器验收，截图留 tmp。不隐式跑全量或提交。

## 实施与验收结果（2026-10-03）

### 用户确认预览后的界面修订

- 用户确认独立原型主界面（`tmp/settings-preview-v2/main-wide.jpg`），编辑器顶部随后改成浅蓝灰色并确认（`editor-soft-header.jpg`）。此轮仅调整已确认的前端布局，后端配置与迁移逻辑不变。
- 正式界面改成四模块、每模块两页签；切换保留节点及输入。右下角按钮为 `{…}` 与软盘图标，桌面 58px、右 40px、下 62px，间距 14px；窄屏 52px 并预留卡片右侧与底部空间。编辑器全屏、顶部 `#edf3fb`。最终资源版本 `20261003-settings-5`。
- 先新增四模块、页签及保存图标回归，得到预期失败后实施。最终静态 UI 与完整配置文件回归 52 项通过；最终缓存版本单独复核 1 项通过。`ruff check --no-cache tests/api/test_static.py`、`node --check src/api/static/js/settings.js`、`git diff --check` 通过。前端合并规格/质量审查通过，无本轮 P1/P2。
- 在已运行的真实 CLI 服务中验证跨页签草稿、编辑器合入表单修改、非法小时 99 选中错误行、应用与统一保存。温度 3 保存收到真实 422 错误，输入保留、软盘图标及按钮状态恢复。测试模型已恢复 gpt-4o，温度恢复 0.3；隔离配置外的用户配置不受影响。
- 真实会话发送“列出可用工具”收到正文，切换配置页再返回同一会话后正文可见。宽屏 1280px 与窄屏 390px 的个股、指数、雷达、报告库、订阅、日志、配置无页面水平溢出；配置控件与两个悬浮按钮的矩形相交数量为 0。配置页滚动归零后标题 x=12/y=70；已滚动状态的坐标另外保留在测量记录，不能与顶部间距混淆。
- 本轮截图为 `tmp/settings-redesign/approved-main-wide.jpg`、`approved-editor-wide.jpg`、`approved-main-narrow.jpg`、`approved-editor-narrow.jpg`、`approved-chat-response.jpg`；测量为 `approved-layout-measurements.json`。仅配置选择器改变，其余页面截图验收复用前次相同布局的结果，补充了本轮宽窄屏坐标检查。
- 未运行全量门禁、外部 LLM/邮件服务或实际系统登录启动项写入；尚未提交或合并。

- 独立工作树：`C:/Users/25618/.codex/worktrees/settings-redesign/stock_robot`。原工作区配置、凭据与数据库未复制或修改，尚未提交、合并。
- 已实现六模块单页切换、分组卡片、桌面右侧操作栏和移动端底部操作栏。完整 YAML 编辑提供行号、高亮、恢复内容、校验错误行定位；应用仅更新草稿，最终保存统一持久化。未知配置字段保留，无表单合并时保留源文本；合并表单修改时可能重排格式、丢失注释，界面已明确提示。
- 雷达计划迁入 `radar.collector`，旧 SQLite 设置只迁移一次。保存采用文件锁、原子替换与版本冲突检测。关闭自动采集不取消手动任务；系统登录启动保持独立操作。
- 规格与质量独立审查、修复复审均通过。后续真实浏览器发现同一会话从配置页返回时被提前返回拦截，先加入失败回归再调整判断顺序；保留正在运行的消息节点。编辑器首次打开/恢复回首行，校验错误时启用输入框再聚焦。最终缓存版本为 `20261003-settings-3`。
- 相关后端配置、采集 API 和迁移回归 132 项通过；采集 worker 相关回归 11 项通过。最终 `pytest tests/api/test_static.py tests/api/test_configuration_file.py -q` 为 52 项通过（1 条 Starlette/httpx 依赖弃用警告）；缓存版本更新后单独复核旧缓存断言为 1 项通过。各次测试均通过项目 `.venv/Scripts/python.exe`，使用唯一 basetemp/cache 目录。
- 改动 Python 源码与测试执行 `ruff check --no-cache` 全部通过。配置、采集存储、配置 API 三文件以及 worker 与对应测试的定向 Pyright 为零错误。`git diff --check` 通过。没有运行全量 Ruff/Pyright/pytest 门禁，合并或发布前仍需执行全量验证。
- 通过真实启动路径 `.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765` 验收。隔离配置中 LLM、邮件推送及自动采集关闭，不触发外部服务；发送“列出可用工具”收到真实响应，切换配置页、返回同一会话和重载后恢复历史正文均可见。
- 文件编辑真实验收：表单修改进入编辑器、非法小时 99 保留草稿且定位对应行、恢复内容、未知字段保留、应用后磁盘 SHA256 不变；最终保存后 Config 与 CollectorStore 读取同一新值。随后恢复隔离配置初始值。
- 1280px 与 390px 下逐页检查个股、指数、配置雷达、报告库、订阅推送、日志和配置；每页一个可见 h1，无页面水平溢出或控件遮挡。桌面标题 x=234/y=82，窄屏 x=12/y=70；首个组件与标题保持共享左边缘和 16px 间隔（外层有 padding 的容器记录另含容器边界）。配置页保留必要滚动条，因此可见宽度略小。查看实际截图，窄屏编辑器按钮换行、编辑区可滚动。
- 截图与布局记录留在原工作区忽略目录 `tmp/settings-redesign/`，包括 `settings-wide.jpg`、`editor-wide.jpg`、`settings-narrow.jpg`、`editor-narrow.jpg`、其他页面截图与 `layout-measurements.json`。没有覆盖外部 LLM/数据供应商、真实系统启动项写入和长时间自动调度运行。
