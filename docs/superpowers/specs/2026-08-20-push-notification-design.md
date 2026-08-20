# 每日定时报告推送设计

> 日期：2026-08-20
> 状态：已确认

## 背景与目标

用户选择一些指数或股票（订阅），系统在每天固定时间将对应分析报告推送到用户绑定的邮箱或企业微信。

- 推送渠道：邮箱（SMTP，完整报告）+ 企业微信（Markdown 摘要）
- 订阅管理：Web UI + API + CLI 三端
- 调度方式：API 内嵌 APScheduler（启动 `cli api` 自动调度）
- 推送内容：邮箱全文（markdown 转 HTML）、微信摘要（核心指标 + 操作信号 + 维度评分）
- 成本控制：单订阅最多 20 个标的（配置项）+ 全局开关

## 架构

新包 `src/push/`，与现有模块平级：

```
src/push/
├── __init__.py
├── models.py        # Subscription 模型
├── store.py         # 订阅存储（SQLite）
├── scheduler.py     # APScheduler 集成
├── executor.py      # 串行执行器：生成→推送
├── summary.py       # 微信 Markdown 摘要构建（复用信号系统）
└── backends/
    ├── __init__.py  # get_backend(channel) 工厂
    ├── base.py      # PushBackend 协议
    ├── email.py     # SMTP 后端（全文转 HTML）
    └── wecom.py     # 企业微信后端（access_token 缓存）
```

## 数据模型

```python
class Subscription(BaseModel):
    id: int
    name: str                    # 订阅名
    symbols: list[str]           # 标的列表（股票代码或指数代码）
    channel: Literal["email", "wecom"]
    time: str                    # "HH:MM" 每日推送时间
    enabled: bool
    created_at: datetime
```

SQLite 存储（独立文件，如 `push.db`，与 cache.db/sessions.db 同目录）：

- `subscriptions` 表：字段同模型，symbols 存 JSON 文本
- `push_runs` 表：订阅执行记录（subscription_id、执行时间、总标的数、成功数、失败详情列表），供 Web UI 查看历史

标的类型自动判定：执行时查 index_mapping（指数 CSV）——命中走 `IndexPipeline.run([target])`（默认 broad style），否则走股票 `Pipeline.run()`。用户无需手动标类型。

## 配置

config.yaml 新增 push 节（DEFAULT_CONFIG 同步增加）：

```yaml
push:
  enabled: true          # 全局开关
  max_symbols_per_subscription: 20
  email:
    smtp_host: smtp.qq.com
    smtp_port: 465
    smtp_user: xxx@qq.com
    smtp_password: <授权码>
    to_addr: xxx@qq.com
  wecom:
    corp_id: xxx
    agent_id: xxx
    secret: xxx
    to_user: "@all"
```

## 调度与执行

- API 启动（FastAPI lifespan）时若 `push.enabled` 为 true：创建 APScheduler `BackgroundScheduler`，从 store 读取全部启用订阅，按 `time` 注册每日 cron（hour/minute）；订阅 CRUD 后调用 `scheduler.reload()` 重读订阅并重新注册
- 执行器 `run_subscription(sub)`：逐标的**串行**执行"生成报告 → 推送"，每个标的完成立即发出：
  1. 判定类型 → 股票 `Pipeline.run()` / 指数 `IndexPipeline.run()`（复用 bootstrap 构建的管道实例）
  2. 微信：`summary.py` 构建 Markdown 摘要 → wecom 后端；邮箱：markdown 全文转 HTML → SMTP 后端
  3. 单标的失败：记日志 + 写入 push_runs 失败列表，继续下一个
  4. 结束后写入 push_runs 执行记录（总标的数、成功数、失败详情）
- 手动触发 `POST /subscriptions/{id}/run`：后台线程执行，立即返回"已触发"（同步执行 20-40 分钟会 HTTP 超时）

## 三端管理

### API（src/api/app.py 扩展，6 个端点）

- `GET /subscriptions` — 订阅列表
- `POST /subscriptions` — 创建（校验：symbols 非空且 ≤ max_symbols_per_subscription、time 格式 HH:MM、channel 合法）
- `GET /subscriptions/{id}` — 详情
- `PUT /subscriptions/{id}` — 修改（含 enabled 切换）
- `DELETE /subscriptions/{id}` — 删除
- `POST /subscriptions/{id}/run` — 手动触发一次推送（后台线程，返回 202 已触发）

订阅 CRUD 后调用 scheduler.reload()。

### Web UI

新增订阅视图（`src/api/static/js/subscriptions.js`，遵循现有 state.js/api.js/report.js 模式）：

- 订阅列表：名称、标的数、渠道、推送时间、启用状态、上次执行状态
- 创建表单：名称、标的输入（逗号/空格分隔）、渠道选择、时间选择
- 操作：编辑、删除、启用/停用、手动触发

### CLI

`cli subscribe` 子命令组：add / list / remove / enable / disable / run，复用 store，输出用 rich 表格。

## 推送后端

```python
class PushBackend(Protocol):
    name: str
    def send(self, *, title: str, content: str, content_type: Literal["html", "markdown"], subject: str = "") -> None: ...
```

- `EmailBackend`：smtplib + email.mime，`content_type="html"`；**后端内部**将 markdown 全文转 HTML（新增 markdown 依赖），executor 统一传 markdown 内容
- `WeComBackend`：企业微信应用消息 API（requests），`content_type="markdown"`；access_token 缓存（7200s TTL）；失败自动取新 token 重试一次

## 错误处理

- 单标的生成失败：隔离继续（管道已有此模式），失败详情进执行记录
- 推送后端失败（SMTP 拒连、微信 token 失败）：记录日志、标为失败，不做自动重试（历史可查）
- 无效代码：解析失败该标的跳过并记录
- 全局开关关闭：不注册调度任务；API/CLI 仍可管理订阅（只存不推）

## 测试

- store：CRUD 单测（临时 SQLite 文件）
- summary：摘要构建测试（mock 结果数据，验证包含名称、收盘、信号、维度评分）
- executor：mock pipeline + mock backend，验证串行顺序、单标的失败隔离、push_runs 记录
- API：订阅 CRUD + run 端点测试（FastAPI TestClient）
- 调度：mock APScheduler 验证 cron 注册与 reload

## 依赖

- 新增：`apscheduler`、`markdown`
- 已有：smtplib（标准库）、requests（企业微信 API）

## 不做的事（YAGNI）

- 不做自动重试机制
- 不做预生成缓存（方案 B）
- 不做并发生成（方案 C，架构留好口子：executor 独立于 scheduler，后续可把串行换并发）
- 不做推送节流队列
