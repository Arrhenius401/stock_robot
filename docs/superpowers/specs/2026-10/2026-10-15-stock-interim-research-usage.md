# 季度与半年报研究资料

本功能属于第一阶段数据接通，发现Q1/H1/Q3完整报告，并从已归档PDF提取累计字段。不会审核事实、确认最新报告或发布股票池成员。

```powershell
stock-robot radar interim-discover --input companies.json --cache-dir tmp/interim-cache --as-of 2026-10-15 --output discovered.json
# 用现有dividend-materials归档发现的documents，沿用官方来源、大小及hash校验。
stock-robot radar interim-extract --archive-result archive.json --archive-dir tmp/pdf --as-of 2026-10-15 --output extracted.json
stock-robot radar interim-review-packet --input extracted.json --as-of 2026-10-15 --output packet.json
stock-robot radar stock-records-import --kind review_packet --input packet.json --db research.db --as-of 2026-10-15
```

公司输入沿用公告发现器companies格式。发现仅查询对应关键词，查询成功不等于最新报告范围已核实。摘要、英文和特殊版本不会自动变成可用事实。请求与缓存预算沿用已有实现。

支持明确人民币单位和已核验布局：Q1/H1当前及上年同期，Q3只取年初累计；调整后比较列保留重述标志，不能据此自动认定可比。归母利润、扣非归母利润、经营现金流、收入分别保存原始列、页码、摘录、单位及PDF摘要。真正多金额表、冲突单位、未知列布局、身份错误或期间错链都保持待核验，不猜金额。

发现与PDF期间冲突时清空金额建议，记录两个原期间；合法较新期末仅作为阻止回退旧报告的线索，不是已核实财报。提取失败的文档也可留档。导入产生quarterly_financial_proposal与quarterly_financial_document，全部未经审核，已有年报字段不受替换。第一笔功能尚未计算TTM。

输入输出不能互相覆盖，输出不能写入PDF归档目录或查询缓存。完整PDF、缓存、数据库、日志和提取输出只留本地tmp；裁切测试资源与来源记录随测试提交。
