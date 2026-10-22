# 港股个股数据多源备用框架设计（已确认）

## 背景与目标

A 股财务已实现“完整包优先、备用整包替换、仅在明确等效位置补位”的选择规则。港股当前只有东财一套完整财报，行情和估值虽有回退，但语义与整包替换不同。

本轮先抽出与数据类型无关的整包选择核心，并让港股财务接入；行情、估值维持现有行为，港股真实备用数据源后续逐个接入。目标是让后续新增港股财务源时，不需要再复制一套完整性与补位规则。

## 范围与不变量

- 只改财务选择与港股财务接线；A 股主源、备用源、缓存字段、报告行为保持不变。
- 不新增港股财报备用源，不改变行情/估值选择语义，不接入港股行业或新闻。
- 不新增网络请求、并发或重试；不改变评分、风险、AI 调用、插件或项目版本。
- 旧缓存不批量迁移；新字段由正常刷新写入。
- 本轮不把“港股财务已经可以完全替换”当作既成事实。框架支持完全替换，但实际接管能力要等每个备用源单独验证后才有。

## 替换语义

- 一个来源的财务事实必须作为整包参与选择；只有整包完整时才能直接替换。
- 主源完整且未遇到更新的已观察报告期时，直接采用主源，不访问备用源。
- 主源失败或部分时，按顺序尝试备用完整包；已观察到更新报告期后，旧完整包不能替代新报告期。
- 所有包都部分时，选择事实最完整的包原样保留；只有 donor 与当前包逐字段具有同标的、同报告期、同实际起点、同累计/单期、同定义、同单位、同币种、同合并范围、同会计准则及同一修订证据时，才允许补缺。
- 抓取时间、字段同名或若干数值碰巧一致，不能代替修订与口径证据。没有 `Position` 证据的源只允许整包使用，不参与跨源补位。
- 零值和负值是有效事实；非有限值不是事实；空壳期不是“更新的部分财报”。
- 部分包不能被后续旧期数门槛重新标为完整。

## 模块边界

### 通用核心 `src/data/package_selection.py`

新增与 `FinancialData` 无关的模块：

```python
Position:
    definition: str
    unit: str
    currency: str
    start: date | None
    basis: str                 # cumulative / single / instant
    scope: str
    accounting: str
    revision: str
    calculation: str = ""

SourcePackage[RowT]:
    source: str
    rows: list[RowT]
    positions: dict[date, dict[str, Position]]

Selection[RowT]:
    package: SourcePackage[RowT] | None
    complete: bool
    supplements: dict[date, dict[str, str]]

select_package(
    providers,
    *,
    entity: str,
    fields: frozenset[str],
    entity_of: Callable[[RowT], str],
    period_of: Callable[[RowT], date],
    required: frozenset[str],
    required_periods: frozenset[date] = frozenset(),
    completeness: Callable[[SourcePackage[RowT]], bool] | None = None,
    dependencies: Sequence[frozenset[str]] = (),
    row_compatible: Callable[[RowT, Position, str], bool] | None = None,
    apply_metadata: Callable[[RowT, str, RowT], None] | None = None,
    on_error: Callable[[str, Exception], None] | None = None,
) -> Selection[RowT]:
```

核心负责：延迟调用各 provider、隔离异常、校验来源/标的/重复期间、剔除全空壳、完整包优先、新报告期保护、选择部分包、复制后补位、依赖组校验、补位来源记录。核心不 import `FinancialData`，也不包含币种、现金流披露/推算等财务特有判断。`required` 与 `dependencies` 必须是 `fields` 的子集，否则拒绝配置。

`row_compatible` 由领域适配层提供，用于确认一行数据与某个 `Position` 的口径一致；`apply_metadata` 用于补位成功后同步该指标的附随口径字段。

### 财务门面 `src/data/financial_selection.py`

- 保留现有公开名字：`NUMERIC_FIELDS`、`FinancialPackage`、`FinancialSelection`、`select_financial`，并继续可导入 `Position`。
- `FinancialPackage` / `FinancialSelection` 是通用类型的财务特化。
- `select_financial` 把 `entity_of=lambda row: row.symbol`、`period_of=lambda row: row.fiscal_quarter`、`fields=NUMERIC_FIELDS` 和当前财务行级口径校验传给核心，再把结果包装为财务类型。
- 当前币种、期间起点、累计/单期、现金流披露/推算以及 OCF 计算口径检查从原模块迁入财务回调，规则不变。
- 补位时同步 `cash_flow_basis`、`cash_flow_period_start`、`gross_margin_basis` 的行为保持不变。

A 股 `a_financial_policy.py`、`a_financial_sources.py` 和 `_fetch_financial` 调用面不改。

### 港股财务策略 `src/data/hk_financial_policy.py`

- 必需字段：营收、净利润、总资产、总权益、基本 EPS；最新期还必须具备经营现金流或每股经营现金流。
- 完整包至少包含 2 个“至少一项允许数值字段为有限值”的报告期；ROE、扣非、毛利率、普通股权益为可选事实，不因缺失拒绝整包。
- 币种未知不直接判不完整；金额趋势仍由现有期间/币种规则阻断。
- 依赖组按当前分析实际比较关系定义：
  - 营收与净利润；
  - 总资产与总权益；
  - 净利润与基本 EPS。
- 标注函数把来源、逐字段来源、位置证据、`complete/partial` 和说明写入现有 `FinancialData` 字段；说明区分“完整来源包”“单一来源未能提供完整财务包”“采用备用来源”“按逐期等效口径补位”。

### 港股接线

- 现有东财港股财务实现改名为 `_fetch_hk_eastmoney_financial`，数值、期间、币种、毛利率和现金流标记逻辑不变。
- 新 `_fetch_hk_financial` 只注册一个 provider：`东方财富港股财报`；调用 `select_financial` 后标注并返回。
- 东财包 `positions={}`，因为当前没有跨源修订证据；即使以后接入备用源，东财与备用源之间也不能凭字段同名补位。
- 备用 provider 列表本轮为空；后续加源只需追加 provider 并补充该源的完整性与口径测试。请求预算不变：不因选择器增加任何网络调用。

## 缓存与报告

- 新标注复用 `FinancialData` 已有字段，随原 `model_dump` JSON 缓存往返；不改 TTL、断路器或 Pipeline。
- 旧港股缓存缺少来源字段时保持 `unknown`，不迁移、不补造。
- 质量展示中的财务来源、财务包说明和逐字段来源展开条件从仅 A 股扩展到 A 股与港股；字段标签、布局和文案模板不变，A 股行为保持一致。
- 港股财务充足度规则保持现有“部分可用”判断，不套用 A 股部分包降级规则；无评分、无操作信号的状态不变。

## 失败处理

- 单个 provider 抛错只记录安全诊断并尝试下一来源；没有下一来源时保留已有部分包。
- 三张报表或目录的补充失败仍只影响对应事实，不丢弃其他已取得字段。
- 部分包明确标记 `partial`；不用零值填充，不把缺失写成完整。
- 空包、混入其他标的、重复报告期不参与选择。

## 测试与验收

- 通用核心用独立假行类型测试，证明不依赖 `FinancialData`；覆盖完整包优先、不访问备用、部分包选择、严格等效补位、依赖组、空壳、新报告期不回退、异常隔离。
- A 股现有 `tests/data/test_financial_selection.py`、备用源、缓存和消费方测试原样通过，证明门面行为不变。
- 新增港股策略测试：完整/部分边界、仅 1 期、最新期缺字段、OCF 缺失、可选指标缺失、币种未知、标注文案。
- 新增港股接线测试：用已保存或构造的东财行回放，断言不新增网络请求、原金额不变、来源/状态写入、缓存往返；港股充实器仍不套 A 股部分包规则。
- 数据覆盖测试断言港股报告的财报来源与说明可见。
- 按受影响范围执行 Ruff、Pyright、pytest；真实本地刷新 00700.HK，检查网页和 Markdown 的来源/选包说明；合并前执行 Full 门禁。
- 不做云端、QQ、付费模型或付费数据验收；不推送、合并、发布。

## 后续备用源接入检查清单

新增港股财务源时，必须独立验证：

- 标的身份、报告期、实际期间起点、累计/单期、币种、单位、合并范围、会计准则与修订标识；
- 是否能在不借用主源字段的情况下提供完整包；
- 失败、截断、重复、冲突和元信息缺失时的降级行为；
- 对分析/报告字段的实际影响，并补充离线响应样本与回归；
- 不把部分覆盖的年度子集、历史过旧或需付费权限的源当作已验证备用。

## 明确不做

- 不实现真实港股财务备用源接入；
- 不重构行情/估值选择器，不改变其回退语义；
- 不新增港股行业、新闻或评分能力；
- 不修改插件、版本、历史归档或已发布报告；
- 不推送、合并或发布。
