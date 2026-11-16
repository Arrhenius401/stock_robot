# 股票原始日线与成交额研究

```powershell
stock-robot radar stock-market-window --calendar-db .stock_robot/radar_collector.db --as-of 2026-10-15 --output tmp/market-window.json
stock-robot radar stock-market-history --input companies.json --market-window tmp/market-window.json --as-of 2026-10-15 --cache-dir tmp/stock-history-cache --output tmp/stock-history.json
```

公司输入为非空companies对象列表或同样内容的JSON列表，每项symbol为沪深普通股票代码，支持任意候选集合，不固定20家或某个行业。窗口接受新envelope与原60日期列表。输入、窗口、输出不可重叠，也不能放在cache-dir内。该命令不改数据库或成员，不启用后台自动更新。

当前采用已实测的新浪原始历史日线。源变量必须匹配代码，只接受编码字符串赋值；不执行服务器返回的JS。复用已安装AkShare的可信解码常量，隔离子进程处理。价格保持未复权，成交额为来源原始元金额，不通过收盘价乘成交量估算。postVol/postAmt保留原字段，不自行合并。未复权价格用于原始行情和市值研究，不能直接作为总收益或复权趋势曲线。

每家公司保留实际URL、完整响应SHA256、采集时点和目标日原行。重复或无效记录标为缺失，停牌不填零、不ffill、不向更早交易日借行情。window_complete仅表示60日记录完整，不能当作公司或价格机会合格；顶层complete/partial仅表示采集执行状态。

只有60条目标日记录全部合法时才生成liquidity_fact。该对象可供后续显式证据添加及审核，始终是待审建议；它不批准准入、成长、价值或成员。disclosed_on记录实际采集的北京时间日期，不伪称历史公告日；采集晚于历史研究截点时不能当作当时可知事实。默认只导出，不自动保存或审核。

请求按已批准预算：串行、间隔1秒，单次15秒、最多重试2次，429停止该来源后续网络；无重定向、响应4MiB上限，必关闭响应。缓存保存完整原始响应，以内容摘要命名；刷新不会删除旧原文，命中仍重验摘要/身份并重新解析。失败不冒充缓存成功，缓存超过1GiB只提醒。完整下载响应和缓存只留本地tmp。

源接口与单位参考：[AkShare官方股票文档](https://akshare.akfamily.xyz/data/stock/stock.html)。本命令没有调用会合并流通股本并填充行情的stock_zh_a_daily封装。当前不推算全股类市值，不推算历史股本；股类/币种、复权与基准仍须独立接通。
