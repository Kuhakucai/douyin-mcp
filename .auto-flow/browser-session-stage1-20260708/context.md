# 四阶段上下文：浏览器登录态通道第一阶段

## 1. 用户需求

用户要求按照四阶段方法开始修改第一阶段。

当前项目主线已经明确为浏览器登录态通道，官方 OpenAPI、小程序基础能力、手动导入都不是当前开发方向。

## 2. 本轮范围

第一阶段只实现浏览器会话层：

- 新增 `src/douyin_creator_mcp/browser/`。
- 新增 Playwright 持久化 profile 会话封装。
- 不注册 MCP 工具。
- 不实现真实页面数据抽取。
- 不在自动测试中启动真实浏览器。

## 3. 依据文档

- `AGENT_HANDOFF.md`
- `docs/agent-handoff.md`
- `docs/browser-session.md`
- `docs/project-structure.md`

## 4. 当前代码状态

已存在：

- `Settings` 中的浏览器配置。
- `browser_snapshots` 表。
- `pyproject.toml` 中的 `playwright>=1.45` 依赖。
- `.env.example` 中的浏览器配置。

未存在：

- `src/douyin_creator_mcp/browser/`
- `src/douyin_creator_mcp/browser/session.py`
- 浏览器会话测试。

## 5. 验证要求

- `python -m compileall src`
- `python -m pytest`

自动化测试只能使用 mock，不启动真实浏览器。
