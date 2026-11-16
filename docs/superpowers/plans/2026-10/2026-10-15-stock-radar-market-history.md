# 第一阶段：股票原始日线与成交额研究适配器

用户要求持续实施无需确认，沿用design-before-implementation与已批准资源预算；不增加投资阈值，不发布成员，不启用自动更新。属于数据接通，非新增交付阶段。

## 探针事实和方案取舍

- 已读安装版ak.stock_zh_a_hist：东财HTTPS kline，klt=101/fqt=0；真实请求被ConnectionResetError10054重置，不能声称源可用。
- 已读安装版ak.stock_zh_a_daily：SDK会outer merge流通股本、ffill、drop_duplicates，不能用于严格60日真实记录；不调用该封装。
- 直接新浪URL https://finance.sina.com.cn/realstock/company/sh600900/hisdata_klc2/klc_kl.js 实际200、65705字节，decoded 5138条，最后2026-10-15 close29.6/volume138200939/amount4072104696，另有postVol/postAmt，均保留不合并猜测。
- body首部var KLC_K2_sh600900="..."; 已确认身份可绑定。可信解码常量akshare.stock.stock_zh_a_sina.hk_js_decode，py_mini_racer.MiniRacer.call支持timeout和max_memory。上下文eval可信常量、call把编码字符串作为数据传入；绝不eval服务器JS。
- 生产解码在现有bounded_provider_call子进程中，deadline15秒；V8 eval/call各2秒限制、max_memory=64MiB。复用已安装AkShare/mini_racer，不加依赖。先用该已实测可用来源；东财不引入故障源强制重试链。
- 官方AkShare文档 https://akshare.akfamily.xyz/data/stock/stock.html 历史日线章节说明未复权/日成交额元；新浪章节amount元、volume股。此适配器按原始解码字段，不把复权或流通股本带入。未复权价格不能直接当总收益或趋势复权序列。

## 模块与契约

新增radar.stocks.market_history：sina_url(symbol)固定HTTPS主机与路径，只支持现有沪深股票_symbol；decode_sina_body(raw,symbol)->list使用完整assignment正则核对变量身份和JSON字符串，不执行响应。parse_sina_history(raw,symbol,market_window,*,decoder=None)解析已知解码行、校验日期（YYYY-MM-DDT00:00:00.000Z视为源交易日期而非逐笔时点）和OHLC正值范围、volume非负、amount非负，数值Decimal字符串；保留postVol/postAmt及原始行。仅目标window内数据进入rows；重复目标日全部标未知/冲突不取有利条，缺失日不补零/不ffill/不挪更早行情。返回rows、missing_dates、diagnostics、window_complete、currency=CNY、price_basis=unadjusted、amount_basis=source_raw_turnover，research_only/financial_verified=false/formalfalse。窗口须60递增唯一合法日期。

collect_stock_history(companies,market_window,cache_dir,*,as_of,refresh=False,http_get=None,sleep=...)批量串行请求（<=已批准并发2），每次timeout15、最多重试2次、请求间隔1秒、429立即停止此来源后续网络。响应stream读取上限4MiB，禁重定向，必close。HTTP4xx非429不重试、5xx和requests/OSError重试。单公司失败隔离，初始输入/窗口一次全校验再建目录；不因一家公司成功将整批标全成功。

缓存body按真实SHA256命名<sha>.js，manifest按URL/symbol/asof/window/version摘要命名，原始数据刷新不删除旧blob；缓存命中重验字节/hash/身份并重跑当前解析器，禁止符号链接/路径逃逸、坏摘要重抓。失败刷新不能把旧cache当最新成功。缓存1GiB软提醒不删除。写入原子；不将完整响应、缓存、日志提交。

输出items逐公司status=downloaded/cached/source_error/source_stopped、url、content_sha256、fetched_at、parse结果；status=complete/partial仅采集执行状态。只在60日真实记录全部合法时生成liquidity_fact={kind:style_liquidity,payload:{currency:CNY,scope:a_share_raw_turnover,daily:[date,amount_yuan],price_basis:unadjusted,amount_basis:source_raw_turnover,as_of,fetched_at},source_url,content_sha256,disclosed_on:实际采集北京日期}，并requires_review=true。disclosed_on不伪装历史公告日，历史研究若采集晚于截点不能用作当时已知事实。该fact仅导出供后续显式证据添加/审核，不写DB或自动确认。市值不推算、复权/基准/趋势另行处理。

新增stock_robot.stock_market_history命令stock-market-history --input companies.json --market-window JSON --as-of --cache-dir --output [--refresh]。复用_read_market_window和候选规范；输入/窗口/输出不能互相覆盖或位于cache内，检查在任何网络/写入之前。默认无DB写入，注册CLI由父处理。

## 分工与验收

S级跨API：实现代理仅market_history.py及test_stock_market_history.py；父CLI/CLI测试/注册/映射/文档及真实网络。独立规格、质量双审查/修复复核。测试先行：真实3行样本(仅裁解码后末3日，不是完整下载响应)、非法身份/注入字符串、重复/缺失/零成交/坏OHLC/金额单位/源post字段、不变输入；HTTP预算/429/禁redirect/size/close、cache摘要破坏/符号链接/刷新不可覆盖旧blob/部分失败/不碰DB。V8依赖原有已安装，无新增安装。

实际原文与完整解码仅tmp/stock-market-history-probe，采样2026-10-15；tests/resources/radar/sina-600900-2026-daily-sample.json为末3条解码行，完整JS摘要见tmp/sina-request.json，正式验证后补。verify映射明确；用真实600900采集→缓存重用→生成待审流动性fact，不审核/不入池。相关检查通过完整事项本地提交；不全量、不重复UI验收。

## 原文摘要与补充实现决定

真实新浪完整JS SHA256=88620bac9829a4c357918db16014ed9e753f3bf78bd2479b44820a11fe687812（65705字节）；裁切样本只有末3条解码记录，原始响应/完整解码留tmp，不提交。MiniRacer当前导入from py_mini_racer import MiniRacer，eval/call timeout=2000/max_memory=64MiB探针通过，不能用旧py_mini_racer子模块入口。

HTTP requests timeout只限制socket等待，不能限制慢速流整体时长；因此生产完整GET/流式读取/关闭响应也放现有bounded_provider_call，单次15秒总deadline；注入fake HTTP仅用于测试，正常CLI不会绕过隔离。429必须返回状态后由父流程停源，不能扁平为普通重试异常。解码单独15秒进程deadline，两步V8各2秒。

父审发现畸形manifest JSON若为列表/null可能在逐公司隔离外抛AttributeError，已交实现收窄类型并将缓存异常保留为可重抓/逐公司失败。CLI先红ImportError（模块未实现），实现后11项输入保护与注册测试通过。

## 真实验收、异常及修复

正式CLI第一次返回source_error：真实JS赋值后还带单个281字符完整块注释，严格正则原先未支持；不把来源失败说成成功。修复仅允许该完整惰性尾注释，嵌套/未闭合/多块/额外执行仍拒绝，服务器JS从不eval。实际65705字节probe经身份校验通过。

源200响应在解析前按SHA留blob，即使身份或解析失败也保留raw_artifact、摘要和fetched_at，错误manifest仍source_error、无成功缓存和事实。生产过大/类型错误改结构化非瞬态不重试，超时/网络错误仍最多三次；429不扁平化。

真实stock-market-history重新执行：600900 status=downloaded、window_complete=true、60日/缺口0，2026-10-15 close29.6、raw amount4072104696元、postVol68000/postAmt2012800原样保留未相加；完整JS摘要与前述相同，fetched_at=2026-10-15T12:58:00.796850+00:00。第二次status=cached，原摘要和fetched_at保持，重新解析，不额外联网。

通过真实stock-evidence-add将导出的style_liquidity显式留档为pending，evidence_id=af62cf4f50c0196fd807a4cf724a0c085c80a888812fe54e2d80660917cacf95，600900 revision5。库companies685/evidence802/reviews0/batches4；两池重新计算的liquidity均unavailable，证明未审资料不放行；未发布成员。旧评估按来源版本自然失效，不覆盖历史。

质量审查追加实读上限防stat后文件增长，非JSON/NaN辅助字段导致该日未知而不丢其他公司输出，缓存stat/iterdir错误只提醒不推翻已完成采集；修复后70项测试通过，1项Windows符号链接权限跳过，Ruff/Pyright0。CLI11项测试通过。只涉及CLI与数据层，无UI变更，无需重复浏览器验收；不运行全量。

最终门禁：verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv，Ruff通过、Pyright 0错误0警告、255项相关测试通过/1项Windows符号链接权限跳过（51.82秒），成功目录已清理。规格审查通过；质量三项修复定向复审通过，无剩余阻断。不运行全量，不推送或发布。
