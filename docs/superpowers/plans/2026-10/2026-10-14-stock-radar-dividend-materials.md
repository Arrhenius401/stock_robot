# 通用分红资料准备与原文留档阶段计划

日期：2026-10-14。承接已提交的通用支付率研究基础阶段，用户已确认继续接入公告采集与缓存转换并要求独立成果及时本地提交。

## 范围与方案

新增缓存转换入口 `stock-robot radar dividend-prepare`：接受任意旧候选缓存及可选风格缓存，按symbol匹配，保留年度利润、每股分红等research_hints；实际金额、股类、年度完整性均不自动填为核实。可合并显式提供的标准化证据，但仍由上一阶段评估器校验。输出可直接交给dividend-research。来源路径与SHA256保存，不将旧supplier总股本乘每股金额当实际派息总额。

新增原文采集入口 `stock-robot radar dividend-archive` 与独立模块 `src/radar/stocks/source_archive.py`：接受明确URL公告清单，下载公开PDF并保存来源、SHA256、字节数、抓取时间、下载／缓存／失败状态及逐项checkpoint。来源URL发现与任意PDF财务金额抽取仍在后续适配器阶段，本阶段不声称已自动找出全市场全部实施公告。

输入 documents 每项有symbol、source_url，可另保留fiscal_year、kind、share_class等描述。同一URL只下载一次，不同公司描述仍保留。默认允许现有已核验官方来源精确主机名：www.cypc.com.cn、static.cninfo.com.cn、www.hkexnews.hk、static.sse.com.cn、www.sse.com.cn、disc.static.szse.cn；其他公司官网使用明确allow-host扩展。HTTPS、来源及每次重定向均验证；不接受凭据URL、IP或localhost，不绕过返回的验证页。

资源预算沿用已确认默认：最多并发2，每次连接／读取超时15秒（流式下载累计耗时可能超过15秒，不是整个文件总时长截止），网络异常与5xx最多重试2（总尝试3），429不重试并停止本轮该来源；已有在途最多2请求无法撤回，后续请求不得继续。缓存1GiB软上限只提醒，不自动删。PDF流式写入临时文件，校验PDF文件头后同目录原子替换；返回HTML即使HTTP200也失败。相同URL且原文SHA正确默认复用，refresh明确重取，失败保留正确旧原文并标明本次失败。

不新增依赖：requests已被现有radar数据源使用，复用共享环境。无数据库迁移、UI或后台自动任务。所有结果rule_enabled=false，原文downloaded不等于财务verified。

## 实施与验证

1. 测试先行：候选缓存转换（缺失、重复symbol、每股字段不伪造金额、显式证据匹配、输入来源摘要、不得覆盖输入）。
2. 网络独立模块测试：源与跳转限制、429停止、重试预算、HTML失败、SHA缓存及重启续跑、部分失败、刷新失败保留旧原文、并发上限、路径安全。
3. 接入CLI与定向验证映射，保留原有ETF及通用支付率命令，资源样本统一tests/resources/radar。
4. 按S级网络边界进行规格、质量双审查及修复后复核；原文真实下载小样本、完整685候选缓存转换＋研究串联验收。
5. 改动范围Ruff、Pyright、pytest和最终暂存差异检查。无UI／会话改动，不重复浏览器验收，不隐式执行全量。独立阶段本地提交，不推送或合并。

## 待后续完成

批量来源URL发现、不同公告结构抽取、真实派息基数／H股／回购证据标准化及事件完整性自动证明。不得因原文下载成功或线索转换成功，将685候选标成支付率合格或入池成员。

<a id="historical-2026-10-14-stock-radar-dividend-materials-validation"></a>

## 分红资料准备与原文留档阶段验收

> 2026-11-24 文档整理：本节合并自 `2026-10-14-stock-radar-dividend-materials-validation.md`，保留原历史记录；以下结果仅适用于所述日期和代码状态，不代表当前验收结果。

日期：2026-10-14。实施工作树 `C:/Users/25618/.codex/worktrees/stock-radar-pools/stock_robot`，分支codex/stock-radar-pools。承接通用评估阶段提交65673b2，按用户要求作为独立阶段本地提交，不推送、合并或发布。

### 实际结果

- `dividend-prepare`将任意旧候选缓存、可选风格缓存与显式标准化证据按symbol组合，原始输入路径／SHA256可追溯。供应商利润及每股分红字段只保留线索，不猜实际金额、普通股口径、股类或事件完整性。
- `dividend-archive`按明确官方URL清单下载PDF，最多两个来源并发，同hostname串行处理429；15秒连接／读取超时，下载累计时长可能更长；网络与5xx共享最多2次重试预算。来源／跳转验证、PDF头检查、原子文件替换、逐项checkpoint、SHA缓存续跑及1GiB软上限提醒均实现。
- 失败隔离、刷新失败保留旧原文、输入／输出与归档目录分离，financial_verified与rule_enabled恒false。没有成员库写入或后台自动任务。

### 真实验收

共享.venv及工作树PYTHONPATH下执行以下真实命令：

1. `radar dividend-prepare --candidates D:/code/stock_robot/tmp/stock-radar-market-20261013/market-candidates.json --style-cache D:/code/stock_robot/tmp/stock-radar-market-20261013/style-candidates.json --evidence tests/resources/radar/stock-dividend-evidence.json --output tmp/dividend-materials/prepared.json`：685家公司，保留2580条分红事件线索。
2. `radar dividend-research --input tmp/dividend-materials/prepared.json --output tmp/dividend-materials/research.json --as-of 2026-10-14`：完整1、待核验684。只显式长江样本提供实际金额证据；不是685家公司支付率通过率。
3. `radar dividend-archive --input tests/resources/radar/stock-dividend-documents.json --archive-dir tmp/dividend-materials/official-archive --output tmp/dividend-materials/archive-result.json`：三份长江电力2023—2025实施公告全部downloaded，字节数739950、218729、150126，记录原始／最终URL、SHA256和抓取时间。
4. 同一命令再次运行输出archive-resumed.json：三份均cached，SHA长度和final_url断言通过。输出financial_verified=false，未自动将原文转换为财务事实。

### 检查及审查

最终10个源码／测试／资源／使用说明／计划暂存文件执行 `scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：Ruff通过、Pyright零错误，115项pytest通过（25.20秒），成功测试目录由入口清理。之后仅添加本验收文档，提交前执行暂存差异空白检查；未再次重复业务测试。

S级规格、质量双审查及问题复核通过。修复缓存权限错误可中断批次、流式传输断开未共享重试、响应关闭异常误报成功文件失败等问题；另保留缓存最终URL、验证四个跨来源请求仍最多并发2及软缓存只提醒不删。相应回归纳入最终门禁。

静态修复中一轮第三方响应清理noqa未使用告警失败，调整为记录具体异常且注明隔离边界后通过；失败运行目录保留，没有将失败结果算成通过。未运行全量测试，无UI／会话改动，不重复浏览器验收。

### 尚未完成

官方URL自动发现、任意PDF的分红金额／派息基数抽取、多股类与财政年度完整性自动核实仍需后续适配器。当前清单中的fiscal_year／kind／share_class是调用者描述，下载器不证明其财务含义。正式选股门槛、成员发布和自动更新未启用；原始PDF、大缓存及测试产物都留在忽略tmp。
