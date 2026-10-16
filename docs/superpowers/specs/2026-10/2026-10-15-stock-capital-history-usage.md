# 股票股本变动研究资料命令

此命令接通巨潮资讯公开股本变动原文，为后续审核提供可追溯资料。**采集完整不等于市值资格通过**。单位、全部股类覆盖以及GDR底层股份映射尚未核实，命令不换算股数、不计算市值、不写审核或成员。

## 使用

在实施工作树运行，显式复用主仓库环境：

```powershell
$env:PYTHONPATH="$PWD/src;$PWD"
& D:/code/stock_robot/.venv/Scripts/stock-robot.exe radar stock-capital-history `
  --input tmp/companies.json --as-of 2026-10-15 --start-date 1990-01-01 `
  --cache-dir tmp/stock-capital-history-cache --output tmp/capital-history.json
```

输入可为公司对象数组，或含`companies`数组的对象；每家公司须有合法沪深普通A股六位`symbol`，不得重复。默认起点1990-01-01只表示查询范围，不是公司上市日期或历史覆盖证明。未来截点、倒序范围、覆盖输入或缓存内部的输出会在请求前拒绝。`--refresh`重新采集，失败不会伪装成旧缓存成功。

## 输出口径

逐公司保留URL、查询范围、SHA256、实际采集时间、原文归档名、原始字段、公告日与变动日。只有两个日期都不晚于研究截点的合法记录才作为该截点研究记录；未来变动、截点后公告、身份冲突、重复等单独诊断，不择优取数。

`F003N`、`F022N`等保留来源字段。`F022N`仅为流通股份中的人民币普通股，不是全部A股；不能用它加H股后推断历史公司全部股份。`unit_verified=false`、`all_share_classes_verified=false`和`financial_verified=false`始终保留，缺失值不填零。对历史截点查询当前接口会标明回溯研究，`point_in_time_verified=false`，不冒充过去当天保存的快照。

`complete`只说明本次请求范围的返回数量与合法解析情况，不证明所有历史事件无遗漏。即使完整也没有`capitalization_fact`，不能直接用作200亿元市值筛选事实。

来源：[巨潮资讯公开API文档](https://webapi.cninfo.com.cn/#/apiDoc)，接口`p_stock2215`。字段映射参考已安装AkShare `stock_share_change_cninfo`；单位仍须补官方说明或独立原文审核，不能依数量级反推后认定已核实。

## 请求、缓存及失败

复用已有AkShare和mini_racer，不新增依赖。只执行安装包内可信时间编码函数以生成公开请求头，不保存动态头；不处理账户或验证码。生产请求含头生成与完整响应读取总截止15秒，单响应4MiB，串行间隔1秒，网络/5xx最多重试2次（沿用功能设计第6节，重试退避3秒、10秒）。401、403、429或权限/验证码挑战会停止该来源后续网络，已有效缓存仍可读取。

HTTP200预算内原文先按内容摘要归档，再严格解析。缓存复用保留首次采集时间并重新解析；异常缓存不得生成成功事实。1GiB缓存软上限仅提醒，不自动删除。完整原文、缓存、错误诊断和真实验收输出只放忽略目录`tmp/`。
