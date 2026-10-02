# 网络健壮性增强设计

## 目标

提升 `stock-robot analyze` 在真实网络环境下的数据采集成功率。当前即使已部署重试退避（3 次、0.5s 指数退避）和降并发（5→3），部分 AkShare 端点仍持续失败。根因不在重试次数不够，而在**某些端点本身负载过重**（如全市场扫描接口），需替换为轻量端点。

## 现状

上一轮修复（2026-07-08 管道修复）已加入的重试覆盖：

| 端点 | 重试包裹 | 实际成功率 |
|---|---|---|
| `stock_zh_a_hist`（单股 K 线） | ✅ `_ak_hist` | 低（RemoteDisconnected 频发） |
| `stock_zh_a_spot_em`（全市场行情） | ✅ `_ak_spot_em` | 极低（全市场扫描，请求最重） |
| `stock_board_industry_name_em`（板块列表） | ✅ `_ak_industry_name` | 低 |
| `stock_news_em`（个股新闻） | ✅ `_ak_news` | 可接受 |
| `stock_financial_abstract_ths`（财务摘要） | ❌ 无包裹 | 可接受（THS 接口较稳） |
| `stock_info_a_code_name`（名称解析） | ❌ 无包裹 | 低 |

新暴露的问题：
- `resolve_name`（`symbols.py`）调用 `stock_info_a_code_name()` 无重试，常是第一个失败
- 全市场接口 `stock_zh_a_spot_em()` 请求体量过大（4000+ 股票），重试也救不回来
- 行业接口 `stock_board_industry_name_em()` 遍历全部板块后取第一条，不仅重，而且是 bug——取的并非该股票所属行业
- 并发请求间无间隔，3 个请求同时发出仍触发上游限流

## 设计决策

| 决策点 | 选择 |
|---|---|
| 失败策略 | 尽力而为，诚实降级（已确认） |
| 重端点处理 | 调研并替换为单只股票轻量接口，旧端点保留作 fallback（已确认） |
| resolve_name | 补上重试包裹（已确认） |
| 并发减压 | 请求间加 stagger 延迟（已确认） |

## 详细设计

### 修复 1 — `resolve_name` 缺重试

在 `src/utils/symbols.py` 的 `resolve_name` 中，对 `ak.stock_info_a_code_name()` 调用包裹 `retry_on_network_error`：

```python
from utils.retry import retry_on_network_error

@retry_on_network_error()
def _ak_code_name():
    return ak.stock_info_a_code_name()

def resolve_name(symbol: str) -> str:
    try:
        df = _ak_code_name()
        ...
```

### 修复 2 — 估值端点轻量化

**当前：** `_fetch_valuation` → `_ak_spot_em()` → `ak.stock_zh_a_spot_em()`（全市场 4000+ 只股票）

**替换方案：** 调研并使用单只股票实时行情接口，候选函数（实施时验证字段可用性）：
- `ak.stock_individual_info_em(symbol="000001")` — 单只个股信息

**回退链：**
```
_ak_individual_spot(symbol)  ← 新轻量端点（含重试）
    ↓ 失败
_ak_spot_em()                ← 旧全市场端点（含重试，fallback）
    ↓ 失败
pe_ttm=None, pb=None         ← 诚实降级
```

实现方式：在 `akshare.py` 新增 `_ak_individual_spot` helper，`_fetch_valuation` 先调新端点，`except` 中 fallback 旧端点。

### 修复 3 — 行业端点轻量化

**当前：** `_fetch_industry` → `_ak_industry_name()` → `ak.stock_board_industry_name_em()`（遍历全部板块，取第一条——且不是该股票的行业）

**替换方案：** 调研并使用按股票查行业的接口，候选函数（实施时验证字段可用性）：
- `ak.stock_individual_info_em(symbol)` — 单只个股信息中通常含 "行业" 字段

**回退链：**
```
_ak_individual_info(symbol)  ← 新轻量端点（含重试）
    ↓ 失败
_ak_industry_name()          ← 旧全板块接口（含重试，fallback）
    ↓ 失败
industry="未知"              ← 诚实降级
```

### 修复 4 — 并发请求 stagger

在 `src/core/pipeline.py` 的 `collect` 方法中，`fetch_one` 内部发起实际请求前插入 `time.sleep(0.3)`，让 3 个并发请求错峰发出，减轻对上游的瞬时冲击。

## 测试策略

| 测试 | 内容 |
|---|---|
| `resolve_name` 重试 | mock `stock_info_a_code_name` 前两次抛 `RemoteDisconnected`，第三次成功，断言返回正确名称 |
| 估值 fallback 链 | 新端点失败 → 回退旧端点成功 → 返回数据；新端点成功 → 不调旧端点 |
| 行业 fallback 链 | 同上 |
| `_fetch_valuation` 新端点 | mock 新端点返回结构，验证 PE/PB 字段提取正确 |
| `_fetch_industry` 新端点 | mock 新端点返回含行业字段的结构，验证 `industry` 提取正确 |

## 影响范围

### 修改文件

| 文件 | 改动 |
|---|---|
| `src/utils/symbols.py` | `resolve_name` 加 `retry_on_network_error` 包裹 |
| `src/data/akshare.py` | 新增 `_ak_individual_info` helper；`_fetch_valuation` 新端点优先 + fallback；`_fetch_industry` 新端点优先 + fallback |
| `src/core/pipeline.py` | `collect` 的 `fetch_one` 内部加 0.3s stagger |
| `tests/utils/test_symbols.py` | `resolve_name` 重试测试 |
| `tests/data/test_akshare.py` | 估值/行业 fallback 链测试 |

### 不修改

- 分析模块、LLM 层、报告构建 — 不感知数据源变更
- 配置管理、缓存层
- `_fetch_financial`、`_fetch_price`、`_fetch_news` — 已稳定或不在本次范围

## 范围外

- 更换财务数据源（THS 当前可用）
- 引入 tushare 或其他数据源作为第二备选
- 价格端点 `stock_zh_a_hist` 的替换（当前已有重试，且单股 K 线接口不算重）
