# 短行情、事实报告与部分评估实施计划

依据：用户提供的 Claude 交接文本；产品决策和验收口径已锁定。主仓基线为 163c1bc；独立工作树为 fa27/stock_robot。禁止提交、推送、发布、部署和云端访问。历史项目文档不追加本次独立事项。

## 实施顺序

1. 先新增 0/1/2/19/20/34/59/60 日线边界回归，修正价格位置的展示门槛，保留评分算法。
2. 在质量视图中统一表达身份、必需维度、评估状态、报告范围及去重限制；零日线或不足两维评分仅展示事实。
3. API 保留 422/detail，增加错误码、身份和逐来源行情诊断；只在没有任何事实时拒绝报告。
4. 将行情来源诊断经适配器、采集记录和缓存传入报告；保留主源优先、备用整份、不拼接。
5. 同步 Markdown、结构化报告和 Web 标签；事实报告隐藏历史区间和技术指标，部分报告明确参考分。
6. 验证真实充实器、分析器和报告路径，覆盖可信报价、财务独立事实、来源失败及旧缓存；按范围执行 pytest/Ruff/Pyright。
7. 按项目启动路径进行真实浏览器发送和响应验证；记录未覆盖事项后交由 Claude 验收，不提交。

## 插件与字体边界

插件作为后续独立工作区处理：错误码优先、detail 向后兼容；两种实际字体都覆盖上标才直传 Unicode，否则图片副本统一回退，下载原文不变。

后续只读检索在插件 `tmp/render-fix-20261010/fonts/NotoSansCJKsc-Bold.otf` 找到匹配的粗体，已与交接前缀核对完整 SHA-256。未访问云端。CFF CID 原型成功，最终产物改名 Stock Robot CJK，保留授权信息。

安全检查发现交接建议的 `cid65530`、`cid65527` 是 GSUB Lookup 41 的替换输出（分别对应 `cid15923`、`cid47109`），不得覆盖。改为两套共同空闲的 `cid63107`、`cid63106`，并检查所有 cmap 及 GSUB/GPOS/GDEF/BASE 引用。字形数仍为 65535，原 GSUB 映射不变。

## 验证记录

- 初始价格边界回归 8 failed，证实 1/2/19 日线仍计算位置且缺少样本数字段。
- 首轮相关回归 50 passed、6 setup errors：唯一 basetemp 的父目录未建立，已修正运行命令；复跑 56 passed。
- 新增真实 Pipeline.run/充实/技术分析/双报告回归及必需维度状态测试后，目标文件 22 passed。
- 初次 Pyright 发现测试中 Enum/Literal 标注错误，已修正；最终增量 Pyright 为 0 errors，Ruff 通过，两仓 `git diff --check` 通过。
- `scripts/verify.ps1 -Scope Changed` 执行了 Ruff、Pyright、取数入口检查及 tests/api、core、data、report、agent：2288 passed、7 failed。七项均为旧契约断言或不完整测试样本，修正后相关测试 59 passed；审查修复后的评分/质量/真实管道范围为 77 passed。没有重复运行整套范围回归，不能把首次输出写成全部通过。完整首轮异常及失败清单保留于 `tmp/verify-changed.log`。
- 最终 API 契约与真实管道目标回归 54 passed、77 deselected，覆盖全部结构化错误分支、零日线可信报价/仅财务事实、未确认身份有/无事实、各行情数量、有/无报价、备源失败、缓存来源保留和过期缓存报告。
- 独立规格和质量审查发现事实报告研判/限制区技术指标泄露及 `100m²` 单位误改，均已修复并加入回归；评分公式、权重与阈值未改。
- 指定真实启动命令 `./.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765` 已执行；内置浏览器两次真实发送 ANT，最终页面保留报价、明确 2 条日线的起止日期、事实报告范围，不显示技术章节/综合评分/操作信号。最终截图 `tmp/browser-final.jpg`。
- 插件客户端相关测试 120 passed、1 failed；唯一失败为交接已知的进度文案断言，保留不改。新增错误码与旧格式目标测试 6 passed；指数错误码目标复核 1 passed。字体与渲染适配 20 passed；最终字体管理单独复核 10 passed。
- 字体构建脚本使用 fontTools 4.65.0，两次构建清单完全相同。Pillow 12.3.0 分别渲染常规/粗体，图在插件 `tmp/final-font-validation/`，最终产物和哈希清单位于插件 `assets/fonts/`。

## 改动清单与交付位置

主仓独立工作树：`C:/Users/25618/.codex/worktrees/fa27/stock_robot`。

- API：`src/api/app.py`。
- 采集与上下文：`src/core/pipeline.py`、`src/data/akshare.py`、`src/data/collection_state.py`、`src/data/schemas.py`、`src/data/us_stock.py`。
- 报告：`src/report/builder.py`、`formatter.py`、`payload.py`、`quality.py`、`scoring.py`、`templates/report_v2.jinja2`。
- Web：`src/api/static/js/report-renderer.js`；缓存版本同步于 `src/api/static/index.html` 及 `js/app.js`、`chat.js`、`indexview.js`、`report-drawer.js`、`report-library.js`、`report.js`。
- 回归：`tests/api/test_app.py`、`tests/core/test_us_commentary_fallback.py`、`tests/data/test_us_quote.py`、`tests/report/test_scoring.py`、`test_us_c_scoring.py`、`test_short_history_scope.py`；文档仅本计划。

插件独立工作树：`C:/Users/25618/.codex/worktrees/short-history-fonts/astrbot_plugin_stock_robot`。

- 代码：`client.py`、`font_support.py`、`main.py`、`report_renderer.py`。
- 字体：`assets/fonts/StockRobotCJK-Regular.otf`、`StockRobotCJK-Bold.otf`、`superscript-hashes.json`、`NOTICE.txt`，原 OFL 和常规源字体保留。
- 构建：`scripts/build_superscript_fonts.py`。
- 回归：`tests/test_client.py`、`tests/test_font_support.py`、`tests/test_report_renderer.py`。

## 留给 Claude/用户的验收与风险

- 本地插件环境未安装 AstrBot，尚未执行真实 MarkdownRenderer 和重启后的字体缓存验收；现有验证为 Pillow 原型/最终产物及插件原生渲染调用适配测试。未访问云端；未修改实际运行字体。
- 行情样本不足只陈述实际数量和来源诊断，不能确认上市以来完整历史，也不能仅凭数量判断上游是否截断。
- `general.py` 量价评分在 20–24 条时使用短窗口，报告量比要求 25 条；此既有口径差异只记录，未改公式。MACD 当前两端均使用 34 条门槛。
- 字体替换后的当前进程保持回退；重启后两套实际字体完整覆盖上标才直传 Unicode。未知字体不覆盖，已知旧字体完整哈希匹配后备份、原子替换。
- 没有提交、推送、发布、部署、改版本或标签；两个原仓工作区保持干净。代码和产物留在上述工作树供审查，不隐式合入原仓。

## 原交接补充：三市场边界

依据用户补充，报告范围、部分评估、价格标签及结构化错误码统一覆盖 A 股、港股、美股；市场数据策略继续独立。

- A 股/港股保持财务、技术、估值三核心信号门槛；美股额外要求行业。A/港股财务逐项评分依据的既有充分度判定保留，不套用美股四维充分度规则。
- 没有将美股 20 条行情入口条件推广到 A/港股；采集层和各市场取数策略在此次补充中未修改。零日线有事实仍返回事实报告；A/港股可以复用上下文中的已有名称。
- A 股派生估值继续检查行情与财报；港/美实测估值不依赖日线。短历史技术不足仍阻断信号。
- 港股事实解读及五维结构保留。事实报告仅从展示副本过滤技术结果；结构化解读保留其余事实和技术不可用说明，不生成综合分或操作信号。原始分析结果不修改。
- 三市场 API 无可用事实时均返回结构化错误。美股继续保留 us_history 逐来源诊断，A/港股沿用现有采集记录。

新增 `tests/report/test_market_report_scope.py`；扩展 `test_short_history_scope.py` 与 `tests/api/test_app.py` 的三市场参数。相关旧测试中的完整评估样本补齐实际日线，零日线断言按事实报告更新；快照测试改核对维度原始摘要，避免用已被范围规则过滤的正文检验快照隔离。

验证（本轮未执行全仓检查）：

- 三市场真实管道与入口/范围目标集：141 passed、77 deselected。
- 首轮失败相关文件复核：169 passed。
- 非法语法、已有名称、实测/派生估值边界补充：11 passed、150 deselected。
- 最终 `.venv/Scripts/python -m pytest tests/report -q`：447 passed，11.87 秒。
- 改动 Python 文件 Ruff 通过，Pyright 0 errors，`git diff --check` 通过。
- 本轮没有新做三市场真实浏览器采集验收；此前真实美股页面验收记录保留。未运行真实 AstrBot 或访问云端。

此次额外更新的既有回归文件为 `tests/report/test_hk_live_report.py`、`test_quality.py`、`test_signal.py`、`test_stock_report_refinement.py`，其余改动继续在上方同一工作树累积，不分笔提交。

## 本地合入授权

用户随后明确授权将本次主仓改动直接合入本地 dev，覆盖原交接中的主仓禁止提交限制。按完整事项合为一笔提交，再从 163c1bc 快进合入；不推送远端，不提交插件改动。主仓原有未提交的 CHANGELOG.md 保持原样。前文“未提交”描述的是此前交付时的历史状态。
