# 官方沪深证券候选名录

```powershell
stock-robot radar stock-universe-discover --as-of 2026-10-15 --cache-dir tmp/stock-roster-cache --output tmp/stock-roster.json
stock-robot radar stock-records-import --kind candidates --input tmp/stock-roster.json --db research.db --as-of 2026-10-15
```

第一条默认仅导出，不改研究库；第二条才显式导入共享候选，仍不审核或发布成员。扫描上交所主板、科创板和深交所A股表，不限既有20家或685家。三个来源的成功、失败和完整性分别保留，缺一个来源不能声称全市场完成。

返回companies是研究候选；上市日、来源行业代码/名称、原行、URL和响应摘要均可追踪。行业为名录原始标签，不冒充申万两级或行业龙头。issuer_type未知，不从公司名或金融业大类猜银行、证券或保险。深交所A/B股数只保留原文本，不把A股总股本/流通股本当作公司全股类股本，不据此推算市值。

普通A股范围明确排除689009存托凭证，单独保留范围排除记录；未知代码、重复、身份矛盾、分页不齐保留诊断。缺上市日为未知，不填默认年份。名录中的ST、未盈利等原始状态不被删除或改写为资格通过；经营、审计治理和龙头还需单独核验。

这些接口提供当前名录，不提供历史成分。当前北京日期可联网采集；历史日期仅复用当天抓取缓存，缺缓存返回部分结果，不以今天名单冒充历史。抓取时间不是交易所业务资料日期。security_proposal只是待审规范建议，默认不保存或审核，完整名录也不等于符合选股条件。

请求串行、间隔1秒，15秒总截止，最多重试2次，429按来源主机停止；SSE主板和科创共用主机限流，深交所独立。无重定向，原文4MiB上限；xlsx另有20MiB展开/64项/64列/20000行预算，错误dimension不会使22列丢失，公式不作为证券事实。生产解析同样隔离15秒，不增加依赖。

缓存使用内容摘要保留原始响应，命中仍校验并重解析，刷新不删除旧原文。超过1GiB仅提醒，统计失败不推翻已有结果。输出不能位于缓存内；实际数据、缓存和完整下载响应留本地，不自动推送、合并或发布。

来源：[上交所股票名录](https://www.sse.com.cn/assortment/stock/list/share/)、[深交所A股名录文件](https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1110&TABKEY=tab1)。当前功能尚未提供全股类历史股本、公司总市值核验或行业龙头证据。
