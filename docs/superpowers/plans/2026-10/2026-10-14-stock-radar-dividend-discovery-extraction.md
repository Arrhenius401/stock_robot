# 官方公告发现与实施字段建议阶段计划

日期：2026-10-14。承接已提交的支付率计算、旧缓存转换及原文留档阶段，用户同意继续公告自动发现和财务字段提取；正式数值筛选、成员发布仍关闭。阶段成果独立本地提交。

## 1. 官方接口事实与隔离探针

从巨潮官网公开 `common.js` 与全文检索页面的 `search_new.js` 查到股票搜索及公告查询路径，不执行网站脚本、设备指纹或验证逻辑。官网源码与响应保存于主仓库忽略目录 `tmp/dividend-discovery-probe-20261014/`。

- 股票搜索POST `https://www.cninfo.com.cn/new/information/topSearch/query`，keyWord=600900、maxNum=10；返回code、category、orgId、type、名称。实测精确A股600900、orgId=gssh0600900。机构ID必须查得且唯一，不拼接猜测。
- 公告POST `https://www.cninfo.com.cn/new/hisAnnouncement/query`，stock=代码与orgId、tabName=fulltext、column=sse或szse、pageSize=30、pageNum、searchkey、seDate及isHLtitle。官网脚本确认stock拼接和接口；其余字段通过单公司隔离请求验证。
- `searchkey=权益分派`、2024-01-01至2026-10-14实测返回5条长江实施公告，含三年年度与两次中期。返回totalAnnouncement=5、hasMore=false、totalpages=0，不能用totalpages判定分页；标题带em高亮，日期UTC毫秒，PDF为finalpage相对路径。

元数据来源以官网真实响应为准，不依赖第三方接口教程作正式规范。查询起点采用as_of年前第三年1月1日，允许发现同年中期派息，之后按公告实际财政年度核验。

## 2. 范围与接口

`announcement_discovery.py`：任意A股公司列表，精确匹配官方身份；分别查权益分派、利润分配两关键词，逐页保存原始响应，核对总数稳定、身份、公告ID与重复页，清理HTML标题并按北京时间确认截点。保留实施公告候选及相关变更风险公告，取消／更正等不能默默替代原文。PDF路径必须属于static.cninfo.com.cn的安全finalpage路径，不能接受任意绝对URL或路径穿越。

输出documents供既有archive命令直接留档；query_scope_complete只指两个关键词在给定截点的查询范围完整，绝不据此填财政年度分红coverage。空查询结果不证明没分红；失败保留部分发现和显式错误，429停止整个来源本轮后续查询。缓存按公司、截止日和请求字段独立、逐页断点记录，刷新显式重取。

`dividend_extract.py`：在核验PDF文件名／路径、SHA和PDF头后，用已有pypdf提取文本。只对明确实施公告模板提取字段建议，股票代码须匹配、财政年度需明确。金额使用Decimal和明确单位，本次实际金额、每股／实际基数、全年合计和已派中期分别保留，不把预案金额选作实施金额，不猜缺失股类或H股派息日期。扫描件、冲突、多种金额关系无法确认的文本转待核验。

输出event_proposals采用现有财政年度、amount_yuan、source_url、disclosed_on、share_class、paid_on等标准字段，额外标requires_review。全年合计没有完整派息日期证据时不填paid_on；全年合计不得再次叠加中期／末期。本阶段建议不直接写评估器events，不生成profits或coverage，不声称解析结果已经财务verified。

CLI新增 `radar dividend-discover`（input/output/as-of/cache-dir/refresh）和 `radar dividend-extract`（archive-result/archive-dir/output/as-of），现有研究／转换／留档命令不变。输入和输出分离，原文目录内部不得输出汇总，避免覆盖原始材料。所有产物financial_verified与rule_enabled=false。

## 3. 资源及异常策略

复用requests、pypdf，无新依赖、UI、数据库或自动任务。官方查询串行，源请求间隔1秒，仍不超过已确认并发2；连接／读取超时15秒，网络与5xx最多重试2，429不重试并停止源，不绕过HTML验证或重定向限制。缓存1GiB软上限只提醒，不自动删。

PDF金额字段建议不能作为“机器已核实”证据。元数据或原文来源失败、截点不一致、SHA错误、文件越界、异常PDF均单项隔离。即使模板匹配也保留审核状态；支付率门槛仍等待实验结论。

## 4. 任务与验证

1. 官方发现模块测试先行：精确身份、重复／冲突、HTML、时区、分页一致性、截点、429与重试、缓存续跑、PDF路径。
2. 实施建议模块测试先行：真实裁切的长江、美的、神华原始文本及构造冲突样本；金额不能取历史预案，回购后基数、ALL/A/H、未知股类、扫描、日期、SHA和路径边界。
3. CLI、验证映射、必要资源和使用说明接入。真实源全文保留tmp，离线必要样本统一tests/resources/radar，来源／采样日期／裁切口径在本计划及验收中记录。
4. S级规格／质量双审查，修复后复核。真实小样本通过“公司输入→发现→留档→字段建议”，第二次缓存续跑；与旧缓存和支付率工具不混淆。
5. 改动范围Ruff／Pyright／pytest、暂存差异检查，符合独立验收后本地提交。不运行全量或重复UI会话验收。

样本来源补充：2026-10-14采样。五份初始长江／神华／美的文本来自已有payout-evidence官方PDF；长江／神华裁至四、分配实施办法前，美的裁至三、分配对象前，保留历史方案误导与重字标题。新增中期／重要提示变体来自真实巨潮公告ID1224966232、1222342911、1224799414、1223787314、1224784190，披露日分别2026-02-05、2025-01-16、2025-11-12、2025-06-06、2025-11-04，来源https://static.cninfo.com.cn/finalpage/对应日期/公告ID.PDF；长江／神华同前裁切，美的裁至三、利润分配对象前。零命中资源cninfo-000333-announcements-empty.json保留官方完整小JSON，原始公告响应null格式是实际异常回归样本。明确金额关系容差0.01元只作保守展示舍入检查，大单位舍入差可转待核验，不推算缺失金额。

<a id="historical-2026-10-14-stock-radar-dividend-discovery-extraction-validation"></a>

## 分红公告发现与字段建议阶段验收

> 2026-11-24 文档整理：本节合并自 `2026-10-14-stock-radar-dividend-discovery-extraction-validation.md`，保留原历史记录；以下结果仅适用于所述日期和代码状态，不代表当前验收结果。

日期：2026-10-14。对应实施计划 `2026-10-14-stock-radar-dividend-discovery-extraction.md`。仅完成资料自动化阶段，正式选股、股票池成员发布和自动更新仍关闭。

### 实际改动

- 添加通用官方公告发现命令 `radar dividend-discover`：官方身份精确匹配、两关键词分页查询、原始响应缓存、断点记录、相关变更风险保留。
- 添加 `radar dividend-extract`：核验留档文件及校验和，从实施公告生成待审核字段建议；复用既有下载与支付率研究命令，不直接写入已核验财务证据。
- 接入 CLI、按范围验证映射、真实公告裁切文本和使用说明。没有新增依赖、UI、数据库或后台调度。

### 真实来源验收

以长江电力600900、美的集团000333、中国神华601088，截止日2026-10-14，执行公司输入→官方发现→原文留档→字段建议。

- 三家公司关键词查询范围完成，分别发现6、5、3份公告，共14份；14份PDF留档成功。这不是财政年度分红完整性证明。
- 首轮解析5份匹配、9份待核验；补充通用中期及提示栏模板后，最终10份匹配、4份待核验，生成16条字段建议，全部保留 `requires_review=true`。
- `financial_verified=false`、`rule_enabled=false`。建议数量包含不同口径，不得直接相加为实际分红。
- 再次发现全部查询页复用缓存，三家公司仍完成查询范围。未运行全市场685家公司公告核验。
- 原始查询响应、14份PDF、各轮输出保留在忽略目录 `tmp/dividend-auto/`；官网脚本探针保留在主仓库 `tmp/dividend-discovery-probe-20261014/`。

### 审查与回归

完成独立规格和质量审查，并复核修复。修复包括真实零结果 `announcements:null` 格式、发现与留档状态分离、从已核验字节解析PDF、修订公告标题、金额关系检查，以及回购后实际基数缺失或相互冲突时禁止使用总股本替代。

真实离线资源的来源、采样时间和裁切范围见实施计划。资源保留实际重字标题、历史预案段落和行尾空白；`.gitattributes` 仅对 `tests/resources/radar/*-implementation.txt` 豁免行尾及文件尾空白检查，源码仍执行正常格式检查。首次暂存格式门禁因这些原文空白及一处源码文件尾空行失败；清理源码空行、限定资源属性后复核通过。

最终命令：

```powershell
.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv
```

结果：23个暂存文件，Ruff通过，Pyright为0 errors、0 warnings、0 informations，165个相关pytest用例通过（32.89秒）。唯一测试临时目录运行结束后已清理。`git diff --cached --check` 通过。补充本验收文档后仅复核暂存差异格式，不重复业务测试。

### 尚未覆盖的范围

扫描件、部分旧模板、复杂股类及日期证据不足仍为待核验；未自动生成归母利润、年度覆盖证明或完整A/H股分红证据。官方关键词查询范围完成不代表所有分红公告发现完整。未做全量回归；本阶段无前端或会话改动，复用此前真实UI验收，不重复启动服务。股票池正式准入标准与成员不因本阶段而改变。
