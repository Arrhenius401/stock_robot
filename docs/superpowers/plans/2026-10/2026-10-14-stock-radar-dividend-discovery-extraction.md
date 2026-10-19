# 官方公告发现与实施字段建议阶段计划

日期：2026-10-14。承接已提交的支付率计算、旧缓存转换及原文留档阶段，用户同意继续公告自动发现和财务字段提取；正式数值筛选、成员发布仍关闭。阶段成果独立本地提交。

## 1. 官方接口事实与隔离探针

从巨潮官网公开 `common.js` 与全文检索页面的 `search_new.js` 查到股票搜索及公告查询路径，不执行网站脚本、设备指纹或验证逻辑。官网源码与响应保存于主仓库忽略目录 `tmp/dividend-discovery-probe-20261014/`。

- 股票搜索POST `https://www.cninfo.com.cn/new/information/topSearch/query`，keyWord=600900、maxNum=10；返回code、category、orgId、type、名称。实测精确A股600900、orgId=gssh0600900。机构ID必须查得且唯一，不拼接猜测。
- 公告POST `https://www.cninfo.com.cn/new/hisAnnouncement/query`，stock=代码与orgId、tabName=fulltext、column=sse或szse、pageSize=30、pageNum、searchkey、seDate及isHLtitle。官网脚本确认stock拼接和接口；其余字段通过单公司隔离请求验证。
- `searchkey=权益分派`、2024-01-01至2026-10-14实测返回5条长江实施公告，含三年年度与两次中期。返回totalAnnouncement=5、hasMore=false、totalpages=0，不能用totalpages判定分页；标题带em高亮，日期UTC毫秒，PDF为finalpage相对路径。

元数据来源以官网真实响应为准，不依赖第三方接口教程作正式规范。查询起点采用as_of年前第三年1月1日，允许发现同年中期派息，之后按公告实际财政年度核验。

## 2. 范围与接口

`announcement_discovery.py`：任意A股公司列表，精确匹配官方身份；分别查权益分派、利润分配两关键词，逐页保存原始响应，核对总数稳定、身份、公告ID与重复页，清理HTML标题并按北京时间确认截点。只保留实施公告候选，取消／更正等不能默默替代原文。PDF路径必须属于static.cninfo.com.cn的安全finalpage路径，不能接受任意绝对URL或路径穿越。

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
