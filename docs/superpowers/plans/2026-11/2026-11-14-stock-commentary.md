# 个股结构化解读实施计划

## 基线

分支codex/structured-stock-commentary，基线6203f2c，独立工作树tmp/worktrees/structured-commentary。复用主仓.venv，PYTHONPATH显式指定本树src，不改editable安装。pytest每次唯一tmp/pytest/<范围>-<PID>-<GUID>，失败产物保留。

## 步骤

1. 补充规则、业务源、模型结构与降级回归，先确认失败。
2. 新建业务资料适配器及规则模块：来源/日期/身份/8秒超时/7天缓存，五维规则和证据候选；修改AnalysisContext默认兼容字段。
3. Pipeline.run仅A股额外分析启用版本及资料采集；_generate_commentary新版本分支单次模型JSON生成，重试不采集；旧路径保留。
4. payload与Markdown共用组装结构；网页逐行渲染、安全文本、失败仍保留五维，更新资源版本。
5. 相关测试、单文件检查，真实服务与内置浏览器验收；独立规格/质量审查及修复复核。
6. 记录结果，核对完整事项暂存，diff --cached --check，中文本地提交。

## 记录

- 预览内容已确认。源码调查确认AnalysisContext无业务资料字段；同花顺两标的主营与产品接口成功，巨潮主营范围较窄，选同花顺。
- 主仓其他股票池改动不纳入本事项。

## 实施结果与审查

- 新增data/business_profile.py：同花顺主营页面8秒单请求、7天缓存，标题证券代码身份校验，保留主营/产品/来源/采集时点，失败不影响报告；不使用经营范围或过期资料补造。
- 新增report/research.py：五维规则、证据与风险候选、严格模型JSON结构/数值/引用校验、共享Markdown组装。Pipeline仅A股额外分析启用v2，重试不重新取主营。payload与会话成果持久化research_commentary，旧路径不迁移。
- 网页新增结构化纯文本渲染，沿用报告卡片、逐行标签；缓存版本链一致更新。关闭/失败/不足保留主营与规则，核心结论不伪造。Markdown不足分支与网页统一。
- 两项独立规格/质量审查发现不可用财务残留、中文数字漏检、长主营与不足状态两端不一致、会话字段遗漏、身份校验过宽；均修复并补回归，原审查者复核通过。

## 验证记录

- 首轮新增回归先因模块不存在失败。实现后发现测试缺summary与basetemp父目录，修正测试设置后7项通过。
- 相关报告/重试/模式/同行/DOM回归346项通过，1项新DOM因简化桩不支持append失败；改用现有appendChild写法，新增集成与DOM共12项复核通过。
- core/data/agent及API app/static共1269项通过；4项因资源版本断言及测试导入旧抽屉URL失败，更新版本与测试共享URL后，定向88项全部通过（含新增边界、业务源、会话透传、静态全组、API重试）。没有重跑全量。
- 修改范围Ruff通过；Pyright单文件相关范围0 errors/0 warnings。工作树复用外部.venv，工具仅提示本树没有本地.venv目录，不影响依赖解析。
- 真实路径：主仓.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765，PYTHONPATH显式使用本树src。cwd隔离tmp/acceptance/commentary；复制配置及SQLite backup缓存，关闭推送、定时采集、清理任务。系统裸命令指向用户目录旧安装，未使用。
- 内置浏览器输入000338并提交，观察真实加载与HTTP200、真实模型生成的主营/三句核心/五维/三项风险；真实主营适配器返回ok。成功结果保存在隔离reports。模型综合仍为一次调用，原有新闻标注另有独立调用，未改其契约。
- 浏览器1280px与390px实测document.scrollWidth均等于viewport；五维行全部位于内容区，截图wide.png/narrow.png保留tmp/acceptance/commentary。已恢复浏览器临时尺寸。
- 未验证全市场主营覆盖、港股新版（本次不迁移）、全部模型供应商真实生成表现；程序数值/结构校验不等于自然语言语义完全正确。
- 最终补充冒烟（入口、CLI、utils、打包、雷达UI）136项通过；其余API及检查脚本476项通过，只有依赖弃用警告。修复后的规则13项复核通过，单文件Ruff/Pyright仍通过；数据入口检查、Changed PlanOnly（UnmappedFiles=[]）和暂存diff检查通过。复用已通过范围，未触发Full或重复真实模型验收。
- 真实归档核对commentary_status=ok、research_commentary.version=2、dimensions共5行；本任务8765服务已停止。成功测试目录尝试清理，失败诊断目录保留；截图和验收日志留在tmp。
- 测试目录递归清理被自动审批拒绝，仅返回blocked by policy；该命令整体未执行，未重试清理、未修改ACL。成功和失败测试产物均保留在忽略目录，后续单独提交，不影响验证结果。
