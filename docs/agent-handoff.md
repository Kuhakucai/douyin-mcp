# Agent 交接上下文

本文给后续接手开发的本地 agent 使用，目标是快速恢复上下文并继续开发。

## 当前目标

项目名称：`douyin-mcp`

最终目标：让本地 AI Agent 能获取用户自己的抖音创作者视频数据，写入本地缓存，并生成账号复盘报告。

关键约束：

- 用户是个人主体，当前无法创建抖音开放平台「网站应用」。
- 小程序基础能力不能满足“读取创作者本人视频经营数据”的目标。
- 不接受手动导入 CSV/JSON 作为主方案。
- 因此项目当前只做浏览器登录态通道。官方 OpenAPI、小程序、手动导入都不是下一步开发方向。

## 已做决策

### 1. 官方 OpenAPI 通道已否决为当前主线

仓库里已有 OpenAPI 基础骨架：

- OAuth start/complete/status。
- Token Fernet 加密落库。
- SQLite schema。
- API mapping。
- 能力探测。
- 同步任务。
- 降级报告。
- MCP 工具注册。
- FastAPI callback app。

这些代码暂时保留，避免下班前大规模删除引入风险。但后续 agent 不要继续沿 OpenAPI 路线开发，也不要把它作为当前 MVP 的验收目标。

### 2. 浏览器登录态通道是唯一主线

原因：

- 个人主体无法创建网站应用，导致 OpenAPI 获取视频数据路线不可用。
- 用户明确要求必须让 AI 获取自己的视频数据，不能做手动导入版。
- 浏览器登录态模式可以复用用户本机已登录的创作者中心页面，读取用户自己可见的数据。

边界：

- 默认打开可见浏览器。
- 首次登录、扫码、验证码、风控确认必须用户本人处理。
- Cookie/profile 不返回给 Agent。
- 不绕过权限，不读取用户不可见数据。
- 登录态保存在 `data/browser-profile/`，已在 `.gitignore` 中忽略。

## 当前代码状态

当前已实现：

- `src/douyin_creator_mcp/config.py`
  - 已新增浏览器配置字段：
    - `douyin_browser_profile_dir`
    - `douyin_browser_headless`
    - `douyin_browser_auto_close`
    - `douyin_browser_channel`
    - `douyin_creator_home_url`
    - `douyin_creator_video_url`
- `src/douyin_creator_mcp/storage/schemas.sql`
  - 已新增 `browser_snapshots` 表。
- `pyproject.toml`
  - 已加入 `playwright>=1.45` 依赖。
- `.env.example`
  - 已加入浏览器通道配置。
- `docs/browser-session.md`
  - 已描述浏览器登录态模式。
- `docs/project-structure.md`
  - 已描述下一步目录结构。
- `src/douyin_creator_mcp/browser/session.py`
  - 已实现 Playwright 持久化 profile 会话封装：
    - `start()`
    - `open_page()`
    - `open_creator_home()`
    - `open_creator_video_page()`
    - `close()`
    - `is_running`
    - 上下文管理协议。
- `tests/test_browser_session.py`
  - 已用 mock 覆盖浏览器会话启动、打开页面、关闭、空 channel、上下文管理协议。
- `src/douyin_creator_mcp/browser/extractors.py`
  - 已实现页面文本清洗、登录状态判断、疑似视频文本提取、页面快照抽取。
- `src/douyin_creator_mcp/services/browser_service.py`
  - 已实现：
    - `login_start()`
    - `login_status()`
    - `sync_creator_data()`
    - `refresh_report()`
  - `sync_creator_data()` 会写入 `browser_snapshots` 和 `sync_jobs`。
  - `refresh_report()` 会基于最近浏览器快照生成基础 Markdown 报告并写入 `reports`。
- `src/douyin_creator_mcp/tools/browser_tools.py`
  - 已注册：
    - `douyin_browser_login_start`
    - `douyin_browser_login_status`
    - `douyin_browser_sync_creator_data`
    - `douyin_browser_refresh_report`
- `src/douyin_creator_mcp/server.py`
  - `ServiceContainer` 已增加 `browser_service`。
  - `create_mcp()` 已注册 browser tools。
- 浏览器通道测试：
  - `tests/test_browser_extractors.py`
  - `tests/test_browser_service.py`
  - `tests/test_browser_tools.py`
  - `tests/test_browser_smoke.py`
- `src/douyin_creator_mcp/browser_smoke.py`
  - 已提供 `login`、`status`、`sync`、`report`、`latest-snapshot`、`videos` 六个子命令。
  - CLI 输出 JSON，异常返回非零退出码。
  - 最近快照摘要不返回页面正文、Cookie 或 Storage，并移除 URL 查询参数和片段。

当前未实现：

- 页面未展示的完播率、平均观看时长和粉丝增长。
- 官方稳定作品 ID；当前使用账号、发布时间和标题生成本地哈希 ID。

## 建议下一步开发顺序

1. 增强页面结构变化后的诊断和解析回退。

真实联调已经按以下 CLI 顺序通过，后续可直接复跑：

- `douyin-browser-smoke login --timeout 180`
- `douyin-browser-smoke status`
- `douyin-browser-smoke sync --account-id browser-default`
- `douyin-browser-smoke latest-snapshot --account-id browser-default`
- `douyin-browser-smoke report --account-id browser-default --period latest`

MCP Agent 的正常调用顺序仍是 `douyin_browser_login_start`、用户处理验证、`douyin_browser_login_status`、`douyin_browser_sync_creator_data`、`douyin_browser_refresh_report`。第二天通常可以直接同步；只有 profile 登录失效或风控触发时才需要用户再次处理。

2. 设计本地合成 ID 向未来稳定作品 ID 的迁移方案。

当前抽取器是保守文本快照版。下一步应根据真实创作者中心页面结构，补充稳定选择器和字段映射。

3. 将快照映射到结构化表。

后续逐步写入：

- `videos`
- `video_metrics`

4. 使用已实现的 Playwright 持久化 profile 会话层

当前已封装在：

```text
src/douyin_creator_mcp/browser/session.py
```

默认：

```env
DOUYIN_BROWSER_HEADLESS=false
DOUYIN_BROWSER_AUTO_CLOSE=true
DOUYIN_BROWSER_CHANNEL=chrome
```

5. 数据抽取当前是保守版

当前已经先抽取：

- 页面标题。
- 当前 URL。
- 页面中疑似视频行的文本快照。
- 同步时间。

写入：

- `browser_snapshots`
- 后续再逐步映射到 `videos`、`video_metrics`。

6. 测试策略

单元测试优先：

- 不启动真实浏览器。
- 对 extractors 输入 HTML 字符串，验证能提取候选视频文本。
- mock browser session，验证 `login_required`、`verification_required`、`success` 分支。

真实浏览器验证单独手动跑。

## 常用命令

```powershell
python -m compileall src
python -m pytest
python -m unittest discover -s tests
```

安装依赖：

```powershell
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

如果使用本机 Chrome channel，通常先尝试不下载 Chromium：

```env
DOUYIN_BROWSER_CHANNEL=chrome
```

## 当前验证状态

已通过：

- `python -m compileall src`
- `python -m pytest`：87 passed，另有 6 个参数化子测试通过
- `python -m unittest discover -s tests`：87 passed
- `python -m douyin_creator_mcp.browser_smoke --help`
- FastMCP `create_mcp()` 对象创建。
- FastAPI `create_app()` 路由创建。
- 真实 Chrome 扫码登录，`login_status=logged_in`。
- 关闭后重新启动浏览器可复用 profile。
- 真实视频管理页同步：80 行文本、1 个候选，快照成功写入 SQLite。
- 基于真实快照生成 Markdown 报告。
- 无限滚动已从首屏加载到页面声明的全部作品，声明数、加载数和解析数一致。
- 结构化作品与当日列表指标重复同步后记录数保持不变；真实数量不进入仓库。
- `douyin-browser-smoke videos --limit 3` 可读取视频标题、发布时间、时长、封面和最新指标。

未验证：

- 页面结构发生变化后的回退效果。
- 合成作品 ID 在标题或发布时间编辑后的迁移。

## 风险提醒

- 抖音创作者中心页面结构可能变化。
- 登录态可能过期。
- 风控/验证码必须用户本人处理。
- 不要把 Cookie、localStorage、sessionStorage、profile 内容返回给 Agent。
- 不要提交 `data/` 或 `data/browser-profile/`。
