# 实现计划：浏览器真实联调入口（第三阶段）

## 1. 背景与目标

浏览器登录态通道已经具备 MCP 工具和页面快照能力，但当前只能通过 MCP 客户端触发，真实 Chrome 联调缺少稳定、可复跑的入口。本阶段增加一个本地 CLI，将同一套 `BrowserService` 能力暴露为开发者命令，使首次登录、次日复用 profile 同步、报告生成和故障定位可以独立验证。

成功标准是命令可安装、输出机器可读 JSON、不泄露浏览器凭证，并由 mock 测试验证所有命令路径。真实浏览器登录后的 DOM 精准解析留到获得真实页面样本后实施。

## 2. 需求范围

### 2.1 包含

- 新增 `douyin-browser-smoke` 命令及 Python 模块入口。
- 提供 `login`、`status`、`sync`、`report`、`latest-snapshot` 子命令。
- 登录命令轮询当前页面状态，允许用户在可见浏览器完成扫码或验证。
- 增加浏览器会话显式关闭能力和最近快照安全摘要接口。
- 增加 CLI 单元测试和服务层摘要测试。
- 更新 README、浏览器通道文档及 Agent 交接文档。

### 2.2 不包含

- 不实现官方 OpenAPI 通道。
- 不采集或返回 Cookie、Storage、验证码、密码。
- 不基于猜测实现真实页面 DOM 选择器。
- 不将候选文本写入 `videos`、`video_metrics`。
- 不把真实 profile、页面全文或用户数据加入测试夹具和 Git。

## 3. 现状分析

`BrowserService` 已能打开主页、检查同一进程中的会话、同步视频管理页快照并生成报告。`sync_creator_data()` 每次可独立创建持久化浏览器上下文，因此适合次日由 Agent 独立触发。当前缺口是没有 CLI 编排登录等待过程，也没有公开的安全快照摘要接口；直接调用私有 `_latest_snapshot()` 会暴露完整抽取文本，不适合作为诊断命令。

`pyproject.toml` 已有两个 console script，可沿用相同方式新增命令。测试以 `unittest` 风格编写并由 pytest 收集，适合通过 mock `BrowserService` 避免启动 GUI。

## 4. 方案设计

### 4.1 总体思路

新增轻量 `browser_smoke.py`，复用 `load_settings()`、`ensure_runtime_dirs()`、`Database.init_schema()` 和 `BrowserService`。CLI 只负责编排和 JSON 输出，不复制服务逻辑。所有子命令单次进程可执行；`login` 在同一进程中保持浏览器会话并轮询状态，结束后显式关闭。

### 4.2 详细设计

- `BrowserService.close_browser()`：幂等关闭当前会话。
- `BrowserService.latest_snapshot_summary(account_id)`：只返回快照 ID、账号 ID、URL、标题、状态、创建时间、文本行数和候选视频数，不返回页面全文或任何浏览器存储。
- `browser_smoke.build_parser()`：定义子命令和参数。
- `browser_smoke.run_command(args, service, sleep)`：可测试的命令分发函数。
- `login --timeout --poll-interval`：调用 `login_start()`；若已登录立即成功，否则轮询 `login_status()`，遇到 `logged_in` 成功，超时返回 `timed_out`，最终关闭会话。
- `status`：短暂打开创作者中心读取当前 profile 状态，然后关闭，并在 help 与文档中明确会启动浏览器。
- `sync --account-id`：独立调用同步。标准联调顺序先执行 `login`，再执行 `sync`，避免受阻后的 CLI 进程生命周期差异。
- `report --account-id --period`：基于最近快照生成报告。
- `latest-snapshot --account-id`：输出安全摘要。
- `main()`：初始化运行目录和数据库，输出 `ensure_ascii=False` 的 JSON；异常转为统一错误响应并返回非零退出码。

### 4.3 涉及变更的文件清单

- `src/douyin_creator_mcp/services/browser_service.py` — 增加显式关闭和安全快照摘要。
- `src/douyin_creator_mcp/browser_smoke.py` — 新增 CLI 编排入口。
- `pyproject.toml` — 注册 `douyin-browser-smoke`。
- `tests/test_browser_service.py` — 覆盖安全摘要与关闭行为。
- `tests/test_browser_smoke.py` — 覆盖命令解析、分发、登录轮询、超时和错误输出。
- `README.md` — 增加真实联调命令和次日自动同步说明。
- `docs/browser-session.md` — 增加完整联调步骤和安全边界。
- `docs/agent-handoff.md` — 更新第三阶段状态与下一步。
- `AGENT_HANDOFF.md` — 更新简版交接信息。
- `docs/project-structure.md` — 记录新增 CLI 和测试。

## 5. 实施步骤

1. **服务层补齐诊断接口** — 增加会话关闭和安全快照摘要，并以单元测试验证不含完整抽取数据。
2. **实现 CLI** — 完成五个子命令、登录轮询、统一 JSON 输出和退出码。
3. **注册安装命令** — 在 `pyproject.toml` 增加 console script。
4. **补充自动化测试** — mock 服务和 sleep，覆盖成功、超时、异常及参数路径。
5. **更新文档和交接** — 给出首次登录、次日同步及 Agent 调用顺序。
6. **运行验证** — 执行 compileall、pytest、unittest，并检查 CLI help。
7. **真实 GUI smoke** — 在获得 GUI 执行授权后启动 `status` 或 `login`；用户验证环节不计为自动化测试通过条件。

## 6. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| CLI `login` 长时间等待 | 终端看似卡住 | 提供超时和轮询间隔参数，最终必定关闭会话 |
| 登录状态词判断与真实页面不一致 | 误报未知或未登录 | 本阶段保留保守判断，真实页面样本进入下一阶段 |
| `sync` 遇到验证时窗口被关闭 | 用户无法处理验证 | 文档推荐先完成 `login`，再独立执行 `sync` |
| 诊断输出泄露页面内容 | 隐私风险 | 摘要只返回计数和元数据，不返回正文与存储内容 |
| Chrome/profile 被其他进程占用 | 启动失败 | 输出结构化错误，文档提示关闭占用同一 profile 的进程后重试 |

## 7. 测试策略

- 单元测试：服务层摘要字段白名单、关闭幂等；CLI 五个子命令、登录立即成功、轮询成功、超时、异常返回。
- 集成验证：初始化临时 SQLite，确认最近快照摘要可读取。
- 命令验证：`python -m douyin_creator_mcp.browser_smoke --help`。
- 全量回归：`python -m pytest -q` 与 `python -m unittest discover -s tests`。
- 手动验证：授权后打开真实 Chrome，检查 profile 复用和登录状态。

## 8. 回滚预案

删除新增 CLI 模块和测试，移除 `pyproject.toml` 中的 console script，并回退 `BrowserService` 新增的两个公开方法及文档更新。数据库结构不变，不需要数据迁移。
