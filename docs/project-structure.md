# 项目结构

当前项目按“浏览器登录态主线 + 历史 OpenAPI 骨架保留”组织：

```text
douyin-mcp/
  pyproject.toml
  README.md
  .env.example
  docs/
    api-mapping.md
    browser-session.md
    install.md
    limitations.md
    oauth.md
    project-structure.md
    scopes.md
  src/
    douyin_creator_mcp/
      server.py
      callback_app.py
      config.py
      errors.py
      responses.py
      security.py
      api_mapping.py
      browser/
        __init__.py
        extractors.py
        session.py
      tools/
        auth_tools.py
        account_tools.py
        sync_tools.py
        report_tools.py
        browser_tools.py
      services/
        auth_service.py
        douyin_api.py
        account_service.py
        capability_service.py
        sync_service.py
        report_service.py
        browser_service.py
      storage/
        db.py
        token_store.py
        schemas.sql
  tests/
```

## 当前已实现

- 官方 OpenAPI 通道的历史基础骨架。
- OAuth state、token 加密、本地 SQLite、能力探测、降级报告。
- MCP 工具注册入口。
- HTTP callback app 入口。
- 浏览器登录态通道的第一阶段会话层：
  - `browser/session.py`
  - Playwright 持久化 profile 封装。
  - 可打开创作者中心首页和视频管理页。
  - mock 单元测试，不启动真实浏览器。
- 浏览器登录态通道的第二阶段可调用闭环：
  - `browser/extractors.py`
  - `services/browser_service.py`
  - `tools/browser_tools.py`
  - `server.py` 注册 browser tools。
  - 页面快照写入 `browser_snapshots`。
  - 基础浏览器快照报告写入 `reports`。
- 浏览器登录态通道的第三阶段真实联调入口：
  - `browser_smoke.py` 提供真实联调 CLI 子命令。
  - `services/browser_service.py` 提供安全快照摘要和显式会话关闭。
  - `tests/test_browser_smoke.py` 覆盖命令分发、登录轮询、超时和错误输出。
  - `pyproject.toml` 注册 `douyin-browser-smoke`。
- 浏览器登录态通道的第四阶段结构化数据闭环：
  - `browser/extractors.py` 自动滚动并解析作品卡片。
  - `services/browser_service.py` 幂等写入 `videos`、`video_metrics` 并分页读取。
  - `tools/browser_tools.py` 注册 `douyin_browser_list_videos`。
  - `tests/fixtures/browser_video_cards.json` 提供脱敏结构夹具。

## 下一步结构调整

下一步只开发浏览器登录态通道，后续增强：

```text
src/douyin_creator_mcp/
  browser/
    fixtures/
      creator_video_manage_logged_in.html
  storage/
    browser_repository.py
```

对应测试：

```text
tests/
  test_browser_session.py
  test_browser_service.py
  test_browser_extractors.py
  test_browser_tools.py
```
