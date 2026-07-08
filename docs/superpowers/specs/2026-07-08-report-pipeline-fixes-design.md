# 研报管道数据缺失与 LLM 幻觉修复设计

## 目标

让 `stock-robot analyze` 在真实网络与真实数据格式下产出**有数据支撑、不编造**的报告。当某维度确实无法采集到数据时，报告应诚实标注“暂时不可用”，而不是让 LLM 凭训练记忆虚构分析。

## 现状（问题复现）

一次 `stock-robot analyze sh600350` 的真实输出暴露了以下问题：

- 财务 12 行数据全部被跳过：`could not convert string to float: '3.54亿'`
- 行情、行业采集失败：`RemoteDisconnected('Remote end closed connection without response')`
- 五个维度 LLM 解读全部失败：`生成 X 解读失败: 'revenue' is undefined`
- 综合总结生成了一段**没有任何真实数据支撑**的“车轱辘话”

## 根因分析

经过逐层排查，共定位 **4 个独立 Bug + 1 个设计缺陷 + 1 个渲染瑕疵**。因果链为：**数据层缺失（Bug 1、2）→ 解读层崩溃（Bug 3、4）→ 总结层幻觉（缺陷 5）**。

| 编号 | 位置 | 根因 |
|---|---|---|
| Bug 1 | `src/data/akshare.py:73-86` | AkShare `stock_financial_abstract_ths` 返回的数值是中文单位字符串（`'3.54亿'`、`'7217.13万'`），裸 `float()` 抛异常，`except`（第 90 行）静默跳过**每一行**，财务数据恒为空 |
| Bug 2 | `src/data/akshare.py` | 价格、行业等请求遇到 `RemoteDisconnected` 无重试；且 `pipeline.py:107` 以 `max_workers=5` 并发冲击同一上游，加剧远端掐断 |
| Bug 3 | `src/llm/prompt_templates/*` | `technical`、`valuation`、`industry`、`sentiment` 的 `_openai`/`_claude` 共 8 个模板是**财务模板的逐字节拷贝**，全部引用 `revenue`。`pipeline.py:180` 用 `**result.metrics` 渲染时，非财务维度的 metrics 无 `revenue`，`revenue/1e8` 对 undefined 做算术触发 `'revenue' is undefined`。**即使数据完好，5 个维度中也有 4 个恒定失败** |
| Bug 4 | `src/core/pipeline.py:180` | 即使是正确的财务模板，在财务数据 `unavailable`（metrics 为空）时同样会因 Jinja 默认 `Undefined` 的算术报错。缺少“不可用维度跳过 LLM”的护栏 |
| 缺陷 5 | `summary_*.jinja2` + `pipeline.py:190-201` | 所有维度解读为空时，总结提示词无任何 grounding 数据，LLM 凭记忆编造；提示词也未要求“无数据时拒答”。且即便全部维度不可用，仍会请求总结 |
| 瑕疵 6 | `src/report/templates/report.jinja2` | Jinja 环境未开 `trim_blocks/lstrip_blocks`，控制块残留空行，rich 终端把表格头与分隔线折成一行显示 |

## 设计决策

| 决策点 | 选择 |
|---|---|
| 某维度无数据时的解读 | **跳过 LLM 调用**，报告显示已有的“XX数据暂时不可用”占位；不消耗 token、不虚构 |
| 网络健壮性 | **重试 + 退避 且 降低并发**：单请求最多 3 次指数退避重试，采集并发 `max_workers` 从 5 降到 3 |
| 表格折行瑕疵 | 一并纳入本次修复 |

## 详细设计

### 修复 1 — 中文数字单位解析

新增 `src/utils/numbers.py`：

```python
def parse_cn_number(value) -> float | None:
    """解析带中文单位的数值字符串。'3.54亿'→3.54e8，'-'/''/None→None。"""
```

- 单位映射：`万`→1e4、`亿`→1e8、`万亿`→1e12。
- 边界：`'-'`、`''`、`None`、`'--'` → `None`；纯数字/浮点直通；支持前导负号（`'-3.54亿'`）；去除千分位逗号与空白。
- 已经是 `int`/`float` 的输入原样返回。
- 在 `akshare.py:_fetch_financial` 中，用 `parse_cn_number` 替换 revenue/net_profit/assets/equity/cash_flow 的裸 `float(...)`；解析结果为 `None` 时按 `0.0` 兜底（保持 `FinancialData` 字段为非空 float 的约定）。
- `_fetch_valuation` 的 PE/PB 复用该函数，兼容 `'-'` 以外的带单位取值。

### 修复 2 — 网络健壮性

新增轻量重试装饰器 `src/utils/retry.py`：

```python
def retry_on_network_error(max_attempts=3, base_delay=0.5):
    """对 ConnectionError / RemoteDisconnected 等瞬时网络异常重试，指数退避。"""
```

- 仅对网络类异常重试：`ConnectionError`、`requests.exceptions.ConnectionError`、`http.client.RemoteDisconnected` 及其父类；其它异常立即抛出。
- 退避序列：0.5s → 1s → 2s；用尽后抛出最后一次异常，交由 `AkShareAdapter.fetch` 的外层 `except` 处理（返回 `[]`，行为不变）。
- 包裹 `_fetch_price`、`_fetch_industry`、`_fetch_valuation`、`_fetch_news` 内部的 AkShare 调用。
- `pipeline.py` 采集阶段 `ThreadPoolExecutor(max_workers=5)` 改为 `max_workers=3`。

### 修复 3 — 重写 8 个维度提示词模板

`financial_openai`/`financial_claude` 已正确，保留。重写其余 4 个维度 × 2 provider（共 8 个），使用各分析模块**真实产出的 metrics**：

| 维度 | 可用 metrics（来自对应 analyzer） | 提示词要点 |
|---|---|---|
| technical | ma5/10/20/60、latest_close、price_vs_ma20、avg_volume_5d、volume_ratio、macd_dif/dea/bar | 均线多空排列、价格与 MA20 关系、量比、MACD 金叉/死叉 |
| valuation | pe_ttm、pb、ps_ttm、pe_percentile | PE/PB/PS 绝对水平、历史分位、是否高估/低估 |
| industry | industry、sector、peers | 行业归属、同业公司、行业地位 |
| sentiment | headline_count、headlines、date | 新闻数量、标题倾向、舆情热度 |

- 所有 metrics 引用都用 `is defined` / `is not none` 或 `| default(...)` 守卫，避免任何单个缺失字段再次触发 `Undefined` 报错。
- 每个模板末尾追加**防幻觉约束**：“仅基于以上给出的数据进行分析，不得编造未提供的数字；若数据不足以支撑某项判断，请明确指出。”

### 修复 4 — 解读渲染的缺失数据护栏

在 `pipeline.py:_generate_commentary` 的维度循环中，渲染前判断：

```python
if result.status == "unavailable" or not result.metrics:
    commentary[result.dimension] = ""   # 跳过 LLM，报告模板已有占位
    continue
```

- 避免对空 metrics 调用 LLM（省 token、防报错）。
- 报告模板 `report.jinja2` 已有 `status == "unavailable"` → “XX数据暂时不可用”分支，无需改动。

### 修复 5 — 综合总结的防幻觉

- 在 `_generate_commentary` 生成 summary 前，判断是否**至少存在一个非空维度解读**；若全部为空则跳过 summary（`commentary["summary"] = ""`），报告综合总结区块留空。
- `summary_openai/claude.jinja2` 提示词补充：列出“已覆盖维度 / 缺失维度”，并加入约束“仅基于上方各维度解读撰写，不得引入未提供的数据；对缺失维度不做臆测”。

### 修复 6 — 报告表格渲染

- 在 `src/report/builder.py` 的 `Environment(...)` 增加 `trim_blocks=True, lstrip_blocks=True`，清理控制块残留空行，修正 rich 终端下表格头与分隔线折行的显示问题。

## 测试策略（TDD，先写失败测试）

| 测试 | 断言 |
|---|---|
| `parse_cn_number` 单元测试 | `'3.54亿'→3.54e8`、`'7217.13万'→7.21713e7`、`'-'/''/None→None`、负号、纯数字直通 |
| `_fetch_financial` 解析 | 用中文单位 mock DataFrame，断言行不再被跳过、`revenue` 等被正确换算 |
| 重试装饰器 | 前两次抛 `RemoteDisconnected`、第三次成功返回；纯业务异常不重试 |
| 模板冒烟测试 | 每个维度模板用其 analyzer 真实 metrics 渲染**不抛异常**（一次性锁死 `'revenue' is undefined` 回归） |
| `_generate_commentary` 护栏 | unavailable 维度不触发 LLM 调用；全部 unavailable 时不生成 summary |

## 影响范围

### 修改 / 新增文件

| 文件 | 改动 |
|---|---|
| `src/utils/numbers.py` | 新增：`parse_cn_number` |
| `src/utils/retry.py` | 新增：`retry_on_network_error` 装饰器 |
| `src/data/akshare.py` | 财务用 `parse_cn_number`；网络调用加重试装饰器 |
| `src/core/pipeline.py` | 采集并发降到 3；`_generate_commentary` 加不可用维度护栏与 summary 跳过逻辑 |
| `src/llm/prompt_templates/{technical,valuation,industry,sentiment}_{openai,claude}.jinja2` | 重写为对应维度真实内容 + 防幻觉约束（8 个） |
| `src/llm/prompt_templates/summary_{openai,claude}.jinja2` | 补充覆盖/缺失维度清单与防幻觉约束 |
| `src/report/builder.py` | Jinja 环境开 `trim_blocks/lstrip_blocks` |
| `tests/utils/`、`tests/data/`、`tests/core/`、`tests/llm/` | 新增上述测试 |

### 不修改

- 分析模块（financial/technical/valuation/industry/sentiment analyzer）逻辑不变——它们产出的 metrics 结构本身正确，问题在模板侧。
- 缓存层、配置管理、CLI 命令结构不变。
- LLM 适配器（openai/claude）调用逻辑不变。

## 范围外

- 更换或新增财务数据源（如东方财富财报接口）——本次仅修复现有 THS 接口的解析。
- PE/PB 历史分位数据源（当前 `stock_zh_a_spot_em` 只给单点，`pe_percentile` 恒不触发）——需要历史估值序列，属独立增强，本次不做。
- 港股/美股支持。
