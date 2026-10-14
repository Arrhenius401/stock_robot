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

已交付：旧缓存转换、明确URL原文采集、来源级限流、联网原文续跑。第三阶段已新增巨潮A股公告自动发现及支持模板的字段建议；更广来源、任意旧PDF结构及股类／年度完整性补证仍待后续，支付率数值门槛未启用。

## 公告自动发现与字段建议

第三阶段可从公司代码发现官方公告，再串联原文留档和字段建议：

```powershell
stock-robot radar dividend-discover --input tmp/dividend-materials/prepared.json --output tmp/dividend-auto/discovery.json --as-of 2026-10-14 --cache-dir tmp/dividend-auto/query-cache
stock-robot radar dividend-archive --input tmp/dividend-auto/discovery.json --archive-dir tmp/dividend-auto/pdf-archive --output tmp/dividend-auto/archived.json
stock-robot radar dividend-extract --archive-result tmp/dividend-auto/archived.json --archive-dir tmp/dividend-auto/pdf-archive --output tmp/dividend-auto/extracted.json --as-of 2026-10-14
```

开发工作树仍使用共享.venv和前文PYTHONPATH。发现输入可为公司列表或companies对象，精确A股代码与官网orgId匹配；两关键词为权益分派和利润分配，起点为截止年前第三年1月1日。支持分页、原始JSON缓存和refresh，源请求串行间隔1秒，15秒连接／读取超时、最多2次重试、429停止该来源。合法零结果的公告列表null仅在总数明确为0且无后续页时接受；字段缺失不能当零。

query_scope_complete只指这两个关键词的指定日期查询范围完整，不证明该公司三个财政年度分红资料已齐。标题高亮清洗，公告时间戳按北京时间解读，输出disclosed_on。实施候选及相关取消／终止／更正／调整等变更风险公告留档；后者独立discovery_status=pending_review，不因下载成功覆盖提醒。

提取器先核对URL对应安全文件名、SHA、大小和PDF头，使用同一已核验字节解析。支持明确的年度、中期、半年度实施模板及A股重要提示变体；本次实际金额、全年合计、中期参照分别输出，不相加。美的历史股东会方案金额与实施金额分段，回购后实际基数不明时不能拿总股本替代；A/H日期或金额不明保持待核验。扫描件与不支持的旧结构不能自动解释为无分红。

event_proposals沿用amount_yuan、fiscal_year、source_url、disclosed_on、share_class、paid_on等字段。requires_review恒true，financial_verified与rule_enabled恒false。matched_template表示支持模板提取，不表示已经核实金额、股类、利润或分红持续性；proposals不能直接作为研究命令的companies财务输入。审核确认后才整理标准化证据，程序不会自动填events、profits或coverage。

同时明确每股、实际参与基数与本次总额时，进行Decimal关系检查；允许≤0.01元的保守展示舍入差，明显不一致转待核验。较大单位的舍入差可能保守待核验，不自动放大容差；缺总额不得按股本乘每股金额推算。

本阶段新增原始零结果JSON及十份裁切文本资源。原始异常样本保留尾随空白、标题重字、历史审议误导段和中期变体，来源与裁切范围见[发现与提取计划](../../plans/2026-10/2026-10-14-stock-radar-dividend-discovery-extraction.md)。整份PDF和查询响应留在忽略tmp。

实际小样本三公司查询发现14份实施候选，解析复验10份匹配、4份待核验；这个数量不是公司支付率通过率或入池数量。仍待后续：更广来源与旧结构支持、年度／股类完整性及利润核实、经审核资料的标准化证据汇总。正式选股门槛仍未启用。

## 通用批量工作流与审核资料包

新增一个入口串联已有命令，不用逐公司或逐阶段手工重复运行：

```powershell
stock-robot radar dividend-workflow --input tmp/companies.json --workspace tmp/dividend-workflow --as-of 2026-10-14
```

输入可为公司对象列表或 `{"companies":[{"symbol":"600900","name":"长江电力"}]}`。输入必须放在workspace之外；工作目录绑定公司输入文件SHA和截止日，不同研究输入使用不同目录，不能静默混入旧结果。`--refresh`明确刷新查询与PDF，失败仍会记录本次失败；默认继续使用原采集器的来源级限流、重试及缓存核验。

已经核验并整理的规范证据可通过 `--evidence 规范证据.json` 显式提供，格式仍为现有companies结构；可以在同一工作目录补证后重跑，证据文件SHA记录在本次产物中。提取建议永不自动填入events、profits或coverage，供应商旧缓存仍仅是研究线索。

工作目录保留发现、原文留档、提取、证据准备、支付率研究和review-packet.json。审核资料包逐公司列出此前三个完整财政年度、未解决事项、原文失败原因、带稳定编号的建议及原文SHA／摘录，便于后续审核，不会删除没有成功提取建议的公司。年度证据行计数只表示字段存在，不表示已经核实，查询范围完成也不表示年度分红完整。

review-packet.json的revision_sha256绑定整包内容；建议proposal_id绑定公司、字段内容及原文摘要，排序改变不改变建议身份。它是核验入口资料，不是批准记录，requires_review=true，rule_enabled=false；正式入池和自动更新保持原状态。
