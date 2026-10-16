# 第一阶段：官方股本变动原文与双日期研究记录

## 授权与范围

用户要求持续完成完整股票池且不再逐步确认；继续已批准数据接通，沿用design-before-implementation。第一阶段子事项，不新增交付阶段。公开原文可采集，不新增投资阈值、不推断单位、不发布成员。S级跨API实现及独立规格/质量双审。

## 已验证事实

2026-10-15公开POST https://webapi.cninfo.com.cn/api/stock/p_stock2215，params scode/sdate/edate；两个完整15秒deadline探针600900返回69条、601318返回52条。raw及metadata仅tmp/stock-capital-source-probe。SDK stock_share_change_cninfo字段映射：DECLAREDATE公告日，VARYDATE变动日，F003N总股本，F021N已流通股份，F022N为其中人民币普通股；F028N流通受限，另有受限B/H股。F022N不能代表全部A股。2007-03-01平安流通A/H之和不足总股本；长电有GDR上市，不能推断另加GDR或底层股数。SDK和响应均无已核实单位，保持unit=None/unit_verified=false/all_share_classes_verified=false。

SDK公开动态头仅安装包cninfo.js本地getResCode1纯时间编码，无账户/验证码/Cookie。仅执行可信安装常量，V8 eval/call各2秒、64MiB，生产完整POST含头生成/响应读取由bounded_provider_call总15秒。遇401/403/429或权限/验证码挑战停止来源，不换身份绕过，不留动态头。

## 模块契约

新增radar.stocks.capital_history，capital_url(symbol)固定官方HTTPS URL；parse_capital_history(raw,symbol,*,as_of,start_date)->dict。严格原始JSON（拒非有限数、重复键），校验resultcode200、records列表、count/total与实际记录数量；无法证明完整时complete=false。保留全部source_fields，不改写数值，不乘10000。合法六位symbol须逐行完全匹配，两个日期严格合法；只在公告和变动均在截点内且变动>=start_date时进入rows，其余excluded_rows有原因。重复相同双日期记录全部排除且不认完整，冲突不择优。同一变动日不同公告的修订保留并提示，不选latest数值、不合成股数序列。原文错误包/挑战拒绝。rows只给announced_on/effective_on/source_fields，unit=None及上述false标志，capitalization_fact绝不生成。完整仅指请求范围原文完整解析，不保证全历史无漏报或所有股类。

collect_capital_history(companies,cache_dir,*,as_of,start_date=date(1990,1,1),refresh=False,http_post=None,sleep=...)->dict。预校验全部symbol/重复/date，不晚于北京时间当前日。当前/历史请求都保留双日期过滤；历史导出声明retrospective=true、point_in_time_verified=false，不冒充当时原文快照。默认历史起点1990-01-01只是请求范围，不是已验证上市日期。原文4MiB、行数20000、串行1秒、timeout15、网络/5xx最多重试2次（沿用功能设计第6节，重试退避3秒、10秒）、429/401/403/挑战停止后续同源网络；缓存可用不受停止影响。非瞬态格式/权限不重试。每公司失败隔离。

复用已安装依赖，不引入新包。预算内HTTP200原文按sha256 .jsonbody先归档；manifest版本/symbol/start/asof/URL键，命中验实际限长/摘要/aware fetched_at不未来、重跑parser，不修改采集时间；refresh失败不得回旧成功。原子写、拒缓存root/祖先/子文件符号链接；1GiB只提醒不删除，统计失败不影响采集。保存失败清单和raw hash；不自动写研究DB或生成可确认市值事实。

CLI stock-capital-history --input companies.json --as-of --start-date(默认1990-01-01) --cache-dir --output [--refresh]；输入和输出不能互相覆盖/位于cache内，未来/倒序日期在任何采集写入前拒绝。导出input_source摘要，research_only=true/financial_verified=false/formal_screening_enabled=false/members_published=0。

## 分工与验证

实现代理仅capital_history.py和test_stock_capital_history.py；父CLI/CLI测试/注册/verify映射/文档/真实验收。资源tests/resources/radar/cninfo-600900-capital-history.json与cninfo-601318-capital-history.json，真实响应各裁3条（最新、最早或上市及GDR/受限），保留真实total/count，默认不能声称全量完整；正向测试明确合成裁切总数。采样2026-10-15，原始数字JSON精度按原文保留，完整响应只tmp。

测试先行覆盖双日期未来/公告晚于变动、单位未知/受限股/GDR不推算、错误包/身份/非有限/重复键/重复冲突/截断数量；HTTP总deadline/close/重试停止/预算、缓存hash/时间/符号链接/失败刷新、历史非时点证明；CLI路径/输入先拒、无DB改写、注册。真实两公司下载及缓存复用，不自动审核/入池。相关verify门禁通过才完整事项本地提交，不全量。

## 实施记录

首轮CLI红态因新域模块尚未实现，ModuleNotFoundError radar.stocks.capital_history；测试产物保留。静态检查同样记录缺模块及导入排序，后续模块落地后再复核。

原始600900响应SHA256：d8283af04af279507fcc4dee629cb739606a890ed9a52515a090d633d09f0fe7；字节数49834。

原始601318响应SHA256：47f9ae9871ff819360baa2bcab2db9cb61b7df407529432259eab5c72830bb20；字节数38063。

数值保真：source_fields保留源JSON数字类型，另附source_numeric_tokens保存源数字token字符串，完整原文bytes按SHA归档；不依浮点转写承担精度审核。规格预审确认count/total如同时存在应分别等过滤前原始records数量。公开文档目录已确认p_stock2215与增量接口，但目录无单位或更正语义，未核实状态不变。

收尾修复：顶层status原先仅按下载/缓存成功判断，现同时要求每家公司解析complete=true；count/total不一致即使HTTP200也只能是partial。已增加回归。真实CLI采集600900 69条、601318 52条，第二次均命中缓存且摘要/采集时间稳定；单位和全部股类覆盖仍为false。最终暂存范围门禁：`.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`，Ruff通过、Pyright 0错误、pytest 189 passed（62.90秒），测试临时目录已清理。
