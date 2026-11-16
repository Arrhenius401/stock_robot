# 共同准入计算验收记录

日期：2026-10-15。工作树：`codex/stock-radar-pools`，承接持久化提交798c21e。

## 交付范围

新增纯计算 `radar.stocks.admission.evaluate_common_admission`，只消费七类有效且显式审核的规范事实，逐项计算上市满三周年、当前及60市场交易日均值200亿元、三个完整年度及最新TTM归母与扣非归母利润为正，以及审计、治理、龙头、对应企业类型的财务专项。

新增 `stock-robot radar stock-admission-evaluate` 及SQLite服务：独立研究报告保留事实来源、规则版本和公司修订号；公司资料计算中变化则失效，单公司读取/计算失败隔离。未确认风格及机会参数仍待配置，正式筛选关闭，不写入成员或覆盖公司资格。

代码、命令、检查映射及使用说明按同一事项收敛提交。没有UI或会话改动，本阶段不重复既有真实浏览器/会话验收。

## 测试与审查

执行 `scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：Ruff通过、Pyright 0错误、PowerShell语法检查通过、pytest **237项通过（54.36秒）**，临时目录由验证脚本成功清理。检查计划无未映射文件，覆盖：

- tests/test_verify_script.py
- tests/radar/test_stock_admission.py（80项纯引擎断言）
- tests/radar/test_stock_admission_cli.py（16项真实SQLite/CLI集成）
- tests/radar/test_stock_research_store.py
- tests/test_cli.py、tests/test_cli_index.py、tests/radar/test_collector_cli.py

规格及质量双审查最终PASS。审查修复覆盖302代码、同日全股类市值相互矛盾、极端数值指数、单公司首次读取隔离、失败结果的日期/版本/证据字段。已审核事实内的clear不会覆盖数字失败；已知独立失败不因其他缺失项消失；矛盾事实本身不能选择有利值通过。质量审查还做3864次坏类型动态探针，没有未捕获异常。

## 685家公司真实研究库验收

使用现有 `tmp/stock-research-acceptance/final.db`，通过共享venv正式CLI入口执行：

```powershell
$env:PYTHONPATH="$PWD/src;$PWD"
D:/code/stock_robot/.venv/Scripts/stock-robot.exe radar stock-admission-evaluate --db tmp/stock-research-acceptance/final.db --as-of 2026-10-15 --output tmp/stock-research-acceptance/common-admission.json
```

前后数据库统计相同：685家公司、772条证据、0条审核、3个导入批次。研究报告：total685、common_passed0、common_failed0、pending_verification685、errors空、eligible_members0、members_published0、formal_screening_enabled=false。公司尚未完成规范事实审核，所以不能把候选提示当合格成员；此结果不是“市场没有公司达标”的统计。

报告及数据库留在忽略的tmp目录，不提交原始资料。实际筛选通过比例、完整批量采集耗时、内存峰值、正式风格/机会引擎、发布及成员生命周期、完整管理UI均未由本阶段验证。研究用日期文件不替代未来发布所需的生产交易日历和行情版本；当前审核状态不构成历史审核回测。
