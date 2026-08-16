# Web UI 跑通 — 子项目 B：前端产品化设计

> 日期：2026-08-16
> 状态：已评审通过
> 前置：`2026-08-13-api-wiring-design.md`（子项目 A 后端接通，已验收）

## 1. 背景与问题

子项目 A 已将后端接通：`stock-robot api` 一键启动，chat/stream/analyze/index/sessions 端点全部可用。但前端仍是 91 行原型 `index.html`：

1. 聊天走非流式 `/api/v1/chat`，看不到 Agent 的计划拆解和执行进度
2. 快捷按钮形同虚设：`/` 开头消息被前端拦截只显示提示文本，"清空会话"是空操作
3. 报告能力（analyze/index 端点）在网页上无任何入口
4. 会话持久化已做但前端无列表/切换/管理 UI，刷新后会话上下文丢失（无 session_id 传递）

## 2. 目标与范围

### 目标

把 Web UI 做成可用的产品形态：聊天页流式展示 Agent 执行过程，报告页/指数页渲染完整分析结果，会话管理 UI 完整可用，视觉达到现代暗色金融终端水准。

### 范围

- 聊天页：SSE 流式渲染 + 计划卡/进度实时展示 + 快捷按钮真正可用 + 工具列表面板
- 个股报告页：渲染 `/api/v1/analyze` 完整 JSON，导航直达 + 聊天联动双入口
- 指数分析页：单指数研报 + 多指数对比表格（含 `/api/v1/index` 小改）
- 会话管理：侧边栏列表、新建/切换/删除/清空、重启后历史消息恢复
- 后端小改动 4 项（见第 6 节）

### 非目标（YAGNI）

- 前端框架/构建链（原生 HTML/JS/CSS 零构建，已确认）
- Playwright 等 UI 自动化测试（API 测试 + 手工验收清单，已确认）
- 多 worker 会话存储、用户认证（沿用子项目 A 决策）
- 图表库（K 线/走势图）：报告页只用数值 + 进度条，不引入绘图依赖
- 移动端适配：桌面浏览器优先，窄屏保证可用即可

## 3. 方案与决策记录

| 决策点 | 结论 | 理由 |
|---|---|---|
| 技术栈 | 原生 HTML/JS/CSS 零构建（ES Modules） | 保持"stock-robot api 一键启动"体验，无 node 工具链 |
| 页面架构 | SPA 单页（index.html + 多 JS 模块，视图内切换） | 会话上下文、聊天联动天然共享状态 |
| 侧边栏 | 可折叠，默认展开 | 兼顾会话可见性与专注模式 |
| 报告页排版 | 纵向研报流（概览→评分→维度→解读） | 最贴近研报阅读习惯 |
| 指数页 | 同风格：概览→Section 卡→综合研判；多指数先对比表 | 与报告页视觉统一 |
| 报告页入口 | 导航直达 + 聊天联动双入口 | 用户确认需要聊天联动 |
| 指数页 | 做（含多指数对比） | 用户确认 |
| 视觉风格 | 暗色金融终端：分层灰底 + 绯红强调色 | 用户确认方向（浏览器 mockup 评审） |
| 前端测试 | pytest 覆盖后端改动 + 手工验收清单 | 零构建下 UI 自动化成本高收益低 |

## 4. 组件设计

### 4.1 静态文件结构

```
src/api/static/
├── index.html          # SPA 壳：顶栏 + 侧边栏 + 视图容器
├── css/
│   └── app.css         # 全部样式（CSS 变量主题）
└── js/
    ├── app.js          # 入口：导航、视图切换、全局 store
    ├── api.js          # fetch 封装：JSON 请求 + SSE 事件流消费
    ├── chat.js         # 聊天视图：流式渲染、计划/进度卡片、快捷按钮
    ├── report.js       # 个股报告视图：渲染 analyze 完整 JSON
    ├── indexview.js    # 指数视图：单指数报告 + 多指数对比表格
    ├── sessions.js     # 侧边栏会话列表：新建/切换/删除/清空
    ├── markdown.js     # 轻量 markdown 渲染（~150 行）
    └── components.js   # 共享组件（评分卡、维度卡、标签徽章、工具面板）
```

### 4.2 视觉规范

- 配色：背景 `#0d1017` / 面板 `#131722` / 边框 `#262b36` / 强调色 `#e5484d`（渐变到 `#f76b15`）/ 正文 `#e6e8ee` / 次级 `#8b93a5`；成功 `#3fb68b`、警示 `#f5a623`、风险红 `#e5484d`
- 组件：圆角 8-12px、卡片式面板、状态圆点呼吸动画、SVG 线性图标（无外部依赖，断网可用）
- 字体：系统字体栈 `Inter, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei"`
- 状态徽章：成功绿 / 警示琥珀 / 风险红 / 不可用灰；指数多空标签三色徽章（bull/undervalued/positive 绿，shake/neutral 琥珀，bear/overvalued/negative 红）

### 4.3 全局 store（app.js 内模块级状态）

- `current_session_id`：随聊天/切换更新，chat 请求携带
- `current_view`：`chat` / `report` / `index` 三视图
- `reportCache`：symbol → 报告 JSON 内存缓存，同 symbol 二次打开直接渲染
- `sessions`：会话列表缓存，操作后刷新

### 4.4 视图组件

**聊天视图**：
- 消息气泡（用户右 / Agent 左）
- 执行计划卡：目标 + 步骤列表，状态圆点（等待灰 / 进行中琥珀呼吸 / 完成绿 / 失败红），progress 事件按 步骤索引 = current-1 点亮
- 工具结果卡：工具名 chip + 结果文本（markdown 渲染）+ symbol 存在时"查看完整报告 →"链接
- 快捷按钮：择时研判（发预置消息）/ 工具列表（GET /api/v1/tools 渲染面板）/ 清空会话（调 clear 端点）

**个股报告视图**（纵向研报流）：
- 头部：股票名 + 代码 + 行业 chip + 最新收盘 + 涨跌徽章（绿涨红跌，A 股习惯）
- 概览：年内最高/最低、价格位置（进度条）
- 评分卡：大号综合分 + 各维度权重条（label/score/weight）
- 维度卡 ×5：状态徽章（ok/partial/unavailable）+ 分数 + 摘要 + 指标键值 + 风险标签（红 chip）
- AI 解读卡：左侧红条引用样式

**指数视图**：
- 多指数时顶部对比表（headers/rows 动态渲染），再逐指数纵向排列报告
- 单报告：概览（PE-TTM/PB/PE 分位条）→ Section 卡（技术面/估值/资金面/宏观/舆情，带多空徽章）→ 综合研判卡（含仓位系数进度条）
- errors 中的指数渲染为错误卡

## 5. 数据流

### 5.1 聊天（改用 SSE 流式）

```
发送消息（携带 current_session_id）
  → POST /api/v1/chat/stream {message, session_id}
  → 逐事件消费：start（追加气泡+思考中占位）→ plan（计划卡，全等待态）
    → progress（步骤索引 = current-1，点亮对应步骤）→ result（摘要 + 结构化 tool_results）
    → error（错误气泡）→ done（关流、恢复输入、刷新会话列表）
```

- SSE 解析：`api.js` 实现 `consumeSSE(url, body, handlers)`，按 `data:` 行分帧，event.data 逐块缓冲防 JSON 截断
- 流中断：显示"连接中断"占位 + 重试按钮（同消息同 session 重发，重发前清理临时渲染）

### 5.2 个股报告（双入口）

- 导航直达：顶栏输入代码 → POST /api/v1/analyze → 切报告视图渲染（含加载骨架屏）
- 聊天联动：result 事件的结构化 tool_results 含 symbol → 工具结果卡带链接 → 点击调 analyze 并切视图
- 报告缓存命中直接渲染，手动刷新按钮强制重拉

### 5.3 指数分析

- 顶栏输入多个代码（空格分隔）→ POST /api/v1/index {symbols} → 对比表 + 逐报告渲染

### 5.4 会话生命周期

- 启动：GET /api/v1/sessions 渲染侧边栏；无会话显示空态引导
- 新建：POST /api/v1/sessions → 切换
- 切换：列表点击 → 本地缓存渲染；冷启动缓存未命中 → GET /api/v1/sessions/{id}/messages 恢复
- 删除：hover 显示 ✕，confirm 确认；清空：调 clear 端点后清本地渲染

## 6. 后端小改动（4 项）

| # | 改动 | 文件 | 内容 |
|---|---|---|---|
| 1 | chat 响应结构化 tool_results | `src/api/app.py` | 由 `plan.steps`（tool_name/tool_args/status）+ memory tool 消息按序配对，返回 `[{tool, symbol, status, content}]`；非流式 chat 与 stream 的 result 事件同步加。symbol 直接取步骤已提取的 tool_args，不靠文本解析 |
| 2 | 会话历史消息端点 | `src/api/sessions.py` + `app.py` | `GET /api/v1/sessions/{id}/messages` → `[{role, content, created_at}]`；SessionStore 加 `get_messages()` |
| 3 | index 多指数 + compare | `src/api/app.py` | 兼容 `symbol`（单）或 `symbols`（多）；响应加 `compare: {headers, rows}`（IndexPipeline 已计算，不再丢弃）；单指数时 compare 为 null |
| 4 | analyze 响应补涨跌幅 | `src/report/scoring.py` + `app.py` | `compute_price_info` 加 `change_pct`（最新 PriceData.change_pct） |

**不动的部分**：SSE 事件序列不变（仅 result 事件多带结构化 tool_results）；Memory/Executor/Planner 零改动；`api.app:app` 无 Agent 模式行为不变。

## 7. 错误处理

| 场景 | 前端行为 |
|---|---|
| SSE 流中断（断网/服务重启） | "连接中断"占位 + 重试按钮（同 message + 同 session_id） |
| chat 500 / error 事件 | 错误气泡 + 提示检查服务日志，不打断会话 |
| analyze/index 422（代码非法） | 输入框旁红字（复用后端 detail 文案），不切视图 |
| analyze/index 500 | 报告视图错误卡 + 重试按钮 |
| 维度 unavailable | "不可用"灰卡正常渲染（HTTP 200 路径） |
| 会话已删除（消息到达） | 静默 get_or_create 重建，不弹错 |
| 网络完全不可达 | 顶栏全局状态条"无法连接服务" |

原则：LLM/数据源失败不阻断页面（后端契约已保证 HTTP 200 + 缺省文本）；仅"用户输入非法"与"服务不可达"显式反馈。

## 8. 测试策略

### pytest（扩展，走现有 tests/api/）

| 文件 | 内容 |
|---|---|
| `tests/api/test_app.py`（扩展） | chat/stream 结构化 tool_results（含 symbol）；`GET /sessions/{id}/messages`；index 多 symbols + compare；analyze 含 change_pct；422/404 分支 |
| `tests/api/test_sessions.py`（扩展） | SessionStore.get_messages 读回 |
| `tests/report/test_scoring.py`（扩展） | compute_price_info 含 change_pct（有/无 price_data 两分支） |

### 手工验收清单（`stock-robot api` 启动后浏览器逐项过）

1. 聊天流式：发"分析 000001 的估值"→ 计划卡逐步骤点亮 → 工具结果卡出现"查看完整报告"链接
2. 聊天联动：点链接 → 报告视图完整渲染（涨跌徽章有值）
3. 导航直达：顶栏输入 600519 → 报告加载（含骨架屏）
4. 指数页：输入 000300 000905 → 对比表 + 两份报告；单指数无对比表
5. 会话：新建/切换/清空/删除；重启服务后历史消息恢复
6. 快捷按钮：择时研判、工具列表（GET /api/v1/tools 渲染面板）、清空会话真正可用
7. 侧边栏折叠/展开正常；无 Agent 模式（`uvicorn api.app:app`）页面不崩
8. 降级路径：无 LLM key 时聊天仍能执行含 6 位代码的分析并出结果

## 9. 兼容性要求

- 现有测试全部保持绿（子项目 A 的 557 passed 基线）
- CLI 行为不变（本次不触碰 cli.py）
- 无 Agent 模式（`uvicorn api.app:app`）行为不变
- API 契约向后兼容：`/api/v1/index` 的 `symbol` 单参数继续可用；chat 响应新增字段不删旧字段

## 10. 验收标准

1. 浏览器完成手工验收清单 1-8 全部通过
2. 全量 pytest 通过（含新增/扩展用例）
3. `stock-robot api` 一键启动，无构建步骤

## 11. 遗留项（延后，不在本 spec）

- SSE 心跳（长任务时防代理超时）
- 聊天内 K 线/走势图
- 移动端深度适配
