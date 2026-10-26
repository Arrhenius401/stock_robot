# 统一数据获取规范设计（Phase 0：五组能力，长期覆盖全项目取数）

状态：2026-10-26 用户确认首轮范围为“五组能力迁移＋公共机制＋入口门禁”，并确认长期目标是所有市场数据取数最终纳入同一规范。本文落实接口字段、旧缓存兼容、迁移路线和遗留清单；业务代码尚未实现。本主题独立建文档，不追加到已收尾的 2026-10-22 港股多源项目。

## 1. 背景与目标

当前 A/HK 财务、A/HK 行情和 A 股新闻已由 `ResilientCollector` 统一处理缓存、失败等待、参考隔离和并发保护，但业务判断仍写在公共收集器里：

- `DataSource` 只约定 `supports()` 和返回列表的 `fetch()`。
- 采集诊断通过可选的 `get_collection_error()` 旁路读取。
- `ResilientCollector._valid()`、`_state()`、`_price_requirements()` 把财务、行情、新闻的规则混在同一个类里。
- `DataCollectionRecord.cache_state` 同时承担“结果是否完整”和“缓存如何等待”两种含义，无法区分正常空结果、部分成功和完全失败。
- 其他取数路径各自实现缓存、失败回退和诊断，新增能力容易产生行为差异。

本轮目标是把五组能力收敛到“统一结果入口＋公共机制＋业务策略”，同时保持现有用户可见行为不变。迁移是渐进的，不重写底层数据源、不重写专用路径。

成功标准：

- 五组能力都通过同一个 `fetch_result()` 入口返回“数据＋诊断”。
- 公共层只负责缓存、TTL、失败等待、刷新、断路器、并发和参考隔离；业务完整性由策略类判断。
- 正常空结果、部分成功、明确不适用和完全失败在记录中可区分。
- 旧缓存不需要数据库迁移，旧字段仍可读取。
- 缺少策略或新增未声明绕行入口会让门禁失败。
- 港股估值、港股新闻、行业、指数、雷达和辅助补取的既有回归继续通过。
- 长期逐步把所有市场数据取数纳入同一契约；本轮只落地五组能力，专用语义后续以策略/配置保留。

## 2. 范围

### 2.1 本轮范围

- A 股财务、港股财务。
- A 股行情、港股行情。
- A 股新闻。
- 公共采集机制、结果状态、业务策略、契约测试。
- 运行时入口约束、静态遗留清单检查和 `verify.ps1` 接线。
- 受影响的缓存、报告参考隔离和真实浏览器验收回归。

### 2.2 长期目标：全项目取数

用户已确认长期目标是所有市场数据取数最终都使用统一结果契约和策略，包括：

- 报告五组能力（本轮迁移）。
- 港股新闻、港股估值、行业/同行池。
- 同行财务、年度 ROE、总股本、名称解析等辅助补取。
- 指数/策略、ETF、基准等指数链路取数。
- 雷达数据提供器、回测行情、静态数据刷新/发布任务。

统一定义的是“取数结果与诊断契约”：数据、来源、采集时间、结果状态、缓存/参考约束。专用语义不取消，迁移时保留为策略或配置：

- 港股新闻：单次双查询预算、无外层重试、部分查询成功、3600 秒专用缓存。
- 港股估值：组件级补位、逐项日期、非正值语义、`last_complete` 和专用 TTL。
- 行业：资格池复核、`peer_hk_industry_v2`、3600 秒缓存。
- 回测：区间完整性、连续性、独立缓存。
- 雷达：调度、队列和租约继续由雷达层负责；数据提供器逐步接入取数契约。
- 静态刷新：保留发布/保留旧快照语义，按取数契约记录来源和尝试状态。

不属于取数层、保持独立：

- 雷达调度、队列、租约和自动重试。
- 评分公式、操作信号和报告文案。
- AI 事实输入和提示词。
- 配置管理、版本管理和发布流程。

### 2.3 本轮不迁移、后续迁移目标

- 港股新闻、港股估值、行业、指数/策略、雷达数据提供器、回测行情、辅助补取、静态数据刷新。
- 本轮保持其业务逻辑、缓存键、TTL、调度和回退不变；只作为静态清单/附录中的遗留路径。
- 每轮迁移前单独确认范围；不在本轮计划中一次性重写。

### 2.4 长期硬边界（仍不做）

- 不新增数据源、依赖、费用、模型调用或定时任务，除非单独确认。
- 不改变评分公式、操作信号、报告文案或 AI 事实输入范围。
- 不把雷达调度改成报告式的“后续请求重试、无后台轮询”。
- 不为了统一而抹平专用缓存、合并或连续性语义。
- 不做无确认的数据库迁移或旧缓存批量改写。

### 2.5 迁移路线（建议）

| 阶段 | 范围 | 约束 |
| --- | --- | --- |
| Phase 0（本轮） | 五组能力、公共机制、契约测试、入口门禁 | 只改本轮范围；专用路径不动 |
| Phase 1 | 港股新闻、港股估值、行业 | 先接入结果/诊断契约，保留专用缓存、合并、逐项日期和查询预算 |
| Phase 2 | 指数/策略、辅助补取、名称解析、静态数据刷新 | 指数缓存和发布语义保留；辅助补取逐步接入 |
| Phase 3 | 雷达数据提供器、回测行情 | 雷达调度/队列继续独立；回测保留区间完整性、连续性和独立缓存 |

每阶段单独确认设计和验收；本设计是总规范，Phase 0 的实施计划不包含后续阶段的代码改动。迁移时不能让公共层的默认重试、TTL 或缓存键覆盖专用语义。

## 3. 不可回退的既有行为

1. 正常 TTL：财务 7 天、行情 1 天、A 股新闻 6 小时。
2. 故障或未知缺项降级最多等待 1800 秒，由后续请求重试，无后台轮询；手动刷新跳过等待。
3. 正常无结果、明确不适用使用正常 TTL。
4. 当前事实、完整快照、失败等待分离；失败不能抹掉较新的有效事实。
5. 历史参考按原采集时间限龄：财务 30 天、行情 7 天、A 股新闻 24 小时；新闻条目仍受原 30 天窗口限制。
6. 参考只供展示，不进入评分、操作信号或 AI 事实输入；完整报告存档保留参考。
7. 财报不跨期补值，行情不跨源拼接，未知时间不伪造。
8. 港股估值补源、负值语义、逐项日期、专用缓存行为不退步。
9. 不默认增加数据源、依赖、费用或改变评分公式。

## 4. 现状与问题

### 4.1 五组能力的共同路径

`Pipeline.collect()` 先判断 `core.resilient_collection.supports(market, data_type)`。五组能力进入 `ResilientCollector.collect()`，其余进入旧路径或专用方法。`ResilientCollector` 已实现：

- `latest`、`last_complete`、`last_attempt` 三键缓存。
- 失败保护和较新事实保护。
- 参考限龄、新闻参考窗口和同标的同类锁。
- 断路器、每源两次尝试、手动刷新跳过等待。

因此本轮不重写这些机制，只把业务判断外移，并让入口和结果更明确。

### 4.2 需要解决的问题

- 空列表无法区分“正常没有数据”和“请求失败”。A 股新闻即使零条也返回带 `_raw_sentiment` 的 `NewsData`，不能只看外层列表是否为空。
- `cache_state=degraded` 同时表示部分成功和完全失败，无法直接驱动“完整快照保护”和“失败等待”的不同处理。
- 适配器诊断是旁路读取，调用方需要在 `fetch()` 之后额外读取 `get_collection_error()`，新适配器容易漏接。
- 业务规则分散，新增数据类型时可能复制财务/行情/新闻的判断。
- 旧路径缺少统一声明，新增绕行取数不会被检查。

## 5. 方案取舍

### 5.1 统一结果入口

| 方案 | 做法 | 取舍 |
| --- | --- | --- |
| A（推荐） | 新增 `DataSource.fetch_result()`，返回 `CollectionResult(rows, record)`；旧 `fetch()` 保留给遗留路径 | 兼容现有适配器；五组能力获得原子“数据＋诊断”；新增入口可静态检查 |
| B | 直接把 `fetch()` 的返回类型改成结构化结果 | 所有遗留调用、测试假实现和指数/回测路径都要同步改，首轮风险过大 |
| C | 不改入口，只把 `_valid()`/`_state()` 抽成策略 | 改动最小，但诊断仍是旁路，无法满足“数据和诊断一次返回”和入口门禁 |

选择 A。`CollectionResult` 只是传输契约；缓存感知的 `CollectionOutcome` 继续由公共收集器返回，不引入第二套缓存语义。

### 5.2 结果状态

| 方案 | 做法 | 取舍 |
| --- | --- | --- |
| A（推荐） | `DataCollectionRecord` 新增 `result_state`，保留 `cache_state` 作为旧投影 | 新语义清晰；旧缓存、旧测试和旧报告读取不受影响 |
| B | 直接扩展 `cache_state` 枚举 | 旧代码和旧缓存需要同步理解新值，容易把“部分”和“失败”再次混用 |
| C | 只在收集器内部维护状态，不写入模型 | 无法随缓存往返，参考和诊断不能稳定保留 |

选择 A。`result_state` 是业务结果，`cache_state` 是兼容投影；新逻辑以 `result_state` 为准。

## 6. 架构与职责

```text
AkShareAdapter / 其他五组能力适配器
        │  fetch_result(symbol, **kwargs)
        ▼
CollectionResult(rows, record)
        │
        ▼
ResilientCollector
  ├─ CollectionPolicy.validate / classify
  ├─ CacheManager latest / last_complete / last_attempt
  ├─ 断路器、同标的同类锁、刷新
  ├─ DataReference 构造与限龄
  └─ CollectionOutcome(rows, record, reference)
        │
        ▼
Pipeline.collect() → AnalysisContext
```

### 6.1 模块边界

- `src/data/base.py`：`DataSource`、`CollectionResult`、`fetch_result()` 默认兼容桥。
- `src/data/schemas.py`：`ResultState`、`DataCollectionRecord.result_state`；`DataReference` 不变。
- `src/data/collection_policies.py`：五组能力的策略注册表和具体策略。
- `src/core/resilient_collection.py`：公共缓存/状态机；不再包含财务、行情、新闻的业务判断。
- `src/core/collection_contract.py`：五组能力常量、遗留清单、运行时契约校验、静态检查算法；不依赖具体业务模型。
- `scripts/check-data-entrypoints.py`：静态检查 CLI 包装。
- `src/data/akshare.py`：抽出私有采集实现，`fetch_result()` 为主入口，`fetch()` 仅作为遗留兼容包装；底层选源和解析算法不变。
- `src/core/pipeline.py`：五组能力路由、旧路径运行时保护。
- `scripts/verify.ps1`：接入静态检查和测试映射。

### 6.2 依赖方向

`collection_policies` 可以依赖 `schemas` 和 `validation`；`resilient_collection` 依赖 `collection_policies` 和 `collection_contract`；`collection_contract` 不依赖业务模块，避免循环导入。`base.py` 只依赖 `schemas.DataCollectionRecord`。

## 7. 数据模型

### 7.1 结果状态

`ResultState` 取值：

| result_state | 含义 | cache_state 旧投影 | 正常 TTL | 更新 last_complete | 返回历史参考 |
| --- | --- | --- | --- | --- | --- |
| `complete` | 策略确认事实完整、覆盖满足 | `complete` | 是 | 是 | 否 |
| `partial` | 有有效事实，但有已知缺项、未知缺项或覆盖不足 | `degraded` | 否，等待 `min(正常 TTL, 1800)` | 否；若旧 latest 完整则先提升 | 是 |
| `empty` | 请求与解析成功，策略确认没有符合条件的数据 | `complete` | 是 | 是 | 否 |
| `not_applicable` | 有业务依据的不适用 | `not_applicable` | 是 | 否 | 否 |
| `failed` | 无有效事实，或请求/解析失败 | `degraded` | 否，仅写 `last_attempt` | 否 | 是（旧 latest/last_complete） |
| `unknown` | 旧缓存未记录结果状态 | `unknown` | 按策略验证后再决定 | 否 | 视验证结果 |

关键约束：

- `empty` 不能由“外层列表为空”推断。A 股新闻必须有 `NewsData._raw_sentiment`：零条且无错误才是 `empty`；零条且有错误是 `failed`；有内容且有错误是 `partial`。
- 财务和行情没有“空列表即正常”的默认规则。零条数据只有在适配器给出明确 `not_applicable` 诊断时才判为 `not_applicable`，否则为 `failed`。
- `partial` 必须至少有一个可验证事实；没有任何有效事实施是 `failed`。
- `unknown` 只用于读取旧缓存；新写入不使用。
- `result_state` 不直接展示给用户，不改变报告文案；报告继续使用来源、原采集时间和错误说明。

### 7.2 `DataCollectionRecord`

保留现有字段：

- `source`：来源标签。
- `collected_at`：原成功采集时间；失败记录留空。
- `cache_status`：`live/cache/stale/unknown`，表示本次值从哪来。
- `error_kind`、`error_message`：安全诊断。
- `cache_state`：旧兼容投影。

新增：

- `result_state: Literal["complete", "partial", "empty", "not_applicable", "failed", "unknown"] = "unknown"`。

时间语义：

- 成功采集（含 `complete/partial/empty/not_applicable`）才写 `collected_at`。
- 失败不写 `collected_at`；`last_attempt` 缓存的 `created_at` 表示尝试/等待时间，不能冒充原采集时间。
- 缓存命中只改 `cache_status="cache"`，不重写 `collected_at`，也不续期。
- 历史参考使用原记录的 `collected_at`，不能改成当前时间。

允许的 `(result_state, cache_state)` 组合：

- `(complete, complete)`
- `(partial, degraded)`
- `(empty, complete)`
- `(not_applicable, not_applicable)`
- `(failed, degraded)`
- `(unknown, *)`：仅旧缓存；读取后按策略验证/推导，不直接当新结果写入。

其他组合视为契约矛盾。公共收集器在写入前检查；矛盾时不写 `latest`/`last_complete`，记录安全诊断并降级，避免污染缓存。

### 7.3 `CollectionResult`

```python
@dataclass(frozen=True)
class CollectionResult:
    rows: list[Any]
    record: DataCollectionRecord | None = None
```

- `rows` 是适配器返回的 Pydantic 模型列表；`record` 只承载诊断和来源，不承载缓存/参考状态。
- `DataSource.fetch_result()` 默认实现调用 `fetch()` 后读取 `get_collection_error()`，供未迁移的遗留适配器兼容。
- 五组能力的数据源必须显式覆盖 `fetch_result()`；运行时在注册/采集时检查。默认兼容桥不能用于五组能力。
- `AkShareAdapter` 把现有 `fetch()` 主体抽成私有实现，`fetch_result()` 和 `fetch()` 都调用该实现，二者互不调用。`fetch_result()` 返回 `CollectionResult`；`fetch()` 只返回 `rows`，供指数、回测等遗留路径使用，`get_collection_error()` 行为保持不变。
- 适配器不决定业务 `result_state`；公共层根据策略和诊断计算。若适配器诊断里带了 `result_state`，公共层不把它当作权威结果，矛盾时按契约错误处理。

### 7.4 `DataReference`

保持现有字段和隔离语义：`financial/price/news` 三类、`record`、对应数据、`reason`。本轮不扩展成全业务通用模型。

## 8. 公共采集器

### 8.1 采集流程

`ResilientCollector.collect(symbol, market, kind, refresh)` 继续按 `(symbol, kind)` 串行化。`_collect()`：

1. 读取 `latest`、`last_complete`、`last_attempt`；旧记录缺 `result_state` 时按策略验证/推导。
2. 若非刷新且 `last_attempt` 仍在等待期：返回无当前事实、`cache_status="cache"` 的记录，并从旧快照构造参考。
3. 若非刷新且 `latest` 新鲜且无更新的失败等待：返回 `latest`；`partial` 时附 `last_complete` 参考。
4. 若刷新或断路器未打开：按注册顺序遍历来源，每个来源按策略的 `source_attempts` 尝试（本轮五组为 2 次，间隔 1 秒）。
5. 对每个来源调用 `source.fetch_result(symbol, **kwargs)`：
   - 检查策略存在、来源显式实现 `fetch_result()`；否则按契约错误安全降级，不写缓存。
   - 取得 `CollectionResult`，把 `rows` 和诊断交给策略。
6. 策略返回 `not_applicable`：写 `latest`（正常 TTL），断路器记成功，返回已有 `rows` 或 `None`。
7. 策略返回 `failed`：保留失败诊断，尝试下一个来源/下一次尝试，不覆盖 `latest`/`last_complete`。
8. 策略返回 `complete/partial/empty`：
   - 校验迟到数据：财务最新报告期不能早于 `latest`，行情最新交易日不能早于 `latest`。
   - 构造新记录：来源、`collected_at=now`、`cache_status="live"`、保留诊断错误字段、设置 `result_state` 和 `cache_state`。
   - `partial` 且没有错误说明时补安全说明；`empty` 不写错误。
   - 写入缓存；`complete/empty` 更新 `last_complete`；`partial` 保留旧完整快照；`not_applicable` 不更新 `last_complete`。
   - 断路器记成功；`partial` 附历史参考；返回当前 `rows`（新闻空快照保留 `NewsData`，财务/行情空数据返回 `None`）。
9. 所有来源失败：断路器记失败；只写 `last_attempt`；从 `latest`/`last_complete` 构造参考；返回 `None` 和失败记录。

### 8.2 三类缓存键

- `latest`：当前可展示事实（完整、部分、正常空、不适用）。
- `last_complete`：最近一次 `complete/empty` 快照；用于 `partial`/`failed` 时的历史参考。
- `last_attempt`：失败等待；只记录失败/等待状态，不承载当前事实。

写入规则：

- 失败且无当前事实施时只写 `last_attempt`；不能删除或覆盖 `latest`/`last_complete`。
- `partial` 覆盖 `latest` 前，若旧 `latest` 是 `complete`，先按既有 `_can_promote()` 规则提升到 `last_complete`。
- `complete/empty` 写入后复制到 `last_complete`。
- 迟到写入保护继续使用同标的同类锁和“不比 `latest` 更旧”的比较。
- 缓存写入异常只记录日志，不影响本次返回。

### 8.3 TTL

正常 TTL 从现有配置读取：

- 财务：`data.cache_ttl.quarterly`，默认 604800 秒。
- 行情：`data.cache_ttl.daily`，默认 86400 秒。
- A 股新闻：`data.cache_ttl.news`，默认 21600 秒。

等待 TTL：

- `partial`、`failed`：`1800` 秒；若正常 TTL 更短，取更短值；正常 TTL 为负（无限）时仍取 1800。
- `complete`、`empty`、`not_applicable`：正常 TTL。
- 等待命中不续期；手动刷新跳过等待；无后台轮询。

### 8.4 参考与隔离

- 参考只在 `partial` 或 `failed` 时构造；来源快照必须是 `last_complete` 或较新的 `latest`。
- 参考限龄沿用：财务 30 天、行情 7 天、A 股新闻 24 小时（按原 `collected_at`）。
- 新闻参考必须带 `_raw_sentiment`，并过滤到香港日期最近 30 天；没有原始条目日期时不生成参考，不能用抓取日期冒充发布时间。
- 参考的 `record` 深拷贝，保留原来源、原采集时间和结果状态；当前事实与参考不合并。
- `AnalysisContext.reference_data` 的消费和模型隔离不变；报告存档继续保留参考。

### 8.5 刷新与断路器

- `refresh=True` 跳过 `last_attempt` 和 `latest` 的新鲜判断，直接尝试来源；仍保留旧快照用于参考/迟到保护。
- 断路器打开且非刷新时跳过来源；有旧快照则返回 `cache_status="stale"` 和参考，没有则返回失败记录。
- 五组能力继续每源两次尝试；重试次数由策略的 `source_attempts` 提供（本轮五组均为 2），公共层不再硬编码。后续迁移港股新闻、港股估值等专用能力时可以设为 1，避免把双查询预算或单次采集放大。

## 9. 业务策略

策略接口（具体签名在实施计划中固定）：

```python
class CollectionPolicy(Protocol):
    kind: str
    ttl_key: str
    reference_age: int
    source_attempts: int

    def validate(self, symbol: str, rows: list[Any], now: datetime) -> bool: ...
    def classify(self, symbol: str, rows: list[Any],
                 diagnostic: DataCollectionRecord | None,
                 previous_rows: list[Any] | None,
                 now: datetime) -> ResultState: ...
    def reference_rows(self, rows: list[Any], now: datetime) -> list[Any] | None: ...
    def fetch_constraints(self, previous_rows: list[Any] | None,
                          now: datetime) -> dict[str, Any]: ...
```

`fetch_constraints` 由行情策略返回 `minimum_points` 和 `minimum_end_date`，财务/新闻策略返回空字典；公共层只负责把约束传给适配器，不自行判断覆盖。

策略注册表以 `(market, kind)` 为键，五组能力必须齐全：

- `("a-shares", "financial")`、`("hk", "financial")`
- `("a-shares", "price")`、`("hk", "price")`
- `("a-shares", "news")`

缺少任一策略或出现额外未迁移键时，构造阶段视为契约错误。`supports()` 与注册表共用同一个 `MIGRATED_CAPABILITIES` 常量，避免漂移。

### 9.1 财务策略

- 基本校验：非空、同标的、报告期唯一、报告期不晚于今天、至少一个有限财务事实。沿用 `data.validation.financial_has_facts`。
- 完整性：优先使用最新行的 `financial_selection_status`（`complete/partial`）；缺失时用 A 股/港股各自的 `package_complete()` 判断。A/HK 规则不变。
- `complete`：完整包；`partial`：部分包但有可用事实。
- 空数据：除非适配器明确给出 `not_applicable`（例如确认暂无披露），否则 `failed`；不跨期补值。
- 诊断错误不自动降级：如果备用源已给出完整包，仍可为 `complete`，但保留错误说明。
- TTL 键 `quarterly`；参考限龄 30 天。

### 9.2 行情策略

- 基本校验：OHLCV 有限、价格为正、成交量非负、高低价关系成立、交易日唯一且不晚于今天。沿用现有 `_valid` 行情分支。
- 覆盖：沿用现有要求——至少 60 个点；若已有 `latest`/`last_complete`，最新交易日不能早于既有最新日，点数不能少于既有可用点数。A 股窗口 250 天、港股 365 天保持现状。
- `complete`：覆盖满足；`partial`：覆盖不足但仍有有效行；`failed`：无有效行或校验失败。
- 空数据：只有明确 `not_applicable` 才是正常不适用；否则 `failed`。
- 整源选择仍由适配器 `select_history` 完成；策略只验证覆盖，不拼接不同来源。
- TTL 键 `daily`；参考限龄 7 天。

### 9.3 A 股新闻策略

- 基本校验：恰好一个 `NewsData`、标的匹配、`date` 不晚于今天。
- 必须有 `_raw_sentiment`；没有原始舆情对象时无法核对条目日期，判 `failed` 并等待后续刷新，不能猜成正常空结果。
- `_raw_sentiment.items` 为空且无诊断错误：`empty`，正常 TTL。
- `items` 为空且有诊断错误：`failed`。
- `items` 非空且有诊断错误（例如新闻或公告其中一路失败）：`partial`。
- `items` 非空且无诊断错误：`complete`。
- 条目日期出现未来或非法时间时，不能作为有效当前事实；按 `failed` 处理，不伪造时间。
- 当前事实窗口沿用适配器现有的近 30 天请求过滤，不在缓存读取侧新增二次裁剪；参考窗口仍按 30 天过滤。
- TTL 键 `news`；参考限龄 24 小时。

## 10. 旧缓存兼容

- 新字段 `result_state` 有默认值 `"unknown"`；旧缓存反序列化不需要迁移。
- 读取旧记录时：
  - `cache_state=complete`：若策略确认是正常空结果（A 股新闻零条且有原始舆情对象）则为 `empty`，否则 `complete`。
  - `cache_state=degraded`：有可验证事实则为 `partial`，否则 `failed`。
  - `cache_state=not_applicable`：`not_applicable`。
  - `cache_state=unknown`：按策略验证；不能验证时视为未命中，不把空列表猜成正常无结果。
- `last_attempt` 旧记录缺 `result_state` 时按 `failed` 处理，避免旧失败记录长时间占用正常 TTL。
- 旧缓存只在读取时推导，不就地改写；下一次成功采集写入新字段。
- 读取时若新记录的 `result_state` 与 `cache_state` 矛盾，视为未命中并记录日志，不使用该缓存；旧记录的 `result_state=unknown` 不受此限制。
- 缓存键、TTL 键和数据库结构不变。

## 11. 入口门禁

### 11.1 运行时

- `Pipeline.collect()` 对五组能力继续优先进入 `ResilientCollector`；旧路径增加显式保护：若 `(market, data_type)` 属于 `MIGRATED_CAPABILITIES`，记录契约错误并安全降级，不进入旧取数循环。
- `ResilientCollector` 构造时检查五组策略齐全；采集每个来源前检查：
  - 策略存在；
  - 来源类显式覆盖 `fetch_result()`，不是 `DataSource` 默认桥。
- 缺少策略或入口时：
  - 记录 `error_kind="contract_error"`；
  - 返回 `failed` 结果；
  - 不写 `latest`/`last_complete`/`last_attempt`，避免污染缓存。
- 写入前检查 `(result_state, cache_state)` 组合；矛盾时按契约错误处理。
- 增加运行时契约测试：五组能力在真实 `Pipeline.collect()` 流程中只调用 `fetch_result()`，不调用 `fetch()`；遗留路径仍可调用 `fetch()`。

### 11.2 静态遗留清单

`src/core/collection_contract.py` 维护 `LEGACY_FETCH_SITES`，条目为“文件＋函数＋能力（市场/数据类型）＋原因＋计划阶段”。当前清单：

| 文件 | 函数 | 能力 | 原因 | 计划阶段 |
| --- | --- | --- | --- | --- |
| `src/data/base.py` | `DataSource.fetch_result` | 遗留适配器兼容桥 | 仅允许未迁移来源使用；五组能力必须显式覆盖 | 随来源迁移逐步淘汰 |
| `src/core/pipeline.py` | `Pipeline.collect.fetch_one` | `valuation`、`industry`（动态类型） | 旧路径；运行时保护禁止五组能力进入 | Phase 1 |
| `src/core/pipeline.py` | `Pipeline._collect_hk_news` | `hk/news` | 港股新闻专用双查询预算，保留专用缓存 | Phase 1 |
| `src/index/collector.py` | `IndexDataCollector.collect._fetch_price` | `index_price` | 指数专用采集 | Phase 2 |
| `src/index/collector.py` | `IndexDataCollector.collect._fetch_valuation` | `index_valuation` | 指数专用采集 | Phase 2 |
| `src/index/collector.py` | `IndexDataCollector.collect._fetch_capital_flow` | `index_capital_flow` | 指数专用采集 | Phase 2 |
| `src/index/collector.py` | `IndexDataCollector.collect._fetch_macro` | `index_macro` | 指数专用采集 | Phase 2 |
| `src/index/collector.py` | `IndexDataCollector.collect._fetch_sentiment` | `index_sentiment` | 指数专用采集 | Phase 2 |
| `src/backtest/data.py` | `HistoricalPriceProvider.fetch_stock` | `a-shares/price` | 回测自管区间、连续性和缓存语义，不共享报告缓存 | Phase 3 |

`scripts/check-data-entrypoints.py` 用 AST 扫描 `src/`：

- 查找 `*.fetch(` 且带 `data_type=` 关键字或 `**` 关键字展开的调用；字面量类型按清单匹配，动态类型只允许清单中声明的动态条目。
- 只允许 `src/core/resilient_collection.py` 的 `ResilientCollector._collect` 调用 `*.fetch_result(`；其他生产代码新增调用视为未声明绕行。适配器只定义 `fetch_result()`，不调用其他来源的 `fetch_result()`。
- 清单中的条目必须仍然存在；删除遗留路径后未更新清单也失败，强制维护清单。
- 违规输出文件、行号、函数和能力，返回非零退出码。

局限：

- 静态检查覆盖带 `data_type` 关键字或 `**` 关键字展开的 `DataSource.fetch()`，但不能可靠识别所有动态分派、直接调用 provider 函数或既没有 `data_type` 也没有关键字展开的调用。
- 不通过 `DataSource.fetch()` 的遗留路径（见附录 B）不在静态清单中，靠运行时测试、现有回归和代码审查保护。
- 因此门禁必须与运行时契约测试配合，不能宣称静态检查覆盖全部动态调用。

### 11.3 `verify.ps1` 接线

- 新增 `scripts/check-data-entrypoints.py` 的测试映射：`tests/test_data_entrypoints.py`。
- 实际执行时，只要本次有 Python 目标或显式 `-Scope Full`，就在 Pyright 之后、pytest 之前运行静态检查；失败立即退出，不继续测试。
- `-PlanOnly` 不执行检查；`-Scope Changed` 不因检查自动扩大为全量。
- 为契约相关文件补充测试映射：
  - `src/data/base.py`、`src/data/schemas.py`、`src/data/collection_policies.py`：`tests/data`、`tests/core`。
  - `src/data/akshare.py`：`tests/data`、`tests/core`、`tests/report`。
  - `src/core/resilient_collection.py`、`src/core/pipeline.py`：`tests/core`、`tests/data`、`tests/report`、`tests/agent`。
  - `src/core/collection_contract.py`：`tests/core`、`tests/data`、`tests/test_data_entrypoints.py`。
  - `scripts/check-data-entrypoints.py`：`tests/test_data_entrypoints.py`。

## 12. 错误处理

- 适配器异常：保留安全诊断（超时/请求失败），不记录密钥、URL 参数或原始响应正文。
- 单个来源失败：尝试下一来源；全部失败才写 `last_attempt`，不覆盖当前事实。
- 诊断矛盾：按契约错误安全降级，不写缓存；记录日志便于定位。
- 缓存损坏：沿用现有反序列化异常处理，视为未命中。
- 缓存写入失败：只记录日志，不影响本次分析结果。
- 断路器：成功/失败记录规则保持现状；缓存命中不触达上游。
- 迟到响应：同标的同类锁和“不早于 latest”比较共同阻止旧结果覆盖新事实。

## 13. 测试与验收

### 13.1 契约与单元测试

- 五类状态：`complete/partial/empty/not_applicable/failed` 的定义和非法组合。
- TTL：正常值、1800 秒边界、正常 TTL 无限、等待不续期、手动刷新跳过等待。
- 状态转换：完整→部分→失败→恢复；较新事实和完整快照不被抹掉。
- 缓存往返：来源、原采集时间、`result_state`、A 股新闻 `_raw_sentiment`。
- 旧缓存：缺 `result_state`、`cache_state=unknown`、空列表不被猜成正常无结果。
- 参考：30/7/1 天限龄、未知时间不伪造、新闻 30 天窗口。
- 迟到写入：财务报告期和行情交易日不能回退。
- 参考隔离：不进入评分、信号和模型；完整存档保留。
- 策略：财务完整性、行情覆盖、新闻零条/部分失败/未来日期。
- 入口：缺少策略、缺少 `fetch_result()`、新增未声明 `fetch()`、清单失效。
- 适配器：`AkShareAdapter.fetch_result()` 与 `fetch()` 返回一致，诊断线程隔离。

### 13.2 回归

至少覆盖：

- `tests/core/test_resilient_collection.py`
- `tests/core/test_financial_cache_facts.py`
- `tests/core/test_pipeline_routing.py`
- `tests/core/test_hk_news_pipeline.py`
- `tests/core/test_pipeline_hk_industry_cache.py`
- `tests/report/test_reference_quality.py`
- `tests/agent/test_reference_isolation.py`
- `tests/data/test_akshare.py`
- `tests/data/test_a_financial_fallback.py`
- `tests/data/test_price_source_validation.py`
- `tests/data/test_hk_shared_sources.py`
- `tests/data/test_hk_valuation_history.py`
- `tests/data/test_hk_news.py`
- `tests/data/test_hk_hsics_peers.py`
- `tests/index/test_collector.py`
- `tests/backtest` 相关行情测试

实施前再按实际改动精确确认。日常按范围执行 Ruff、Pyright、pytest；pytest 使用 PID＋GUID 唯一临时目录。提交前执行 Changed/Staged 门禁和 `git diff --cached --check`；不自动触发 Full。

### 13.3 真实浏览器验收

由于影响真实报告取数，交付前必须通过：

```powershell
./.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765
```

完成至少一次 A 股和一次港股真实发送与响应验收，检查：

- 报告能正常生成，数据覆盖/来源/时间可见。
- 正常、部分、失败和手动刷新路径没有静默错误。
- 历史参考只在预期场景出现，且不进入评分/信号/AI 事实。
- 港股估值、新闻和行业专用路径没有回归。
- 不用 Mock 页面冒充真实验收。

全量检查留在合并、发布前或用户明确要求时执行。

## 14. 实施边界与风险

- 首轮只迁移五组能力和公共机制；港股新闻、估值、行业、指数、雷达、回测和辅助补取按 2.5 的迁移路线逐步纳入，不在本轮实施。
- 新增字段和入口必须保持旧调用兼容；任何需要改变用户可见规则的情况回到用户确认，不能隐藏在实现中。
- 多阶段迁移必须避免“一次性全项目重写”；每阶段独立确认范围、验收和回退边界，专用缓存/合并/调度语义不能被公共层默认行为覆盖。
- 最大风险是测试假实现未实现 `fetch_result()` 导致构造期检查失败；实施计划需一次性更新相关测试替身，并用契约测试覆盖。
- 第二风险是静态检查误报 provider 的同名 `fetch()`；检查器只处理带 `data_type` 关键字或 `**` 关键字展开的 `DataSource.fetch()`，并通过运行时测试补充覆盖。
- 第三风险是旧缓存状态推导过宽或过窄；以“空列表不猜正常、无原始新闻对象不出参考、旧失败不占正常 TTL”为硬约束。

## 附录 A：遗留 `fetch()` 清单说明

清单是迁移台账，不是永久豁免。修改遗留取数行为时，必须二选一：

1. 迁移该能力到 `fetch_result()` 和策略；
2. 在清单中更新文件、函数、能力、原因和计划阶段，并在审查中说明兼容例外。

迁移完成后删除对应条目；新增条目不等于长期保留。不能因为主题相近而批量迁移整个模块。

## 附录 B：不经过 `DataSource.fetch()` 的遗留路径

以下路径本轮保持业务逻辑不变，不在静态 `fetch()` 清单内；它们是 2.5 的后续迁移目标，不是永久例外：

- `src/data/peer_comparison_fetch.py`：同行财务/估值批量取数，独立 `peer_financial`/`peer_valuation` 快照（Phase 2）。
- `src/data/peer_comparison.py`、`src/data/financial_scoring_facts.py`：同行和年度 ROE 补取（Phase 2）。
- `src/data/enrichers/valuation_enricher.py` 的 `get_total_shares()`：总股本补充（Phase 2）。
- `src/utils/symbols.py` 的 `resolve_name()`：名称解析（Phase 2）。
- `src/data/hk_hsics_peers.py`：港股行业同行池及 `peer_hk_industry_v2` 快照（Phase 1）。
- `src/data/hk_news.py`：港股关键词新闻双查询（Phase 1）。
- `src/data/hk_eastmoney.py`、`src/data/hk_alternatives.py`：港股快照/历史底层提供器（Phase 1）。
- `src/index/collector.py` 的策略分支、`src/index/strategy_data.py`、`src/index/valuation_data.py`：指数/策略专用缓存（Phase 2）。
- `src/data/hk_stock_connect.py`、`src/data/hk_hsics_industry.py`、`src/data/industry_mapping_builder.py`：港股通成员、HSICS 行业和 A 股行业映射的静态刷新/发布（Phase 2）。
- `src/radar/data.py`、`src/radar/overseas.py`：雷达数据提供器（Phase 3）；`src/radar/collector_store.py` 的持久化队列、租约和调度继续保持独立，不属于取数契约。
- `src/backtest/data.py` 的基准 `_ak_csindex` 和回测行情：Phase 3 迁移取数契约，保留区间完整性、连续性和独立缓存。

这些路径的失败回退、TTL 和调度语义不在本轮统一范围内；它们继续由现有测试和真实验收保护。后续迁移时必须保留各自的专用语义，不能直接套用本轮五组能力的重试、TTL 或参考规则。
