# Stock Robot

AI 驱动的股票分析研报助手。输入 A 股代码，自动采集财务、行情、估值、行业、舆情数据，调用 LLM 生成多维度分析报告。

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

文件名格式：`{股票代码}_{日期}_{时间}.md`

## 报告的五个维度

| 维度 | 数据来源 | 关键指标 |
|------|---------|---------|
| 财务分析 | 同花顺财报摘要 | 营收增长率、净利润增长率、ROE 趋势 |
| 技术面分析 | 日线 K 线数据 | MA5/10/20/60、MACD、量比、价格与均线关系 |
| 估值分析 | 实时行情 | PE(TTM)、PB、PS(TTM)、历史分位数 |
| 行业分析 | 板块分类 | 行业分类、同行业公司列表 |
| 舆情分析 | 个股新闻 | 近期新闻标题列表、数量统计 |

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
v1 仅支持 A 股。港股和美股在架构上已预留扩展接口，后续版本会支持。

**Q: 如何添加其他 LLM（如 DeepSeek、通义千问）？**
在 `src/llm/` 下创建新的适配器（实现 `LLMBackend` 接口），然后在 CLI 的 `_register_llm` 函数中注册即可。
