# 通用分红资料准备与原文留档阶段计划

日期：2026-10-14。承接已提交的通用支付率研究基础阶段，用户已确认继续接入公告采集与缓存转换并要求独立成果及时本地提交。

## 范围与方案

新增缓存转换入口 `stock-robot radar dividend-prepare`：接受任意旧候选缓存及可选风格缓存，按symbol匹配，保留年度利润、每股分红等research_hints；实际金额、股类、年度完整性均不自动填为核实。可合并显式提供的标准化证据，但仍由上一阶段评估器校验。输出可直接交给dividend-research。来源路径与SHA256保存，不将旧supplier总股本乘每股金额当实际派息总额。

新增原文采集入口 `stock-robot radar dividend-archive` 与独立模块 `src/radar/stocks/source_archive.py`：接受明确URL公告清单，下载公开PDF并保存来源、SHA256、字节数、抓取时间、下载／缓存／失败状态及逐项checkpoint。来源URL发现与任意PDF财务金额抽取仍在后续适配器阶段，本阶段不声称已自动找出全市场全部实施公告。

输入 documents 每项有symbol、source_url，可另保留fiscal_year、kind、share_class等描述。同一URL只下载一次，不同公司描述仍保留。默认允许现有已核验官方来源精确主机名：www.cypc.com.cn、static.cninfo.com.cn、www.hkexnews.hk、static.sse.com.cn、www.sse.com.cn、disc.static.szse.cn；其他公司官网使用明确allow-host扩展。HTTPS、来源及每次重定向均验证；不接受凭据URL、IP或localhost，不绕过返回的验证页。

资源预算沿用已确认默认：最多并发2，每次请求超时15秒，网络异常与5xx最多重试2（总尝试3），429不重试并停止本轮该来源；已有在途最多2请求无法撤回，后续请求不得继续。缓存1GiB软上限只提醒，不自动删。PDF流式写入临时文件，校验PDF文件头后同目录原子替换；返回HTML即使HTTP200也失败。相同URL且原文SHA正确默认复用，refresh明确重取，失败保留正确旧原文并标明本次失败。

不新增依赖：requests已被现有radar数据源使用，复用共享环境。无数据库迁移、UI或后台自动任务。所有结果rule_enabled=false，原文downloaded不等于财务verified。

## 实施与验证

1. 测试先行：候选缓存转换（缺失、重复symbol、每股字段不伪造金额、显式证据匹配、输入来源摘要、不得覆盖输入）。
2. 网络独立模块测试：源与跳转限制、429停止、重试预算、HTML失败、SHA缓存及重启续跑、部分失败、刷新失败保留旧原文、并发上限、路径安全。
3. 接入CLI与定向验证映射，保留原有ETF及通用支付率命令，资源样本统一tests/resources/radar。
4. 按S级网络边界进行规格、质量双审查及修复后复核；原文真实下载小样本、完整685候选缓存转换＋研究串联验收。
5. 改动范围Ruff、Pyright、pytest和最终暂存差异检查。无UI／会话改动，不重复浏览器验收，不隐式执行全量。独立阶段本地提交，不推送或合并。

## 待后续完成

批量来源URL发现、不同公告结构抽取、真实派息基数／H股／回购证据标准化及事件完整性自动证明。不得因原文下载成功或线索转换成功，将685候选标成支付率合格或入池成员。
