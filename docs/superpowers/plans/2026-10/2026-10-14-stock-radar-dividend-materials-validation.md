# 分红资料准备与原文留档阶段验收

日期：2026-10-14。实施工作树 `C:/Users/25618/.codex/worktrees/stock-radar-pools/stock_robot`，分支codex/stock-radar-pools。承接通用评估阶段提交65673b2，按用户要求作为独立阶段本地提交，不推送、合并或发布。

## 实际结果

- `dividend-prepare`将任意旧候选缓存、可选风格缓存与显式标准化证据按symbol组合，原始输入路径／SHA256可追溯。供应商利润及每股分红字段只保留线索，不猜实际金额、普通股口径、股类或事件完整性。
- `dividend-archive`按明确官方URL清单下载PDF，最多两个来源并发，同hostname串行处理429；15秒连接／读取超时，下载累计时长可能更长；网络与5xx共享最多2次重试预算。来源／跳转验证、PDF头检查、原子文件替换、逐项checkpoint、SHA缓存续跑及1GiB软上限提醒均实现。
- 失败隔离、刷新失败保留旧原文、输入／输出与归档目录分离，financial_verified与rule_enabled恒false。没有成员库写入或后台自动任务。

## 真实验收

共享.venv及工作树PYTHONPATH下执行以下真实命令：

1. `radar dividend-prepare --candidates D:/code/stock_robot/tmp/stock-radar-market-20261013/market-candidates.json --style-cache D:/code/stock_robot/tmp/stock-radar-market-20261013/style-candidates.json --evidence tests/resources/radar/stock-dividend-evidence.json --output tmp/dividend-materials/prepared.json`：685家公司，保留2580条分红事件线索。
2. `radar dividend-research --input tmp/dividend-materials/prepared.json --output tmp/dividend-materials/research.json --as-of 2026-10-14`：完整1、待核验684。只显式长江样本提供实际金额证据；不是685家公司支付率通过率。
3. `radar dividend-archive --input tests/resources/radar/stock-dividend-documents.json --archive-dir tmp/dividend-materials/official-archive --output tmp/dividend-materials/archive-result.json`：三份长江电力2023—2025实施公告全部downloaded，字节数739950、218729、150126，记录原始／最终URL、SHA256和抓取时间。
4. 同一命令再次运行输出archive-resumed.json：三份均cached，SHA长度和final_url断言通过。输出financial_verified=false，未自动将原文转换为财务事实。

## 检查及审查

最终10个源码／测试／资源／使用说明／计划暂存文件执行 `scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：Ruff通过、Pyright零错误，115项pytest通过（25.20秒），成功测试目录由入口清理。之后仅添加本验收文档，提交前执行暂存差异空白检查；未再次重复业务测试。

S级规格、质量双审查及问题复核通过。修复缓存权限错误可中断批次、流式传输断开未共享重试、响应关闭异常误报成功文件失败等问题；另保留缓存最终URL、验证四个跨来源请求仍最多并发2及软缓存只提醒不删。相应回归纳入最终门禁。

静态修复中一轮第三方响应清理noqa未使用告警失败，调整为记录具体异常且注明隔离边界后通过；失败运行目录保留，没有将失败结果算成通过。未运行全量测试，无UI／会话改动，不重复浏览器验收。

## 尚未完成

官方URL自动发现、任意PDF的分红金额／派息基数抽取、多股类与财政年度完整性自动核实仍需后续适配器。当前清单中的fiscal_year／kind／share_class是调用者描述，下载器不证明其财务含义。正式选股门槛、成员发布和自动更新未启用；原始PDF、大缓存及测试产物都留在忽略tmp。
