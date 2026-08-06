# Stock Robot

AI 驱动的股票/指数分析研报助手。输入 A 股代码或指数代码，自动采集财务、行情、估值、行业、舆情、资金流向、宏观数据，调用 LLM 生成多维度分析报告。支持宽基指数、行业指数、海外指数的独立分析与横向对比，并可将大盘环境嵌入个股分析报告。

## 免责声明

本工具仅用于个人学习和研究目的。所有分析数据和观点不构成任何投资建议。股票投资有风险，入市需谨慎。数据来源的准确性和时效性无法保证，使用者需自行判断。

## 安装

```bash
# 克隆项目后进入目录
cd stock_robot

# 安装（开发模式）
pip install -e ".[dev]"
```

## 首次使用

首次运行时会显示免责声明，确认后即可使用：

```bash
stock-robot config set data.disclaimer_accepted true
```

## 配置 LLM

### 使用 OpenAI

```bash
# 设置 API Key
stock-robot config set llm.api_key "sk-your-openai-api-key"

# 设置模型（可选，默认 gpt-4o）
stock-robot config set llm.model "gpt-4o-mini"
```

### 使用 Claude

```bash
# 切换 provider
stock-robot config set llm.provider "claude"

# 设置 API Key
stock-robot config set llm.api_key "sk-ant-your-claude-api-key"

# 选择模型
stock-robot config set llm.model "claude-sonnet-4-6"
```

也可以直接编辑配置文件 `~/.stock_robot/config.yaml`。

### 不调用 LLM

如果只想看数据指标、不生成解读文字：

```bash
# 单次跳过 LLM
stock-robot analyze 000001 --no-llm

# 全局关闭
stock-robot config set llm.enabled false
```

## 命令详解

### analyze — 分析股票

```bash
stock-robot analyze <股票代码>
```

| 选项 | 说明 |
|------|------|
| `-d, --dimension` | 指定分析维度：`financial` / `technical` / `valuation` / `industry` / `sentiment` |
| `--with-market` | 注入大盘环境数据（沪深300 技术面/估值/资金面快照） |
| `--refresh-cache` | 强制刷新数据，忽略本地缓存 |
| `--no-llm` | 仅输出数据和指标，不调用 LLM |
| `-v, --verbose` | 显示数据采集和计算的过程日志 |

**示例：**

```bash
# 完整研报
stock-robot analyze 000001

# 只看财务分析
stock-robot analyze 000001 --dimension financial

# 只看估值，强制刷新数据
stock-robot analyze 000001 --dimension valuation --refresh-cache

# 详细模式
stock-robot analyze 600036 -v
```

股票代码支持简写（`1` → `000001`）、带前缀（`sh000001`、`sz000001`），上交所（6 开头）、深交所主板（0 开头）、创业板（3 开头）、北交所（8 开头）均可识别。

### index — 分析指数

```bash
stock-robot index <指数代码> [指数代码...]
```

| 选项 | 说明 |
|------|------|
| `--style, -s` | 手动指定指数类别：`broad` / `sector` / `overseas`（已收录的指数自动识别） |
| `--output, -o` | 输出格式：`terminal`（默认）/ `markdown` |
| `--compare-only` | 仅输出多指数横向对比表格，不生成独立报告 |

**示例：**

```bash
# 分析单个宽基指数
stock-robot index 000300

# 分析行业指数
stock-robot index 801080

# 批量分析并横向对比
stock-robot index 000300 000905 000016

# 仅看对比表格
stock-robot index 000300 000905 --compare-only

# 分析海外指数
stock-robot index HSI SPX NDX

# 手动指定类别（未收录的指数代码）
stock-robot index 932000 --style sector
```

指数代码支持 A 股指数（6 位数字）、港股/美股指数（字母代码如 `HSI`、`SPX`）。

**三类指数的分析侧重点：**

| 类别 | 分析重点 | 包含维度 |
|------|---------|---------|
| 宽基 (broad) | 宏观+估值 | 技术面、估值、资金流向、宏观、舆情 |
| 行业 (sector) | 资金+技术 | 技术面、估值、资金流向、舆情（无宏观） |
| 海外 (overseas) | 趋势+汇率 | 技术面、估值、宏观（无资金流向） |

### config — 管理配置

```bash
# 查看配置项
stock-robot config get llm.provider         # → openai
stock-robot config get llm.model            # → gpt-4o

# 设置配置项
stock-robot config set llm.provider claude
stock-robot config set llm.temperature 0.1
stock-robot config set data.cache_ttl.news 3600

# 查看完整配置
cat ~/.stock_robot/config.yaml
```

**常用配置项：**

| 键 | 说明 | 默认值 |
|----|------|--------|
| `llm.provider` | LLM 提供商 | `openai` |
| `llm.model` | 模型名称 | `gpt-4o` |
| `llm.api_key` | API 密钥 | 空 |
| `llm.temperature` | 生成温度 (0-1) | `0.3` |
| `llm.max_tokens` | 最大输出 token | `2000` |
| `llm.enabled` | 是否启用 LLM | `true` |
| `data.cache_ttl.daily` | 日频数据缓存（秒） | `86400` |
| `data.cache_ttl.quarterly` | 季频数据缓存（秒） | `604800` |
| `data.cache_ttl.news` | 新闻缓存（秒） | `21600` |

### cache — 管理缓存

```bash
# 查看缓存状态
stock-robot cache status

# 清空所有缓存
stock-robot cache clear
```

缓存存储在 `~/.stock_robot/cache.db`（SQLite），按数据类型 + 股票代码 + 时间维度组织，TTL 到期自动失效。

## 报告输出

报告在终端中以 Markdown 格式渲染显示，同时保存到 `reports/` 目录：

```
reports/
├── 000001_20260705_143021.md
├── 600036_20260705_150532.md
└── ...
```

文件名格式：`{股票代码}_{日期}_{时间}.md`（指数报告同理，使用指数代码）

## 报告的五个维度

| 维度 | 数据来源 | 关键指标 |
|------|---------|---------|
| 财务分析 | 同花顺财报摘要 | 营收增长率、净利润增长率、ROE 趋势 |
| 技术面分析 | 日线 K 线数据 | MA5/10/20/60、MACD、量比、价格与均线关系 |
| 估值分析 | 实时行情 | PE(TTM)、PB、PS(TTM)、历史分位数 |
| 行业分析 | 板块分类 | 行业分类、同行业公司列表 |
| 舆情分析 | 个股新闻 | 近期新闻标题列表、数量统计 |

## 指数分析的五个维度

指数分析复用 AnalysisModule 接口，使用独立的分析器实现：

| 维度 | 数据来源 | 关键指标 |
|------|---------|---------|
| 技术面 | 指数日线 | MA5/20/60、年内高低点、趋势标签（牛/熊/震荡） |
| 估值 | 指数估值数据 | PE/PB 历史分位数、股息率、估值标签（低估/中性/高估/无效） |
| 资金流向 | 北向资金/主力资金/融资余额 | 净流入/流出量、资金面标签（积极/中性/消极） |
| 宏观 | PMI/Shibor/CPI/汇率 | PMI 扩张/收缩信号、利率水平、汇率波动 |
| 舆情 | 市场新闻 | 关键词匹配的市场情绪标签 |

各维度独立输出定性标签（不做加权评分），最终由报告构建器综合生成定性解读和仓位系数建议。

## 指数比对

多指数同时分析时，自动生成横向对比表格，包含：指数名称、最新价、涨跌幅、PE/PB 分位数、趋势标签、估值标签、资金面标签、综合判断。

配置驱动的标签阈值位于 `src/analysis/config/index/base/`，按指数类别（broad/sector/overseas）分别定义。可通过修改 YAML 文件调整牛熊判断条件、估值分位阈值、宏观信号等参数。

## LLM 成本

每次配置的 LLM 调用会记录到 `~/.stock_robot/usage.log`，包含模型、token 消耗和费用估算。

## 常见问题

**Q: 分析失败，提示 API 配置错误？**
确认 API Key 已正确设置：
```bash
stock-robot config get llm.api_key
```
如果 Key 为空，执行 `stock-robot config set llm.api_key "你的key"`

**Q: 想不花钱试用？**
```bash
stock-robot analyze 000001 --no-llm
```
这样只会计算和展示数据指标，不调用 LLM。

**Q: 数据很久没更新？**
```bash
stock-robot analyze 000001 --refresh-cache
```
强制从数据源重新获取，跳过缓存。

**Q: 支持港股/美股吗？**
个股分析 v1 仅支持 A 股。指数分析已支持港股和美股主要指数（HSI、HSTECH、SPX、NDX、DJI），通过 `stock-robot index` 命令使用。

**Q: 如何添加其他 LLM（如 DeepSeek、通义千问）？**
在 `src/llm/` 下创建新的适配器（实现 `LLMBackend` 接口），然后在 CLI 的 `_register_llm` 函数中注册即可。
