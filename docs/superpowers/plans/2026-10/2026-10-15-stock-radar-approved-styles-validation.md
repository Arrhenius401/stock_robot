# 两池已确认风格条件验收记录

日期：2026-10-15。基于共同准入提交10ad057，复用现有独立工作树和共享venv，无新增依赖、无UI/会话改动。

## 实际交付

新增 `radar.stocks.style.evaluate_stock_styles` 和 `stock-robot radar stock-style-evaluate`。来源校验复用共同引擎；规范事实分组支持指定前缀，原调用默认行为不变。

- 普通红利价值：三年经营现金流/同期间共同事实归母利润>=0.8，另现金专项核验；金融采用匹配类型的专项事实。
- 成长：比率低于0.8仅attention，不直接淘汰；专项未完成继续待核验。
- 三个年度实际实施分红仅约束红利分支；价值分支独立。金额无须逐年增加，零与未知区分。
- 60市场交易日日均原始成交额1亿元只作红利提醒/成长机会参考，不改变共同资格。
- 资料revision变化时共同结果、两个池、各分支及checks全部失效；单公司失败返回两池显式待核验。原共同准入命令不新增池报告，保持原用途。

正式筛选保持false，成员发布始终0。未确认支付率、股息率、估值、成长与趋势规则保持待配置；已确认条件passed不等于完整风格合格。

## 检查与审查

先写CLI集成用例并确认5失败1通过（新命令及include_styles尚未实现），再接服务。最后执行：

```powershell
.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv
```

Ruff通过、Pyright 0错误、PowerShell语法检查通过，pytest **297项通过（43.82秒）**。映射无未覆盖文件，范围含53项纯风格、6项风格SQLite/CLI测试，原共同准入80项、共同CLI16项、研究存储、检查脚本及CLI相关测试。首次改造后原96项共同引擎/CLI测试也通过；同状态无需重复全量。

规格及质量审查均PASS。质量审查额外执行7200次各层坏类型、非有限数、极指数变异，无未捕获异常。测试覆盖精确0.8/1亿元上下、现金流与利润符号区分、来源冲突/过期/未审核、金融分支、分红下降与零/缺失、不同财政年度、缺失及重复市场日、人工clear不覆盖数值失败，以及所有嵌套结果的修订竞争失效。

## 实际研究库验收

通过共享venv正式CLI运行 `radar stock-style-evaluate --db tmp/stock-research-acceptance/final.db --as-of 2026-10-15 --output tmp/stock-research-acceptance/approved-styles.json`，PYTHONPATH明确指向当前工作树。

结果：685家公司、两个池各685项pending_verification、errors空、eligible_members0、members_published0、formal_screening_enabled=false。数据库前后均685公司、772证据、0审核、3批次，没有写入审核或成员。该结果源于尚无已审核的规范事实，不代表市场达标公司数为0。

研究报告/数据库/失败诊断留忽略目录tmp，不提交。当前仍未完成完整风格/机会规则、不可变评估存储、成员发布及生命周期、后台任务及完整管理页面；无本阶段UI变更故不重复前阶段浏览器验收。真实全市场风格通过数量、完整采集耗时及内存峰值未验证。本地提交不含推送、合并或发布。
