# 实现计划：浏览器登录态通道第一阶段

## 1. 背景与目标

当前 `douyin-mcp` 已明确只做浏览器登录态通道。下一步需要先建立稳定的浏览器会话基础设施，让后续服务层和 MCP 工具可以复用同一套 Playwright 持久化 profile 逻辑。

本轮目标是完成第一阶段：新增浏览器会话层，不做 MCP 工具注册、不做真实数据抽取、不启动真实浏览器自动化测试。完成后，后续阶段可以在此基础上实现登录状态检测和页面数据同步。

成功标准是：代码提供可测试的 `BrowserSession` 封装，能根据 `Settings` 构造 Playwright `launch_persistent_context` 参数，能打开指定 URL，能安全关闭上下文，并通过 mock 单元测试验证行为。

## 2. 需求范围

### 2.1 包含

- 新增 `src/douyin_creator_mcp/browser/__init__.py`。
- 新增 `src/douyin_creator_mcp/browser/session.py`。
- 实现 `BrowserSession`：
  - 复用 `settings.douyin_browser_profile_dir`。
  - 默认可见浏览器，遵循 `settings.douyin_browser_headless`。
  - 支持 `settings.douyin_browser_channel`。
  - 支持 `start()`、`open_page()`、`open_creator_home()`、`open_creator_video_page()`、`close()`。
  - 支持上下文管理协议。
  - Playwright 依赖延迟加载。
- 新增 `tests/test_browser_session.py`，使用 mock 验证，不启动真实浏览器。
- 更新交接文档，标记第一阶段已实现。

### 2.2 不包含

- 不实现 `BrowserService`。
- 不注册 MCP browser tools。
- 不解析真实创作者中心视频数据。
- 不运行真实浏览器。
- 不保存 Cookie/profile 内容到任何工具响应。

## 3. 现状分析

项目已经具备浏览器模式配置字段和 profile 目录创建逻辑，但没有实际 browser 包。后续开发需要统一的会话封装，否则服务层会直接依赖 Playwright API，难以测试和维护。

当前测试框架为 pytest，已有 12 个测试通过。本轮应新增独立测试，并保证既有测试不回归。

## 4. 方案设计

### 4.1 总体思路

新增 `BrowserSession` 作为 Playwright 同步 API 的薄封装。它只负责启动、复用、打开页面和关闭，不做业务判断。

服务层后续通过该类获得页面对象，再做登录态检测、数据抽取和错误分类。

### 4.2 详细设计

`BrowserSession` 字段：

- `settings: Settings`
- `_playwright`
- `_context`

方法：

- `start()`：创建 profile 目录，启动 Playwright，调用 `chromium.launch_persistent_context(...)`。
- `open_page(url, wait_until="domcontentloaded")`：确保 context 已启动，复用第一个 page 或新建 page，打开 URL。
- `open_creator_home()`：打开 `settings.douyin_creator_home_url`。
- `open_creator_video_page()`：打开 `settings.douyin_creator_video_url`。
- `close()`：关闭 context 和 Playwright。
- `is_running`：返回 context 是否存在。
- `__enter__` / `__exit__`：支持 `with BrowserSession(settings) as session`。

测试策略：

- mock `_load_sync_playwright()`，避免导入或启动真实浏览器。
- 验证 `launch_persistent_context` 参数包含 profile、headless、channel。
- 验证打开 URL 时调用 page.goto。
- 验证 `close()` 会关闭 context 并停止 Playwright。

### 4.3 涉及变更的文件清单

- `src/douyin_creator_mcp/browser/__init__.py` — 新增 browser 包。
- `src/douyin_creator_mcp/browser/session.py` — 新增浏览器会话封装。
- `tests/test_browser_session.py` — 新增浏览器会话 mock 单元测试。
- `docs/agent-handoff.md` — 更新当前代码状态。
- `AGENT_HANDOFF.md` — 更新下一步建议。

## 5. 实施步骤

1. **新增 browser 包** — 添加 `__init__.py` 和 `session.py`。
2. **实现 BrowserSession** — 完成 Playwright 延迟加载、持久化 context、打开页面和关闭逻辑。
3. **补充测试** — 使用 mock 覆盖启动、打开页面、关闭。
4. **更新交接文档** — 标记第一阶段完成，下一步转向 BrowserService。
5. **运行验证** — 执行 `python -m compileall src` 和 `python -m pytest`。

## 6. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| Playwright 或 Chrome 未安装 | 真实浏览器无法启动 | 本轮只做延迟加载和 mock 测试，真实启动留到手动验证阶段 |
| 无头模式触发风控 | 同步失败 | 默认 `headless=false`，让用户可见处理登录/验证 |
| session 层混入业务逻辑 | 后续难维护 | 本轮只做薄封装，登录判断放后续 BrowserService |

## 7. 测试策略

- 单元测试：`tests/test_browser_session.py`。
- 回归测试：全量 `python -m pytest`。
- 编译检查：`python -m compileall src`。

## 8. 回滚预案

删除本轮新增文件：

- `src/douyin_creator_mcp/browser/`
- `tests/test_browser_session.py`

并回退文档中关于第一阶段已完成的描述。
