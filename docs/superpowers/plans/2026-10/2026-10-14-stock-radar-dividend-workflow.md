# 分红批量工作流及审核资料包实施计划

日期：2026-10-14。承接已确认共享资料、可审计证据与通用脚本计划。用户要求持续推进，不逐小部分请求确认；本阶段只串联现有研究工具并整理核验资料，不启用数值准入或发布成员。

## 设计与执行范围

新增 `radar dividend-workflow --input 公司JSON --workspace 独立目录 --as-of YYYY-MM-DD [--evidence 已规范证据JSON] [--refresh]`。

一次命令执行官方发现、原文留档、字段提取、规范证据准备和支付率研究，保存独立阶段产物与最终摘要。复用现有采集器的限流、超时、重试、缓存软上限和429停止策略，无新依赖或后台调度。输入、可选证据文件必须位于工作目录外；汇总不能覆盖输入。工作目录使用明确版本及截止日、公司输入绑定，已有不同输入不能静默覆盖，应换目录。证据可更新并记录本次SHA，刷新显式重取官方数据。

始终执行发现与留档的原有缓存核验，失败阶段不被历史成功摘要掩盖。提取阶段可以复用绑定归档元数据、文件校验和及解析版本的完整结果；损坏、丢失或版本变化重新提取。每步原子写入，异常保留已完成产物和失败检查点。没有数据的公司继续进入资料包和研究，不能从统计中消失。

新增纯函数 `build_review_packet(companies, extraction, prepared, research, as_of)`，逐公司输出三个完整年度的核验清单、全部建议及稳定ID、原文SHA／摘录／公告日、现有规范证据和研究缺失原因。proposal ID按公司和完整建议内容确定，不依赖输入顺序；原文来源和SHA随建议保留。建议永不自动写入events/profits/coverage；既有规范证据只通过prepare_candidates明确合并。完整查询不替代完整年度证明，matched_template不代表审核通过。支持无分红、A/H缺失、无法解析原文和单公司失败，未知不填零。

最终摘要区分查询范围完成、下载与失败、模板匹配与待核验、研究完整与缺失。输出 `rule_enabled=false` 和明确research_only，不声称全市场筛选已完成。工作目录外原输入与PDF原文不会被改写。

## 任务、边界与验证

1. 主会话实现审核资料包模块及测试，覆盖建议稳定ID、重复去重、不同公司／股类、孤立建议拒绝、缺失研究、原文追踪、不填零和不改变输入。
2. 独立代理实现工作流模块与测试；现有CLI入口和verify映射由主会话集成。代理不得改变现有金融计算／发现／留档／提取模块。断点摘要失败、输入／目录冲突、缓存损坏、截点变化、可选证据变更为主要回归。
3. 整阶段独立规格与质量审查，修复后复核。使用已有三家公司原始缓存及PDF完成真实工作流，重复运行验证续跑，显式检查两股票池没有新增成员。
4. 按改动范围执行Ruff、Pyright、pytest及暂存差异检查；无UI修改，不重复浏览器验收。源代码、测试和正式说明一起本地提交，不提交缓存、原文或截图。

## 接口约定

审核包：`build_review_packet(companies: list[dict[str, Any]], extraction: dict[str, Any], prepared: dict[str, Any], research: dict[str, Any], as_of: date) -> dict[str, Any]`，位于 `radar.stocks.dividend_review`。顶层companies列表，每行symbol/name/years/proposals/evidence/research_status/unresolved；顶层requires_review=true、rule_enabled=false。建议ID使用canonical JSON SHA256，包含symbol及proposal内容；提取项file_sha256/text_sha256独立引用，不把平铺列表当唯一来源。

工作流函数：`run_workflow(input_path: Path, workspace: Path, as_of: date, evidence_path: Path | None = None, refresh: bool = False) -> dict[str, Any]`，位于 `stock_robot.dividend_workflow`。CLI定义同模块；复用 `_read`、`_write_json`、`prepare_candidates`、`run_batch`、`discover_announcements`、`collect_documents`、`extract_archived_documents`，最终调用审核包函数。模块常量版本纳入提取缓存依赖。主会话随后注册CLI。

补充验收边界：审核建议必须具有有效的原文和文本SHA；缺失、错误状态或非有限追踪字段逐项隔离，其他公司的资料仍保留。证据字段列表类型在任何采集和工作目录写入前校验。工作流摘要替换遇到PermissionError仅局部最多重试两次，等待50ms、100ms；持续失败记录错误，不修改ACL或绕过权限。
