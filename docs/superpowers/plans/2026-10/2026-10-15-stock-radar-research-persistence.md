# 股票共享候选与证据审核持久化实施计划

日期2026-10-15。用户明确选择完整股票池并授权继续；按已确认共享候选、证据审核、90天复核及版本冲突设计实施，不重新确认技术细节。本阶段具有独立价值：研究资料可以进入可持续审核的业务记录，而不是继续堆离线JSON。未实现的成员发布、准入计算和生命周期服务不冒充完成。

## 1. 事实、边界与存储

沿用独立工作树codex/stock-radar-pools和主仓库共享.venv。工作树无.codegraph，直接读当前文件；主索引已经CodeGraph调查。现有RadarStore和CollectorStore共用本地SQLite，连接使用contextlib.closing，stocks表不能改ETF表或PRAGMA user_version。新增stocks_schema_versions、stocks_source_batches、stocks_companies、stocks_evidence、stocks_reviews，研究来源、证据与审核不可变长期保留。

所有资料导入保持pending_verification；输入里financial_verified或status不能赋予审核权。候选共同准入与风格仍未知，导入和证据确认均不产生成员。数值规则未配置、ETF配置和空股票池API保持当前状态。本阶段不加UI/API写路由、后台任务或新依赖。

公司symbol共享且唯一，多个批次可补充同一公司资料。批次ID绑定导入种类、数据截点及输入SHA；相同批次重跑幂等，不增加公司revision或审核。旧截点可留档，但不覆盖更新截点的展示资料，不影响已有审核。证据ID绑定公司、证据种类、原文追踪及内容，来源／字段变化产生新ID，不能把旧审核转移到新证据。导入原始批次和每条payload保存，不把供应商提示当事实。对输入非有限数字转为文字线索，最终JSON不含NaN。

SQLite每操作独立连接、WAL、foreign_keys=ON、busy_timeout5秒；写事务BEGIN IMMEDIATE。schema版本使用本模块专用表，可重复初始化；未知较新版本拒绝。空db合法，同库ETF表内容必须保持。每次涉及公司资料或新证据的成功写入递增公司revision一次；读／完全重复导入不递增。

## 2. 模块接口

新增 `src/radar/stocks/research_store.py` 和 `tests/radar/test_stock_research_store.py`，由实现代理完成，主会话写CLI和映射。

- `StockResearchStore(db_path:Path, *, clock:Callable[[],datetime]|None=None)`，clock默认UTC aware，注入naive应拒绝。
- `import_companies(records:list[dict[str,Any]], *, source_sha256:str, source_label:str, data_as_of:date)->dict`。任意候选列表，精确沪深A股symbol，name/current_name可缺用symbol；完整原始行作为kind=research_hint证据，默认不可确认。一次批次原子写入，坏公司/重复symbol整批拒绝，不能部分写入。返回batch_id/companies_added/evidence_added/reused。
- `import_review_packet(packet:dict, *, source_sha256:str, source_label:str, data_as_of:date)->dict`。companies列表；导入每公司proposals、financial_proposals、documents、financial_documents和未解决事项为独立证据。不信任packet审核标志或proposal_id；取fields和origin内容并保存完整行。原始文档失败也作为线索保留。规范evidence字段只能作为research_hint，不能直接confirmed。原文追踪从origin取source_url/disclosed_on/file_sha256；无有效原文hash或日期不补猜。
- `add_evidence(symbol:str, *, kind:str, payload:dict[str,Any], source_url:str, disclosed_on:date, content_sha256:str, expected_revision:int)->dict`，允许用户显式补充原文证据（不限于财务，kind非空）。来源必须HTTPS/无凭据/非localhost或IP，披露日及data_as_of不得晚于当前北京时间日期。hash64位小写；当前行保持pending。无公司拒绝，过期revision拒绝，重复同证据不增加revision。
- `save_review(symbol:str,evidence_id:str,*,expected_revision:int,decision:str,reviewer:str,reason:str)->dict`，decision只允许confirmed/rejected/needs_evidence。确认需证据具有有效HTTPS来源、64位原文hash及有效已披露日期，research_hint和失败documents不能靠输入标志确认；导入的实施／财务建议具有完整原文追踪时可由显式审核确认，但这不确认普通股或整家公司资格。reason/reviewer非空，审核日由服务clock产生而非输入冒充。审核记录只追加并绑定证据ID与内容，原文变化后不复用。审核更改递增公司revision，旧revision抛StockRevisionConflict。
- `get_company(symbol:str)->dict|None`，包含symbol/name/revision/data_as_of，admission_status始终pending_verification。
- `list_companies(*,search:str='',page:int=1,page_size:int=15)->dict`，查询全候选名／代码，SQL转义%/_，稳定按symbol排序，返回items/total/page/page_size。非法页码拒绝，页面大小不添加任意业务上限。
- `list_evidence(symbol:str)->list[dict]`，返回原文payload、来源、当前最新审核和review_status；confirmed自reviewed_at起90天内有效，超过则expired；未来审核不生效。rejected/needs_evidence明确区分，不删除旧review。
- `review_history(symbol:str)->list[dict]`按追加顺序，`summary()->dict`公司/证据/审核总数。可提供内部connect/helper，不外部暴露手工修改事实通过准入的接口。

领域异常：StockRevisionConflict(ValueError)和StockResearchError(ValueError)（如需要），便于后续API映射409/422。结果均JSON安全；日期时间为ISO带时区。记录不因旧批次/源失败恢复成员；本阶段没有成员写入口。

## 3. CLI与验收

主会话新增stock_robot.stock_research CLI：stock-records-import(--kind candidates|review_packet,--input,--db,--as-of)、stock-records-list(--db,--search,--page,--symbol可选公司详情及审核历史,--output可选)、stock-evidence-add(--input,--db,--expected-revision)、stock-evidence-review(--db,--symbol,--evidence-id,--expected-revision,--decision,--reviewer,--reason)，注册radar分组。db显式指定；保护输入／输出不覆盖db和-wal/-shm。import读取候选companies或原始列表、review包；原始输入SHA和路径留档。CLI不提供直接资格通过或成员发布。

测试先行：同批幂等、同股多批、旧批不覆盖新资料、跨公司证据拒绝、无来源不可确认、输入自称verified仍待审、审核不变资格、90天边界、更新后旧审核不迁移、两连接revision竞争一胜一冲突、整个导入原子回滚、重开库持久化、ETF表共存及schema升级保护。CLI测试文件tests/radar/test_stock_research_cli.py；新增源文件在verify精确映射，避免默认全量。

实际验收从现有真实候选缓存及审核资料包导入独立tmp db，核对真实公司/证据数量、重复导入不增加记录、不自动新增成员。审核生命周期用明确标识的合成测试证据独立tmp db演示，不替用户批准实际股票证据。独立规格+质量双审查，范围Ruff/Pyright/pytest和git暂存检查后，本阶段源码／测试／正式文档合为本地提交，不推送／合并，不重复无关浏览器验收。

<a id="historical-2026-10-15-stock-radar-research-persistence-validation"></a>

## 股票共享候选与证据审核持久化验收

> 2026-11-24 文档整理：本节合并自 `2026-10-15-stock-radar-research-persistence-validation.md`，保留原历史记录；以下结果仅适用于所述日期和代码状态，不代表当前验收结果。

日期2026-10-15。对应实施计划 `2026-10-15-stock-radar-research-persistence.md`。用户选择完整股票池并授权持续推进；本阶段把已有离线研究资料接入持久化共享候选／证据审核，不代表完整股票池交付。

### 完成范围

新增StockResearchStore和四个radar CLI：stock-records-import、stock-records-list、stock-evidence-add、stock-evidence-review。stocks_专表保存公司、来源批次、不可变证据和追加审核；独立schema版本、不变更ETF表或PRAGMA user_version；WAL、5秒锁超时、BEGIN IMMEDIATE及公司revision CAS。导入整批原子、同批幂等、旧截点不覆盖新资料；缺名称补证保留已知名称，当前名称优先。

来源／字段变化产生新证据，旧审核不转移；资料自带financial_verified/status不具批准权。明确跨公司归属整批拒绝，空建议和失败文档不能确认。确认需完整来源、日期、摘要及显式审核人／理由；满90天过期，未来审核不生效。公司准入仍pending_verification，未添加成员发布入口或自动任务。CLI输入／输出保护db及其WAL／SHM／journal（含真实路径）。

### 真实验收

从已有market-candidates.json导入685家公司。首次真实导入因302132中航成飞被旧前缀拒绝，整批无资料写入。随后从[深交所披露公告](https://disc.static.szse.cn/disc/disk03/finalpage/2025-04-29/e7ca3d43-4a5a-4c76-bb42-ad4f95dddb40.PDF)确认有效代码并修复校验，新增回归。这只确认身份，不代表符合准入。

最终新建独立tmp/stock-research-acceptance/final.db，经真实项目CLI导入685候选、三公司分红审核包、长江年报审核包，结果公司685、证据772、来源批次3、审核0；全体admission_status=pending_verification。未替用户确认实际股票事实。旧验收库重复相同候选及分红资料包，新增公司／证据均0、reused=true。全库搜索长江返回2家公司，600900详情含47条证据，JSON导出成功。数据和原始库都留忽略tmp，不提交。

StockPoolRepository检查红利价值池及成长趋势池items均为空；研究资料不是成员。合成证据审核、stale revision、90天和并发竞争由真实SQLite回归覆盖，不在实际股票研究库伪造批准记录。

### 审查和检查

独立规格及质量审查复核通过；修复跨公司entry/origin/fields归属检查、空建议确认、大整数分页、连接PRAGMA初始化失败关闭等边界。最后名称保留缺陷由独立合成数据复现（新包仅代码曾将长江电力名称替换为600900），主会话局部修复并补两类导入／当前名三回归。

范围门禁命令 `.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：Ruff通过、Pyright 0 errors／0 warnings／0 informations，149个相关用例通过（29.32秒）。名称修复后仅复核受影响store/CLI，48个用例通过（2.57秒），源及测试Ruff、共享运行时Pyright通过；其余同状态104项复用，最终相关回归合计152项通过。`git diff --cached --check`通过。不运行全量、不重复无关浏览器验收。

先前检查异常已区分：新增CLI导入排版I001经Ruff修复；首次类型检查暴露可空get_company和故意异常JSON测试推断过窄，使用断言helper和诚实Any标注修复；真实302代码失败未发生部分写入。失败诊断留tmp；成功唯一测试目录按规范清理。无前端／SSE变更，无新运行依赖。

### 后续边界

股票准入和风格／机会计算、全市场正式发现服务、成员审核发布、暂停／退出／恢复、任务及正式管理页面仍需接入。已确认数值与未确认数值继续区分，正式筛选保持关闭。个人逐条确认事实不替代确定性硬条件，不产生可入池结论。本阶段数据可供接下来的引擎与管理接口使用。
