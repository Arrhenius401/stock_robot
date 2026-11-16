# 评估留档与版本化成员草案实施计划

日期2026-10-15。基于d37eb24，执行已确认完整设计及本轮继续授权；沿用design-before-implementation技能，不重复询问已确认流程。此次完成不可变研究评估档案及版本化成员新增草案的底层/CLI，不声称成员发布、风险生命周期、后台任务或UI已经完成。未配置规则继续阻止正式入池，不新增依赖、不改ETF和全局SQLite user_version。

## 一、不可变研究档案

新增 `radar.stocks.evaluation_store.StockEvaluationStore(research:StockResearchStore)`，同SQLite独立stocks前缀表及独立feature版本1，不改变research现有schema_versions=1。复用research连接/写事务/时钟，连接必须closing。

接口：
- `capture_inputs(*,symbol:str|None=None)->dict`：保存scope all/single、按symbol排序的companies列表（每项company+evidence），公司完整资料、证据payload/来源/hash/披露及最新审核身份/决定/时间均留档；不只是保存证据ID。before/after修订保证来源稳定，不隐含通过。captured_at仅作记录，排除幂等身份。
- `save_evaluation(report:dict, inputs:dict, *, market_window:list[date]|None=None)->dict`：报告由服务器现有evaluate_store(include_styles=True)生成，CLI不接受导入passed JSON。保存前BEGIN IMMEDIATE下校验公司revision与报告source_revision；scope=all还校验完整当前证券名录，变化抛StockRevisionConflict且整批零写入。校验规范日期、唯一代码、来源JSON、当前共同/风格规则版本，formal_screening_enabled必须false、members_published=0、research_only=true，池/分支不能有eligible/ready资格。执行errors或evaluation_valid=false可留partial诊断报告，但不成为complete；partial不覆盖任何旧complete。
- 身份为规范化report+输入manifest（去captured_at）+市场window的SHA256，同输入重复保存幂等，不把created_at计入ID；已存在快照不更新原文或时间。
- `get(evaluation_id)->dict|None`：id/status/created_at/as_of/report/inputs/context及动态freshness，返回副本；freshness校验来源revision、all范围名录、规则版本和本地当前日与研究截点，旧研究日不冒充当前资格。归档内容不因新审核覆盖。
- `list_evaluations(*,symbol=None,page=1,page_size=15)->dict`：SQL分页稳定created_at DESC/id，symbol按快照实际包含过滤，正整数参数保护。返回摘要，不每页完整大payload。
- `assert_current(evaluation_id)->dict`：后续草案使用；完整、来源版本一致、规则一致才可引用；历史研究可看但不能当今日草案依据。不可用/缺失/部分失败明确错误或StockRevisionConflict。

来源manifest是服务内部数据而非外部批准输入。报告与manifest归属/版本相互校验，不能用存储接口批准正式成员；未来生产发布仍需完整规则与正式行情/日历版本，研究快照不能单独批准成员。review90天自然失效与公司资料变化均影响动态freshness；研究输入的未来审核、披露仍由引擎校验。

## 二、成员新增草案（发布仍关闭）

新增 `radar.stocks.membership_drafts.StockMembershipDraftStore(evaluations:StockEvaluationStore,pools:StockPoolRepository)`，独立stocks草案表与追加事件表。此次没有正式成员表写路径，因此只接受action=add；restore/remove/suspend不能伪造已有成员，后续生命周期模块再提供。

接口create(*,evaluation_id,changes:list[{pool_id,symbol,action:'add',reason}],request_id,actor)->dict、get(draft_id)->dict|None、list_drafts(page=1,page_size=15)->dict、cancel(draft_id,*,expected_revision,actor,reason)->dict。请求id非空且不超过200字；同id同规范内容幂等，异内容冲突。变化不得重复pool/symbol，至少一项，池须配置存在/启用；仅支持实际计算器产出该池结果的已配置股票池，新风格不能因命名自动生效。原子检查evaluation当前完整版本、公司属于该评估，再写入草案和created事件，任一项无效整批回滚。

草案revision从1开始、状态blocked；逐项保留公司/池、理由、评估id、来源revision、未通过/待配置阻碍。没有ready/eligible绕过参数。cancel使用revision CAS，追加取消人、理由、时间及事件，重复旧revision报冲突。get/list保留原草案并附动态evaluation_current，来源变化不改写旧判断，也不能发布。无publish入口，返回publication_enabled=false、members_published=0。真实两池成员保持空。

## 三、CLI/流程

新增 `stock_robot.stock_archive`：
- stock-evaluation-capture --db --as-of [--symbol] [--market-window] [--output]：先capture_inputs，再服务器evaluate_store，保存档案；输出档案摘要/ID。复用原市场窗口读入/文件保护辅助函数，所有输入输出禁止覆盖DB/WAL/SHM/journal，也不能覆盖市场window。
- stock-evaluation-list --db [--symbol --page --evaluation-id --output]。
- stock-member-draft-create --db --input --pool-dir --request-id --actor [--output]：JSON仅evaluation_id及changes，不接受passed/eligible/正式成员JSON。pool-dir缺省config/radar_stock_pools。
- stock-member-draft-list --db [--draft-id --page --pool-dir --output]。
- stock-member-draft-cancel --db --draft-id --expected-revision --actor --reason [--pool-dir]。

CLI全部是实际SQLite往返，不改网页或会员读取API。本阶段结束仍不具备完整成员发布/暂停/退出/恢复及股票更新功能。

## 四、实施与验收

存储代理实现evaluation_store.py+test_stock_evaluation_store.py；父实现草案+CLI/集成测试/映射/使用说明。测试先行、相关范围检查、规格+质量独立审查；审查修复后定向复核。重点不可变/幂等/来源原文存档、审核变更、创建/保存间race、all名录新增、部分失败不覆盖、日期/自然过期、cross-company、请求重放、draft取消CAS/原子失败、文件保护、旧接口兼容。

使用现有685家研究库做真实CLI留档两次应复用同档案；有完整运行但无已审核事实时档案complete仅代表计算完成，资格仍pending。草案合成真实库验证可保存blocked但不能发布，再更新证据验证动态失效。资料/截图/DB留tmp不提交。verify Changed Staged覆盖新模块与原共享存储/引擎/CLI相关范围，git diff --cached --check后整体本地提交；不隐式全量、不推送。测试目录唯一PID+GUID，失败保留、全部结束后尝试清理成功目录。
