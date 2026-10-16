# 股票池正式成员生命周期

用户已确认完整股票池持续实施。本事项实现阶段三的成员持久化与发布边界，不改变尚未确认的估值、股息率、趋势等数值规则，不发布任何真实股票成员。

## 目标与边界

新增独立 `StockMembershipStore`，复用 `StockResearchStore` 的SQLite连接、事务、公司版本和评估档案；不改ETF表、不使用YAML保存成员。研究候选、评估档案、草案和正式成员仍为不同层次。

正式成员只有 `active`、`suspended`、`exited` 三种当前状态。事件是追加式记录：`publish_add`、`suspend`、`exit`、`restore`。成员可同时属于两个池，各池独立；共同准入失效的后续任务可对两池分别提交暂停或退出，不能用价格变动强制退出另一个池。

每个池有单调递增revision。批量变更需给出每池预期revision；同事务内验证、写成员、写事件并递增池revision，任一项失败则整个批次不写入。相同request_id与完整相同内容幂等返回；同request_id不同内容冲突。理由、操作者和事件时间强制保留。查询默认每页15项，行业仅由已确认成员事实决定，未知行业不猜测。

## 发布规则

`add`、`restore` 必须引用 `StockEvaluationStore.assert_current` 的不可变评估，且指定公司仍保持相同source_revision；共同准入和相应池已批准条件都为passed，未配置规则为空，正式筛选显式开启。现阶段评估层固定关闭正式筛选，因此发布会明确拒绝，绝不因实现生命周期而放行。

`suspend` 只允许当前active成员；`exit`允许active或suspended成员；两者只记录用户审核后的理由，不以它们绕过新增门槛。`restore`仅允许exited或suspended成员，重新走当前评估门槛；旧评估、旧公司revision或过期证据不可恢复成员。已经active/suspended/exited的重复状态变更拒绝，不静默覆盖。退出成员保留历史，后续旧评估不能写回active。

将现有 `StockMembershipDraftStore` 保持为“关注草案”且publication_enabled=false，避免隐式把已存草案转换为成员。

## 接口、CLI与验收

新增 `radar.stocks.membership_store`，公开 `publish`、`get_member`、`list_members`、`list_events`；CLI新增 `stock-membership-apply` 与 `stock-membership-list`。apply输入只接受 `evaluation_id`（仅新增/恢复需要）和变更列表；不接受外部passed字段。CLI拒绝输出覆盖输入、数据库或池配置目录。

测试覆盖：空池、双池同股、原子批次、幂等与request冲突、CAS冲突、草案不升级、旧评估/资料revision拒绝、未配置规则与formal关闭拒绝、暂停/退出/恢复顺序、事件不可修改、分页及SQLite重启。新增文件映射到验证脚本。先运行相关测试、Ruff、Pyright；最后`verify.ps1 -Scope Changed -Source Staged`。真实验收只验证CLI拒绝当前不具备正式资格的候选并保证数据库未产生成员；不选取或发布真实标的。

## 实施记录

新增成员、池revision、事件与请求幂等四张独立stocks前缀表；草案表保持原样。第一次来源保留回归发现更新路径只读取成员revision，却尝试读取原评估来源字段，导致暂停/退出失败；已改为同次查询三个字段并增加来源保留断言。关联测试、Ruff与Pyright通过；最终暂存范围门禁另记。

最终暂存范围门禁：`.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`，Ruff通过、Pyright 0错误、pytest 264 passed（57.50秒），测试临时目录已清理。此前门禁首次运行的3项失败仅为验证映射预期未包含新增成员测试；更新映射断言后先行3项通过，再完成最终门禁。
