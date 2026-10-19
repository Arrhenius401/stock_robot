# 发版完善实施计划

依据同日已确认设计。独立工作树 `tmp/worktrees/release-readiness`，基于 dev 的 `434f3be`；主工作区已有云端验收记录保留，不纳入本事项覆盖。

1. 元信息：测试先验证 CLI、API/OpenAPI、MCP 初始化及回测清单版本一致；新增无依赖 metadata.py 并接入，保留 MCP 自定义服务版本参数。setuptools 采用 dynamic version attr，uv.lock 与导出清单由工具生成。
2. 默认配置：CLI 回退和帮助文案从 DEFAULT_CONFIG 读取，测试显式参数及用户配置优先级。
3. 打包：新增资源完整性回归；配置 src 布局与 package-data，构建钩子复制根 config/data/必要脚本，MANIFEST 收录构建与运行所需文件；不复制凭据、报告、缓存、临时目录或测试数据进运行包。
4. 资源路径：先测试源码和 wheel 分支，再接入只读 resource_root；分别核对调用点中配置资源与可写报告/映射/启动工作目录，不能用随包目录代替可写 cwd。
5. 从干净副本构建 sdist/wheel、从 sdist 再构建，独立安装后验证 CLI、模板、分析配置、映射、雷达配置、HTTP 健康和 Web 资源；记录测试产物位置与失败。
6. 补更新日志、维护元信息说明和主仓升级说明。独立规格与质量双审查，修复并复核；执行发布前全量验证。
7. 按完整事项形成一笔本地提交。结果交付时说明工作树与提交，不隐含推送、合并、远端标签/Release 或云端升级。

环境：复用主仓 `.venv/Scripts/python.exe` 与工具，不修改其 editable 指向。使用 PYTHONPATH 显式指定工作树 src；工作树 verify 脚本使用 -VenvPath 主仓 venv。pytest 每次独立 GUID basetemp/cache；环境权限导致的异步测试挂起按已确认事实在沙箱外运行。

## 执行与验收记录（2026-10-19）

- 测试先复现缺少统一元信息和安装资源的问题，随后接入 `metadata.py`、动态构建版本、默认配置引用与资源路径。根资源通过 `_build.py` 白名单复制，安装包的可写行业映射与报告目录不落入 site-packages。
- 首次测试因临时目录父级缺失出现环境错误，创建独立父目录后重新运行得到预期失败；不把该环境错误算作业务失败。初版根目录构建钩子在隔离构建中不可导入，迁入 `src/stock_robot/_build.py` 后修复。uv 缓存缺少构建依赖时获准联网重新解析，业务安装清单重新导出后无内容变化。
- 元信息相关既有回归 103 通过；资源路径与采集启动回归 31 通过、1 跳过。新分发测试从干净副本构建 sdist/wheel、再从 sdist 重建，安装至独立 venv，在非仓库工作目录验证元数据、CLI、分析 YAML、模板、映射、雷达配置及 API。仅复用父环境的第三方依赖，不执行其 editable .pth。
- 执行 `$env:PYTHONPATH=(Join-Path $PWD 'src'); ./scripts/verify.ps1 -Scope Full -VenvPath D:/code/stock_robot/.venv`：Ruff 通过，Pyright 0 错误，pytest **1691 通过、4 跳过**，耗时 244.97 秒。12 条警告来自 Starlette TestClient 与 LangGraph 已有弃用接口。成功运行目录由 verify 清理。
- 独立规格、质量审查发现并修复：邮件及 systemd 展示名未引用 APP_NAME；仅改版本号未触发新增门禁；归档测试不兼容 Python 3.11 早期补丁版。新增测试验证名称替换、精确检查映射，以及 wheel 随包采集脚本的 XML/unit 路径、工作目录与真实 `--help` 子进程。两位审查者复审通过，无剩余阻断项。
- 审查修复后单文件 Ruff 通过、Pyright 0 错误；定向 pytest `tests/test_release_metadata.py tests/test_distribution.py tests/test_verify_script.py tests/push/test_executor.py tests/radar/test_collector_startup.py`：**63 通过、1 跳过**，29.40 秒。最终状态复用首轮全量覆盖，并补上述受影响范围，不将首次全量数字冒充修复后的再次全量结果。
- `uv lock --check --python D:/code/stock_robot/.venv/Scripts/python.exe`：219 个包，检查通过。core/rag 锁定安装清单无业务依赖漂移。
- 使用主仓 `.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765` 和工作树 PYTHONPATH，从独立临时工作目录真实启动服务，关闭该临时实例的 LLM 与推送。本机 HTTP `/health` 返回 200、版本 0.2.0；`/`、`/js/chat.js`、`/api/v1/radar/universes` 均为 200。沙箱阻止本机套接字后获准在沙箱外复核通过，随后终止本次服务。本事项未改前端或会话流；这些 HTTP 冒烟不是浏览器真实会话验收。

未覆盖：没有在 Python 3.11 各补丁版本或全新 Linux 环境实际安装全部依赖；没有执行云服务器升级、QQ 私聊/群聊队列或真实 AI 报告验收。不得把本地主仓发布准备结果替代插件的线上验收。未推送、合并、创建标签或 Release。
