# 第一阶段：官方沪深名录与上市日线索

用户授权持续实现无需确认，沿用design-before-implementation；名录是候选来源，不是成员、龙头或经营质量证据，不新增投资门槛。

## 探针事实

2026-10-15 SSE https://query.sse.com.cn/sseQuery/commonQuery.do，sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L、STOCK_TYPE1主板、8科创，其他params参照tmp/stock-security-source-probe/manifest.json及已安装stock_info_sh_name_code。主板200/1946691字节/1702条；科创200/715290字节/618条。pageHelp.total为实际总数，metadata.pageSize2000/pageSizeWithOutLimit10000；首版只在一次result覆盖total时认定源完整。科创含689009 CDR，AkShare官方文档说明其不属普通A股，需明确排除；其余未知代码不得静默排除后声称完整。

SZSE https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1110&TABKEY=tab1，200/545393字节/2906条。工作表A股列表，实际22列，dimension却A1；openpyxl只读后reset_dimensions已验证。zip9项/展开4154409字节。openpyxl>=3.0.3已是AkShare核心依赖，不新增组件。

SSE LIST_DATE可供A股上市日；CSRC_CODE仅行业大类，J不足以区分金融类型。SZSE仅A/B股文本，无H类与明确业务截点/倍率，不将A股总数当公司全股类。issuer_type默认None；采集时间不冒充资料业务日。原始表仅tmp。

## 模块契约

新增radar.stocks.official_roster：parse_sse_roster(raw,board)->dict，board为sse_main/sse_star；parse_szse_roster(raw)->dict；collect_stock_roster(cache_dir,*,as_of,refresh=False,http_get=None,sleep=...)->dict。

parser返回rows(symbol/name/a_listed_on或None/industry_source_code/industry_source_label/source_fields原行)、declared_total/row_count/scope_exclusions/diagnostics/complete。SSE核sqlId/stock_type/list_board/市场代码、错误包、total与实际唯一数；总数不齐或未知身份则partial，可保留合法线索，绝不猜分页完整。重复code全部排除并诊断；日期未知保留None。只有明确689009按CDR范围排除，其余未知代码导致不完整。

SZSE先zip预算：压缩4MiB、展开20MiB、64entries，拒绝加密/异常路径；只读A股列表并reset_dimensions，唯一必需header，最多64列/20000行，公式不能当事实。代码须六位字符串，不猜补零；日期严格已知格式。股数原文本保留不换算、不算市值。无源total，仅说本次下载表完整解析，不保证历史成员。

合并三个必需source：SSE主板/科创/SZSE。跨源冲突不择优，universe_complete仅全部源完整且无未知身份/冲突时true。companies可直接stock-records-import --kind candidates，admission_status=pending_verification；research_hints含原行/上市日/原行业/URL/hash/fetched_at。可附security_proposal，exchange/security_type ordinary_a/a_listed_on/issuer_typeNone，disclosed_on真实采集北京日，默认仅导出不写DB或审核。formalfalse/members_published0。原行业不是申万两级或龙头事实。

## 请求与缓存

当前接口不是历史成分，仅当前北京日允许网络；历史as_of只复用当日抓取缓存，缺缓存partial，不取今天名录假装历史。生产GET+stream已有bounded_provider_call15秒总deadline，4MiB上限、必close、禁redirect；网络/5xx重试2次，非瞬态错误不重试，429按host停止（SSE主板429跳科创但可继续SZSE）。串行间隔1秒，不超并发2。生产解析也15秒子进程，zip预算如上。

内容寻址blob+版本/source/asof manifest；命中实读限长、摘要、URL、抓取日有效，重跑当前parser；刷新保留旧blob，失败不回用旧成功。root/文件拒符号链接，坏cache/IO逐源隔离，1GiB软提醒不删除且统计异常不推翻结果。原子保存。不重构已完成行情模块。

新增stock_robot.stock_official_roster命令stock-universe-discover --as-of --cache-dir --output [--refresh]，无DB参数仅导出，输出不可位于cache内。父CLI/测试/映射/文档/真实验证；实现代理仅official_roster.py及测试。

## 验证

S级先测试、双审与复审。资源sse-600900-601318-listing.json/sse-688001-listing.json裁真实result、保留真实total所以默认partial，正向完整测试显式合成total；去pageHelp.data重复全表。szse-000001-000333-listing.xlsx裁实际两行22列，包装重现dimensionA1；采样2026-10-15，完整响应/metadata只tmp/stock-security-source-probe，不加README。

覆盖身份/total/重复/未知日期/CDR/历史不得network/429跨source/预算close/坏cache/zip预算/dimension/公式/股数不推算/行业不当龙头；CLI与现有候选导入真实SQLite互通，未审不放行。真实三源采集与cache复用，按变更verify不全量，完整事项本地提交不推送。

原始响应SHA256：sse-a-list.txt=64ce2e2ce67428ce2fe816e82ac2316749b5b1213e982c5f512f95083df4d32d；sse-star-list.txt=12e1f96294b990b4182eb4faa053115c45405ec2de0e8ba1fd3d692ecc2744ff；szse-a-list.xlsx=65c456e1703ce62985027820cb65e93abc12d9728ff46f3240043d65196b7a8a。原型plan写入第一次审批处理超时CreateProcess未启动，按工具许可重试一次后成功，并非安全拒绝。CLI先红ImportError因模块尚未实现，保留红态诊断目录。

## 实施、审查与真实验收

双审关闭：真实单元格坐标检查稀疏第66列/20002行，防max_col/max_row静默截断；Excel datetime先从原cell解析上市日，ISO原值另保留；坏行也参与原始合法code重复计数，不择优；CDR排除前所有dict严格JSON校验，含真正原文字节1e999浮点溢出；SSE actionErrors/fieldErrors即使result足数仍拒完整。顶层status与universe_complete同一变量，financial_verified=false。

CLI未来日期初测未在调用域函数前拒绝（_cutoff只负责解析），父补北京时间未来日期预检；CLI6项通过。实现53项后追加overflow与请求头回归，最终相关门禁另记。一个Windows符号链接权限测试跳过，代码路径仍有拒绝和其他回归。

第一次正式三源：SSE主板1702、科创617（另CDR1）成功，SZSE403；整批partial/False，候选2319，没有冒充全市场完成。原因所有source误共用SSE Referer，已按已安装各SDK真实契约拆分请求头：SSE使用其公开名录Referer+UA，SZSE默认headers={}，生产与fake路径统一，回归覆盖。修后无需重抓已成功SSE缓存，SZSE200/2906。

正式CLI最终status=complete/universe_complete=true，companies5225；上市日600900=2003-11-18、601318=2007-03-01、000333=2013-09-18；issuer_type均None。第二次三source全部cached，hash/fetched_at不变并重新解析。raw原文、导出JSON和cache只tmp/stock-official-roster-cache及tmp/stock-research-acceptance。

显式stock-records-import --kind candidates实库往返：新增4540家公司/5225条候选线索；研究库companies5225、evidence6027、reviews0、batches5；members_published0。示例600900 revision6、601318 revision2、000333 revision3、688001 revision1，均无已确认事实；旧候选保留并附新原文，不自动审查或写入正式成员。该5225是数据来源候选范围，不是股票池正式成员数。

源中未来DELIST_DATE及ST/状态代码等原字段完整保留，未把名录普通A候选解释为经营或上市状态风险已核实。业务日期未知不伪造；全股类历史股本与公司市值仍独立待接通。实际SZSE源缺默认样式触发openpyxl已知UserWarning，仅读取值不影响结果；没有修改依赖或隐藏异常。

最终暂存范围门禁：.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv，Ruff通过、Pyright 0错误、pytest 233 passed / 1 skipped（45.86秒）；跳过项为Windows符号链接权限，成功测试临时目录已清理。双审最终均通过。
