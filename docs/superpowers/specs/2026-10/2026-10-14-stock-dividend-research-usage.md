# 通用分红支付率研究命令

本工具接受任意候选公司的标准化离线证据，计算三年年度支付率及累计比率，供阈值实验、补证和未来股票池更新复用。它不自动发布成员，不启用正式支付率或股息率规则，不自动从PDF猜取金额。

## 使用

在实施工作树中通过项目环境运行：

```powershell
$env:PYTHONPATH = "$PWD/src;$PWD"
D:/code/stock_robot/.venv/Scripts/stock-robot.exe radar dividend-research --input tests/resources/radar/stock-dividend-evidence.json --output tmp/dividend-research/results.json --as-of 2026-10-14 --cache-dir tmp/dividend-research/cache
```

也可使用同一实现的脚本入口：

```powershell
D:/code/stock_robot/.venv/Scripts/python.exe scripts/research-stock-dividends.py --input tests/resources/radar/stock-dividend-evidence.json --output tmp/dividend-research/results.json --as-of 2026-10-14
```

截止日必须显式提供，研究年度为此前三个完整年度。例如2026-10-14研究2023、2024、2025年，而非过去36个月。脚本从环境导入模块，开发工作树须设置上述PYTHONPATH；安装好的CLI无需自行复制计算代码。

## 输入格式

顶层 `companies` 是公司对象列表，symbol在一个批次中必须唯一；空列表合法。只有symbol的公司也能运行，但会返回待核验。每家公司可提供：

| 字段 | 内容 |
|---|---|
| symbol / name | 股票代码与名称 |
| required_share_classes | 需要覆盖的普通股股类，例如A或A/H；未知不得默认只查A股 |
| profits | year、amount_yuan、currency=CNY、scope=company_ordinary、source_url、disclosed_on |
| coverage | year、complete=true、source_url、disclosed_on，明确该年度全部分红已补齐 |
| events | event_id、fiscal_year、share_class=A/H/ALL、amount_yuan、currency=CNY、source_url、disclosed_on、paid_on |

金额建议用十进制字符串保留元精度；分红为税前实际实施金额，回购不是派息，不将回购金额加到现金红利。普通股利润与对应普通股分红匹配，银行其他权益工具的利润／分红不得混入。来源字段记录事实，不表示程序已访问URL或自动证明材料真实，需要来源适配器或人工核验。

event_id标识同一分红事项，多个股类的同一事项共用ID；同一财政年度中期、末期等不同事项使用不同ID。年度合计标记amount_scope=fiscal_year_total，不能再同时加入同年中期／末期事件；可选covered_event_ids明确覆盖的事件也不能重复输入。覆盖声明须可靠；程序不能仅凭字符串识别跨不同event_id的所有业务重叠。精确重复去重，身份冲突或同事项ALL与A/H并存转待核验。

## 输出与缓存

- complete：本项三年证据满足计算要求，不等于入池或龙头核验完成。
- pending_verification：证据缺失、冲突、股类不齐、截点或金额问题；参考条件为null，不当作不满足门槛。
- annual_average：三个年度支付率的算术平均；cumulative_ratio：三年分红合计／利润合计。
- meets_reference_condition：仅供实验，表示均值及最近一年支付率都严格在0和1之间；100%不通过该参考条件。这个字段只判断支付率，不替代连续三年分红条件；其中一年零分红可能仍满足均值及最近年条件，后续选股必须组合独立分红持续性审核。
- rule_enabled固定false，不写数据库、股票池或计划任务。

缓存按输入证据、截止日及计算版本保存，每家公司独立。重复执行可续用已有结果，证据或截止日改变重新计算，损坏缓存重算。缓存默认不删除，输出完整JSON通过同目录临时文件原子替换；磁盘写入失败明确报错。输入输出同路径拒绝，防止覆盖原始证据。

## 样本与局限

`tests/resources/radar/stock-dividend-evidence.json`包含长江电力2023—2025年官方实施金额及2025年年报利润，另两家公司故意保留证据不足状态。真实来源、采样日期2026-10-14及提取口径见[普通企业实施证据实验](../../plans/2026-10/2026-10-14-stock-radar-dividend-evidence.md)。该资源只保留规范化必要字段，不提交整份PDF。年度合计与中期事件不得重复；年报追溯调整口径不是历史时点回测。

当前通用化的是证据校验和批量计算。第二阶段已补旧缓存转换及明确URL的原文留档，自动发现全市场公告与财务金额标准化仍在后续阶段；没有实际派息证据的685候选不会被这个命令自动放行。数值试验不替代分红可持续性、资本开支、债务、金融专项及治理审核。

## 资料准备与原文留档

第二阶段提供旧缓存转换和明确URL清单的公告留档：

```powershell
stock-robot radar dividend-prepare --candidates tmp/market-candidates.json --style-cache tmp/style-candidates.json --evidence tests/resources/radar/stock-dividend-evidence.json --output tmp/dividend-materials/prepared.json
stock-robot radar dividend-research --input tmp/dividend-materials/prepared.json --output tmp/dividend-materials/research.json --as-of 2026-10-14
stock-robot radar dividend-archive --input tests/resources/radar/stock-dividend-documents.json --archive-dir tmp/dividend-materials/official-archive --output tmp/dividend-materials/archive-result.json
```

开发工作树运行时仍须使用前文共享.venv路径与PYTHONPATH。标准化evidence可选：不提供时仅保留线索。旧候选缓存和style缓存都是公司对象列表，重复symbol报错；旧利润、每10股分红、供应商总股本等保留research_hints，不猜股类或年度事件完整性。输入路径与SHA256记录在准备产物中。

公告输入格式为 `{"documents":[{"symbol":"600900","source_url":"https://公司官方域名/公告.pdf","fiscal_year":2025,"kind":"annual_dividend_implementation","share_class":"ALL"}]}`。fiscal_year/kind/share_class属于调用者提供的描述，不代表下载器已核实PDF内容。相同URL只下载一次，公司描述不丢失。

默认允许已验证官方来源的精确主机：www.cypc.com.cn、static.cninfo.com.cn、www.hkexnews.hk、static.sse.com.cn、www.sse.com.cn、disc.static.szse.cn；其他公司官网可使用 `--allow-host 官方主机名`。只访问HTTPS，逐跳验证主机，无凭据URL/IP/localhost；HTML验证页拒绝，不绕过验证。输入清单和汇总输出必须放在原文归档目录之外，防止覆盖checkpoint或PDF。

最多两个来源并发，同一hostname串行执行以可靠处理429；429后该hostname本轮停止。连接／读取超时15秒，网络超时、连接或分块传输中断、5xx共用最多2次重试预算；流式下载累计耗时可能超过15秒，该参数不是整份文件的总时长截止。状态逐条写入manifest.json，可中断续跑；完整且SHA/大小/PDF头一致的原文默认复用，`--refresh`明确重新下载，失败保留正确旧原文并标出本次失败。缓存超过1GiB只在结果warnings提醒，不删除。

归档产物保留原始URL、最终重定向URL、SHA256、字节数和带时区抓取时间。downloaded/cached仅表示原文采集成功，financial_verified与rule_enabled均为false；不会修改标准化分红金额、成员库或自动任务。

`stock-dividend-documents.json`是2026-10-14从现有长江电力实施证据整理的三条官方URL清单，资源只保存描述与链接，完整PDF保留本地忽略tmp。接口与资源预算测试使用离线响应，不在业务测试中批量联网。

已交付：旧缓存转换、明确URL原文采集、来源级限流、联网原文续跑。仍待后续：自动发现全市场公告URL、不同来源财务字段标准化、任意PDF金额抽取及股类／年度完整性补证；支付率数值门槛仍未启用。
