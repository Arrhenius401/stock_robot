# 港股个股分析实施与验收记录

日期：2026-10-16；压缩整理：2026-10-19。依据：[港股个股分析设计](../../specs/2026-10/2026-10-16-hong-kong-stock-analysis-design.md)。状态：首期港股个股分析、真实数据修复、公共分析机制对齐均已在独立工作树实现；本地浏览器、CLI 与云端三只港股验收通过；未合并、未部署、未推送、未发布。

本文合并自 2026-10-16 至 2026-10-19 的实施计划、真实数据修复记录、连接调查与公共分析对齐实施记录；原始探针响应、截图和云端验收 summary 保留在忽略目录与 git 历史。

## 1. 实施历史与提交

工作树 `tmp/worktrees/hk-live-data-fix`，分支 `codex/hk-live-data-fix`，基于 dev/b0eec63。

- `c19dd7b fix(港股): 修复真实财报解析与无评分研报链路`：港股财报改为东财直连解析，建立无评分报告链路；首轮本地/云端真实验收。
- `d18cebf feat(港股): 对齐多源回退、技术指标与覆盖率机制`：36 个文件，+1124/−187；共享选源、港股行情/估值备用、财报缓存判据、技术指标、覆盖率、报告模板与静态 JS。
- `405c4e8 fix(报告): 技术指标按显示精度格式化`：4 个文件，+33/−3；CLI/Markdown 与浏览器统一 2/3 位小数。

## 2. 数据层实现

- **财报**：直接访问 `https://datacenter.eastmoney.com/securities/api/data/v1/get`，使用 `RPT_HKF10_FN_INCOME_PC`、`RPT_HKF10_FN_BALANCE_PC`、`RPT_HKF10_FN_CASHFLOW_PC`；仅过滤 `SECUCODE="00700.HK"`，不加 REPORT_DATE；按 REPORT_DATE、STD_ITEM_CODE 排序；10 秒、pageSize=10000；校验 success、count/pages、代码与记录结构。长表按报告日期聚合、按科目映射并去重，坏行隔离；资产/归母权益、总权益、现金流与 EPS 分别核验。云端实测 2241 行聚合为 96 期，最新 2026-06-30。
- **港股行情**：腾讯 `https://web.ifzq.gtimg.cn/appstock/app/fqkline/get` 为首选，param `hk<code>,day,<start>,<end>,<limit>,`，只接受 `data.hk<code>.day`，不复权；东财 `stock_hk_hist` 为备源。共享 `select_history`：≥60 条立即采用；失败或不足试备源；两份均不足保留最长整份，同长取后者；不拼接。解析校验代码、日期、OHLC 与成交量，坏行隔离；腾讯行按相邻收盘计算涨跌幅。默认近一年，保留显式日期范围。
- **港股估值**：东财单标的快照 `https://push2.eastmoney.com/api/qt/stock/get`（secid=116.00700、fltt=2、invt=2）使用 f164（TTM PE）、f167（PB）、f86（快照时间）；校验 f57 代码、日期不晚于采集日。主源失败或无合法指标后切换百度 `https://finance.baidu.com/opendata` 的 PE(TTM)/PB；两项独立请求最多并发 2；共同日期取最近共同日，无共同日时保留较新一项并记录原因；上市日 `releaseDate` 与采集时间不作为指标日期。
- **诊断与来源**：`_append_collection_error` 保留主源与备源全部失败原因；成功来源写入 PriceData/ValuationData 的 `_data_source`，并由 `DataCollectionRecord` 持久化；缓存命中恢复 `record.source`，不单独恢复私有 `_data_source`。
- **符号**：港股只接受五位数字代码、`.HK` 或 `hk` 前缀，统一为 `00700.HK`；A 股保持六码与 `sh`/`sz` 行为。

## 3. 分析、报告与缓存实现

- **技术指标**：新增 `analysis/technical_indicators.py`，提供 MA5/20/60、量比（5 日/此前 20 日，分母 >0）、MACD（原 EMA，DEA 至少 34 个有效样本）；不足为 `None`。`TechnicalAnalyzer` 输出 `latest_close,ma_5,ma_20,ma_60,volume_ratio,macd_dif,macd_dea,macd_bar`，A 股评分器复用同一公式且结果不变；港股 `score=None`，不加载行业评分配置，不产生信号。
- **覆盖率**：`report/quality.py` 港股适用财务/技术/估值三维，`coverage=可用维度/3×100%`；行业、舆情标记 `not_applicable` 且不计入分母；新增 `available_dimensions`、`applicable_dimensions`、`coverage_basis`，保留 `has_scored_data=False`、`has_analysis_data`、`signal_allowed=False`。A 股 30/20/25/25 权重与信号门槛不变。
- **财报缓存**：新增 `data/validation.py`，至少一个报告期存在有限营收/净利润/资产/权益/经营现金流即可缓存；零/负值有效；全空、NaN/Inf、标的不匹配不可缓存。Pipeline 缓存健康检查统一调用该判据，TTL 604800 秒、刷新、断路器和诊断保持不变。
- **报告与前端**：模板保留章节，市场、来源、行业不适用及财报原币说明从上下文生成；维度不适用时显示“不适用：本期港股分析不支持此维度，不计入覆盖率”；质量区展示“可用维度覆盖 x/3”。静态资源版本链升至 `20261019-hk-shared-analysis-2`；浏览器与 CLI/Markdown 的技术指标均按 2/3 位小数展示。

## 4. 测试与检查

- 首轮全量：Ruff 通过，Pyright 0 errors/0 warnings，pytest **1746 passed、4 skipped、12 warnings、178.39 秒**；相关集成 **203 passed**。警告为既有 Starlette/LangGraph 弃用提示。
- 公共分析对齐后相关集成：`tests/data tests/analysis tests/core tests/report tests/api tests/test_verify_script.py` **863 passed**；Ruff 全过；Pyright 0 errors/0 warnings。
- 报告格式化修复后 `tests/report` **104 passed**；静态资源与 verify 映射测试通过；版本链无旧版本残留。
- 未跑重复全量；相同代码状态复用已通过结果。

## 5. 真实验收

### 5.1 首轮本地（财报修复）

`.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765`，Edge 真实提交 `00700.HK`，HTTP 200；2026-06-30 营收 396431000000、净利润 114115000000、总资产 2159394000000、总权益 1222513000000、经营现金流 154061000000；报告期正确、无港股评分信号；桌面截图与 390px 布局无水平溢出，页面脚本错误为零。

### 5.2 首轮云端（财报修复）

云端 `/tmp` 隔离候选、独立数据目录与端口、关闭 AI 和推送，真实启动 HTTP 服务并提交 `00700.HK`；财务值与本地一致、96 个报告期、`has_analysis_data=true`、无综合分/信号。行情与估值不可用、分析数据覆盖 15%，名称快照可能失败退回代码。该轮证明财报解析修复有效，**不是港股整体可用性验收通过**，不能用历史一次快照成功覆盖失败。

### 5.3 公共分析对齐后本地浏览器

隔离服务 8765（独立数据目录，llm/push 关闭），Edge 真实提交 `00700.HK`：

- 可用维度 **3/3（100%）**；技术为腾讯不复权 245 个交易日（2025-10-20 至 2026-10-16），MA5 422.00、MA20 427.93、MA60 443.23、量比 0.86、MACD DIF −5.305/DEA −5.058/柱值 −0.494。
- 估值为百度 2026-10-19 PE(TTM) 14.01、PB 2.90；东财快照失败原因保留。
- 财务为东方财富 96 期（2026-06-30）。
- 行业、舆情显示“不适用”且不计入覆盖率；无综合分/操作信号；390px 无水平溢出，页面脚本错误为零。
- 截图、响应和渲染文本留在忽略目录 `tmp/hk-shared-browser-20261019-b293ad/`。

### 5.4 CLI

隔离目录执行：

```bash
PYTHONPATH=<worktree>/src .venv/Scripts/python.exe -m stock_robot.cli analyze 00700.HK --no-llm --refresh-cache
```

退出码 0；可用维度 3/3；技术、财务、估值与不适用维度均正确；报告保存到隔离目录。首次验收发现终端/Markdown 指标输出原始浮点，修复后显示 `0.86 / -5.305 / -5.058 / -0.494`，由 `405c4e8` 提交。

### 5.5 公共分析对齐后云端

AstrBot 容器内 `/tmp` 隔离候选、独立数据目录与端口、关闭 llm/push：

| 标的 | 覆盖 | 耗时 | 关键结果 |
|---|---|---|---|
| `00700.HK` | 3/3 | 0.98 秒 | 价格 417.8；PE(TTM) 14.01、PB 2.90；营收 396431000000、净利 114115000000 |
| `09988.HK` | 3/3 | 0.76 秒 | 价格 108.8；PE 25.65、PB 1.79；35 期财报；营收 268953000000、净利 10614000000 |
| `01211.HK` | 3/3 | 0.78 秒 | 价格 73.75；PE 19.84、PB 2.43；88 期财报；营收 344815421000、净利 12325408000 |

三只标的的技术指标、财报、估值来源与不适用维度断言全部通过；Markdown 报告包含“可用维度覆盖：3/3（100.0%）”和行业/舆情不适用，且不含操作信号。验收 summary、三个响应和报告已取回本地忽略目录 `tmp/cloud-acceptance/stock-robot-hk-shared-validation-79wl9h7d/`。

## 6. 在线状态与清理

- 验收脚本比对受保护文件哈希：`instance/active-runtime.json`、`instance/.stock_robot/config.yaml`、`/AstrBot/data/config/astrbot_plugin_stock_robot_config.json` 均未变化。
- 在线 25618 `/health` 返回 `ok`、`version=0.2.0`；active-runtime 仍为 `.releases/release-omu_51ds`，未替换在线运行时、插件、配置或在线报告。
- 候选进程已停止；容器内候选临时目录与上传压缩包已清理；验收 summary 留在容器 `/tmp` 供追溯。
- 未重启 AstrBot、未发布、未推送、未合并。

## 7. 未覆盖与风险

- **未执行**：真实 AI 生成、QQ 图片投递、确认队列与回滚验收；本轮云端验收有意关闭 AI。
- **不稳定**：东财港股行情间歇断连；东财估值快照在本地与云端均失败并由百度降级；腾讯/百度单次成功不代表长期稳定或所有港股可用；容器断连根因未确定，不称封禁。
- **口径限制**：港股半年/年度财报、币种及会计口径不自动转换为 A 股季度 TTM；不换汇；不把财报原币与港币行情混算历史估值。
- **入口限制**：名称解析未统一，东财 push2 失败时退回代码；CLI 大盘环境、行业同行、新闻、回测、雷达、推送、汇率、H/A 合并与综合评分未实施。
- **超时风险**：来源切换增加最坏请求耗时，插件总超时未验证；不自行延长插件超时或提前发送报告。
- **发布边界**：工作树与两笔提交均未共享；合并、推送、部署或发布需另行授权。

## 8. 追溯

原始连接调查、真实数据修复记录与公共分析对齐设计/计划已压缩进本文与设计稿，逐项探针、历史响应和验收日志保留在 git 历史及忽略目录。当前正式文档以本实施记录和[设计稿](../../specs/2026-10/2026-10-16-hong-kong-stock-analysis-design.md)为准。
