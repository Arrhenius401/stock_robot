# 行业自适应打分体系设计文档

> 状态: 设计完成 / 日期: 2026-07-31 / 关联: 修复当前打分逻辑行业一刀切问题

## 概述

当前报告基础数字取自真实行情/财报，但打分逻辑和财务评判标准对所有行业采用统一的硬编码阈值——银行和半导体的 ROE 用同一套标准、周期股和消费股的 PE 分位逻辑完全相同。这导致大量判定结论错误，报告整体合理性差，不能作为投资参考。

本设计引入**行业自适应打分体系**：以 8 大投资风格大类为通用基座 + 申万一级行业个性化覆写，彻底消除一刀切。

## 架构变更

```
                        ┌─────────────────────────┐
                        │   industry_mapping.csv   │  本地离线映射表
                        │   symbol → 申万一级行业   │  每月增量更新
                        └───────────┬─────────────┘
                                    │
                        ┌───────────▼─────────────┐
                        │   IndustryClassifier     │  新增组件
                        │   查表 → 行业 → 大类      │
                        └───────────┬─────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────┐     ┌──────────────────────┐     ┌──────────────────┐
│ 8大类 YAML    │     │ 申万一级覆写 YAML     │     │ 策略子类 (Python) │
│ base/*.yaml   │     │ override/*.yaml       │     │ 周期PE反转逻辑    │
│               │     │                      │     │ 银行PB估值逻辑    │
└───────┬───────┘     └──────────┬───────────┘     └────────┬─────────┘
        │                       │                          │
        └───────────────────────┼──────────────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │   ConfigLoader        │  新增组件
                    │   深度合并 + 权重校验   │
                    │   → 生成最终配置        │
                    └───────────┬───────────┘
                                │
        ┌───────────┬───────────┼───────────┬───────────┐
        ▼           ▼           ▼           ▼           ▼
  财务计分器   技术计分器   估值计分器   行业计分器   舆情计分器
  (可插拔)    (通用)      (可插拔)    (可插拔)    (通用)
```

管道集成后的完整流程：

```
Pipeline.run(symbol, name)
  ├─> collect()
  │     ├─> IndustryClassifier.lookup(symbol)
  │     │     └─> ctx.sw_industry, ctx.style_category
  │     └─> [并行采集 5 类数据，填充 ctx]
  ├─> enricher.enrich(ctx)
  ├─> ConfigLoader.load(ctx.sw_industry)
  │     └─> merged_config
  ├─> [并行分析 5 维度]
  │     └─> 各分析器接收 config，生产 AnalysisResult
  └─> _generate_commentary()
        └─> prompt 注入行业定位信息
```

## 一、行业分类体系

### 8 大投资风格大类 → 申万一级映射

| 大类 | 申万一级行业 |
|------|-------------|
| 大金融 | 银行、非银金融、房地产 |
| 周期资源 | 煤炭、有色金属、钢铁、石油石化、基础化工、建筑材料 |
| 必选消费 | 食品饮料、农林牧渔、纺织服饰、美容护理 |
| 可选消费 | 家用电器、汽车、轻工制造、商贸零售、社会服务 |
| 高端制造 | 机械设备、电力设备、国防军工、建筑装饰 |
| TMT科技 | 电子、计算机、通信、传媒 |
| 医药 | 医药生物 |
| 公用事业 | 公用事业、交通运输、环保 |

### 需要单独覆写的赛道（7 组 8 行业）

| 覆写配置 | 覆盖行业 | 所属大类 | 覆写理由 |
|---------|---------|---------|---------|
| 银行 | 银行 | 大金融 | ROE/负债率/毛利率全不适用，PB为核心 |
| 非银金融 | 非银金融 | 大金融 | 券商看PB+ROE，保险看PEV，与银行又不同 |
| 房地产 | 房地产 | 大金融 | 有息负债口径，剔除预收款后负债率 |
| 周期资源 | 煤炭、有色金属、钢铁 | 周期资源 | PE反转（高PE=低估），毛利率波动豁免 |
| 电子 | 电子 | TMT科技 | 半导体高PE容忍，研发率权重取代ROE |
| 计算机 | 计算机 | TMT科技 | 营收增速权重最高，轻资产加分 |
| 医药生物 | 医药生物 | 医药 | 研发管线估值，创新药vs仿制药不同逻辑 |

未覆写的 23 个申万一级行业直接继承所属大类 base 模板，使用 GeneralScorer 纯 YAML 驱动。

### 映射表存储

`data/industry_mapping.csv`，覆盖全部 A 股约 5000 只标的：

```csv
symbol,sw_level1,sw_level2,style_category
000001,银行,股份制银行,大金融
000002,房地产,房地产开发,大金融
600519,食品饮料,白酒,必选消费
```

- 每月用 AkShare 增量更新一次，运行时不依赖在线接口
- IndustryClassifier 启动时一次性加载到内存 dict，查询 O(1)

## 二、配置体系

### 文件布局

```
src/analysis/config/
├── global_const.yaml
├── 申万_大类_映射.yaml
├── base/
│   ├── 大金融.yaml
│   ├── 周期资源.yaml
│   ├── 必选消费.yaml
│   ├── 可选消费.yaml
│   ├── 高端制造.yaml
│   ├── TMT科技.yaml
│   ├── 医药.yaml
│   └── 公用事业.yaml
└── override/
    ├── 银行.yaml
    ├── 非银金融.yaml
    ├── 房地产.yaml
    ├── 周期资源.yaml          ← 煤炭+有色+钢铁共用
    ├── 电子.yaml
    ├── 计算机.yaml
    └── 医药生物.yaml
```

### 配置合并规则

- **字典**：覆写 key 递归覆盖父类 value
- **列表**：不合并，覆写文件需显式提供完整新列表
- **标量（数字、布尔、字符串）**：覆写优先级 > base
- **开关类字段**（enabled, use_custom_calc）：优先级最高

### 元信息规范

```yaml
meta:
  style_category: "大金融"        # 归属 8 大顶层大类
  sw_primary: ["银行"]            # 适配哪些申万一级行业
  inherit: ""                     # base 模板为空，override 填写父大类名称
  strategy_key: "BankScorer"      # Python 策略类标识，空则默认 GeneralScorer
  cache_key: ""                   # 程序自动生成，无需手动填写
  version: "1.0"
```

### 指标字段统一范式

```yaml
指标名:
  enabled: bool
  max_score: 分数上限
  threshold_mode: tier | range
  tiers:
    - { min: 阈值, max: 阈值, score: 得分 }
  extra_config: {}   # 负分归零、区间上下限等附加参数
  use_custom_calc: false   # true=策略子类接管计算
```

- `enabled: false` — 该指标完全不参与计分
- `use_custom_calc: true` — 指标保留入口，计算由策略子类实现

### 全局常量

```yaml
# global_const.yaml
scoring:
  max_total: 10
  default_score: 0
  negative_infinity: -999
  min_weight: 0.0
  max_weight: 1.0

sufficiency:
  peer_sufficient: 8
  peer_partial: 3
  price_min_bars: 60
  financial_min_quarters: 4
  valuation_min_points: 120
  sentiment_min_items: 10

risk:
  max_flag_penalty: -3
  hard_floor: 0
  hard_ceiling: 10
  
risk_penalty:
  enabled: true
  rules:
    financial_risk:
      max_deduction: -3
      flags: ["roe_low", "high_debt", "cash_flow_mismatch"]
    valuation_risk:
      max_deduction: -2
      flags: ["high_pe_premium", "pb_below_peer"]
    # 扩展预留:
    # goodwill_impairment:
    #   max_deduction: -2
    # pledge_risk:
    #   max_deduction: -2
```

### 八大类权重分配

| 大类 | 财务 | 估值 | 行业 | 技术 | 舆情 |
|------|------|------|------|------|------|
| 大金融 | 0.25 | 0.30 | 0.15 | 0.20 | 0.10 |
| 周期资源 | 0.20 | 0.30 | 0.20 | 0.20 | 0.10 |
| 必选消费 | 0.35 | 0.25 | 0.10 | 0.20 | 0.10 |
| 可选消费 | 0.30 | 0.25 | 0.15 | 0.20 | 0.10 |
| 高端制造 | 0.30 | 0.25 | 0.15 | 0.20 | 0.10 |
| TMT科技 | 0.25 | 0.25 | 0.20 | 0.20 | 0.10 |
| 医药 | 0.25 | 0.30 | 0.15 | 0.20 | 0.10 |
| 公用事业 | 0.30 | 0.25 | 0.15 | 0.20 | 0.10 |

所有 base 模板权重总和 = 1.0，ConfigLoader 加载后自动校验。

### 覆写示例：银行

继承自 `大金融`，仅覆写差异项：

```yaml
meta:
  style_category: "大金融"
  sw_primary: ["银行"]
  inherit: "大金融"
  strategy_key: "BankScorer"
  version: "1.0"

financial:
  weight: 0.20

  roe:
    max_score: 3
    tiers:
      - { min: 0.13, score: 3 }
      - { min: 0.10, score: 2 }
      - { min: 0.07, score: 1 }
      - { min: 0.04, score: 0.5 }
      - { min: -999, score: 0 }

  debt_ratio:
    use_custom_calc: true    # BankScorer 接管：不良率/拨备覆盖率/资本充足率

  gross_margin_stability:
    enabled: false

valuation:
  weight: 0.35
  primary_metric: pb

  pe_percentile:
    enabled: false

  pb_percentile:
    max_score: 6
    tiers:
      - { max_pct: 20, score: 6 }
      - { max_pct: 40, score: 4 }
      - { max_pct: 70, score: 2 }
      - { score: 0 }

industry:
  comparison_metrics: ["roe", "pb", "non_performing_loan_ratio", "provision_coverage"]
```

### 覆写示例：周期资源（煤炭+有色+钢铁共用）

```yaml
meta:
  style_category: "周期资源"
  sw_primary: ["煤炭", "有色金属", "钢铁"]
  inherit: "周期资源"
  strategy_key: "CyclicalScorer"
  version: "1.0"

financial:
  gross_margin_stability:
    enabled: false

valuation:
  primary_metric: pe
  pe_percentile:
    percentile_reverse: true
    tiers:
      - { max_pct: 70, score: 4 }
      - { max_pct: 50, score: 2.5 }
      - { score: 0 }

industry:
  weight: 0.20
  comparison_metrics: ["roe", "gross_margin", "debt_ratio"]
```

## 三、行业适配对照表

| 指标 | 通用默认规则 | 银行 | 周期资源 | 计算机科技 | 房地产 |
|------|------------|------|---------|-----------|--------|
| ROE | ≥15%满分，<5% 0分 | 7%-13%满分 | 跟随周期浮动 | 容忍低位 | 同通用 |
| 资产负债率 | 40%-70%最优 | 自定义：不良率+拨备覆盖率+资本充足率 | 适度放宽上限 | 越低越好 | 有息负债口径 |
| 毛利率波动 | 波动<5pp满分 | 关闭 | 关闭 | 小幅波动加分 | 同通用 |
| PE 历史分位 | <30%=低估 | 关闭，改用 PB 为主 | 反转：高PE=低估 | 高PE 容忍 | 同通用 |
| 行业对比 | 仅毛利率 | 净息差+不良率+拨备覆盖率 | 吨盈利+产能周期 | 研发率+营收增速 | 预售款+有息负债 |

## 四、Python 策略类

### 类层次

```
BaseScorer (抽象基类)
  ├─> GeneralScorer        ← 默认兜底，纯 YAML 驱动
  ├─> BankScorer           ← 银行：PB 为主、不良率、拨备覆盖率
  ├─> CyclicalScorer       ← 周期资源：PE 反转、毛利率波动豁免
  ├─> TechGrowthScorer     ← 科技：研发率、营收增速替代 ROE
  ├─> RealEstateScorer     ← 房地产：有息负债口径
  ├─> NonBankFinancialScorer ← 非银金融：券商/保险差异
  └─> PharmaScorer         ← 医药：研发管线逻辑
```

### 基类接口

```python
class BaseScorer(ABC):
    def __init__(self, config: dict, global_const: dict): ...

    @property
    @abstractmethod
    def dimension(self) -> str: ...

    @abstractmethod
    def score(self, context: AnalysisContext) -> tuple[float, str, list[str]]:
        """返回 (得分, 得分详情, 风险标签)"""
        ...

    def _tier_score(self, value, tiers, reverse=False) -> tuple[float, str]: ...
    def _range_score(self, value, tiers) -> tuple[float, str]: ...
```

### ConfigLoader

```python
class ConfigLoader:
    def __init__(self, config_dir: Path): ...

    def load(self, sw_industry: str) -> dict:
        # 1. 申万行业 → 大类映射
        # 2. 加载 base 模板
        # 3. 存在 override → 深度合并（字典递归、列表替换、标量覆写）
        # 4. 权重校验（五维度总和 = 1.0）
        # 5. strategy_key 为空 → 默认绑定 GeneralScorer
        # 6. 写入缓存，返回

    def _deep_merge(self, base: dict, override: dict) -> dict: ...
    def _validate_weights(self, config: dict): ...
```

- 同行业重复调用命中内存缓存
- override 缺失或字段不存在 → 完全降级使用 base 模板
- 权重校验失败 → 抛出 ConfigError，程序不继续

## 五、分析层改动

### 现有 Analyzer 改造方式

| 模块 | 改动 | 说明 |
|------|------|------|
| `financial.py` | 重写 | 删除硬编码阈值，改为遍历 YAML 指标列表调用 GeneralScorer |
| `valuation.py` | 重写 | 同上 |
| `industry.py` | 重写 | 同上 |
| `technical.py` | 微调 | 参数从 YAML 读取，均线/MACD/量价计算逻辑不变 |
| `sentiment.py` | 微调 | 参数从 YAML 读取，舆情统计逻辑不变 |

### 分析接口微调

```python
class AnalysisModule(ABC):
    @abstractmethod
    def analyze(self, context: AnalysisContext,
                config: dict | None = None) -> AnalysisResult:
        """config 由管道注入；为 None 时降级为硬编码兜底"""
        ...
```

### GeneralScorer 核心逻辑

不包含任何硬编码阈值。遍历 config 中每个 enabled 指标，调用 `_tier_score` / `_range_score` 计算得分。20+ 个普通行业无需任何 Python 代码。

## 六、Schema 变更

```python
class AnalysisContext(BaseModel):
    # 现有字段不变 ...
    
    # 新增
    sw_industry: str = ""       # 申万一级行业名
    style_category: str = ""    # 八大类归属

class AnalysisResult(BaseModel):
    # 现有字段不变 ...
    
    # 新增
    industry_note: str = ""     # 行业特定说明，如"银行采用 PB 为主估值体系"
```

## 七、LLM Prompt 适配

在现有 `batch_analysis_*.jinja2` 模板开头新增行业定位段：

```jinja2
## 标的行业定位

申万一级行业：{{ sw_industry }}
投资风格大类：{{ style_category }}
{% if industry_note %}
估值方法论说明：{{ industry_note }}
{% endif %}
```

示例注入效果（银行）：
> 申万一级行业：银行 / 投资风格大类：大金融
> 估值方法论说明：银行业采用 PB 估值为主（权重 35%），PE 不适用。财务维度重点关注不良贷款率、拨备覆盖率、资本充足率，不采用通用毛利率和资产负债率判定。

## 八、文件清单

### 新增文件

```
src/analysis/config/global_const.yaml
src/analysis/config/申万_大类_映射.yaml
src/analysis/config/base/大金融.yaml
src/analysis/config/base/周期资源.yaml
src/analysis/config/base/必选消费.yaml
src/analysis/config/base/可选消费.yaml
src/analysis/config/base/高端制造.yaml
src/analysis/config/base/TMT科技.yaml
src/analysis/config/base/医药.yaml
src/analysis/config/base/公用事业.yaml
src/analysis/config/override/银行.yaml
src/analysis/config/override/非银金融.yaml
src/analysis/config/override/房地产.yaml
src/analysis/config/override/周期资源.yaml
src/analysis/config/override/电子.yaml
src/analysis/config/override/计算机.yaml
src/analysis/config/override/医药生物.yaml
src/analysis/config_loader.py
src/analysis/scorers/__init__.py
src/analysis/scorers/base.py
src/analysis/scorers/general.py
src/analysis/scorers/bank.py
src/analysis/scorers/cyclical.py
src/analysis/scorers/tech_growth.py
src/analysis/scorers/real_estate.py
src/analysis/scorers/non_bank_financial.py
src/analysis/scorers/pharma.py
src/data/industry_classifier.py
data/industry_mapping.csv
```

### 修改文件

```
src/analysis/financial.py      → 重写为配置驱动
src/analysis/valuation.py      → 重写为配置驱动
src/analysis/industry.py       → 重写为配置驱动
src/analysis/technical.py      → 参数外提 YAML
src/analysis/sentiment.py      → 参数外提 YAML
src/analysis/base.py           → analyze() 签名新增 config 参数
src/data/schemas.py            → AnalysisContext 新增行业字段
src/core/pipeline.py           → 集成 IndustryClassifier + ConfigLoader
src/llm/prompt_templates/      → 新增行业定位段
```

## 九、实现顺序

```
Phase 1: 基础设施
  - industry_mapping.csv 生成
  - IndustryClassifier
  - global_const.yaml + 申万_大类_映射.yaml
  - ConfigLoader（含 deep_merge + 权重校验 + 缓存）

Phase 2: 配置编写
  - 8 份 base 模板
  - 7 份 override 配置
  - 权重校验通过

Phase 3: 策略类
  - BaseScorer 抽象基类
  - GeneralScorer（纯 YAML 驱动）
  - 6 个行业策略子类

Phase 4: 分析层改造
  - financial/valuation/industry 重写为配置驱动
  - technical/sentiment 参数外提
  - AnalysisModule.analyze() 签名更新

Phase 5: Schema + 管道集成
  - AnalysisContext 新增字段
  - Pipeline.collect() 集成 IndustryClassifier
  - Pipeline.run() 集成 ConfigLoader

Phase 6: LLM + 报告
  - Prompt 模板新增行业定位段
  - industry_note 注入

Phase 7: 测试
  - ConfigLoader 单元测试（加载/合并/权重校验/缓存/降级兜底）
  - 策略类单元测试（各行业边界 case）
  - GeneralScorer 纯净 YAML 驱动验证
  - 管道集成测试（端到端行业分类→配置加载→打分）
```

## 十、风险与兜底

- **映射表覆盖不全**：新股/改名标的查不到 → 降级为"高端制造"大类 + GeneralScorer
- **override 文件缺失**：完全降级使用 base 模板，程序不崩溃
- **权重校验失败**：抛出 ConfigError，拒绝以错误配置运行
- **AkShare 分类接口下线**：映射表是本地 CSV，完全不受影响
- **新增申万一级行业**：若属于已有大类，自动继承 base 模板，无需改代码
