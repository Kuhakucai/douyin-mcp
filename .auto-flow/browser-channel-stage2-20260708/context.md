# 上下文：浏览器通道可调用闭环

## 用户需求

用户要求继续做下一阶段。在上一阶段已经完成 `BrowserSession` 的基础上，本阶段需要让浏览器登录态通道可以被 MCP 工具调用，并能把用户自己可见的创作者中心页面信息落到本地。

## 当前仓库背景

- 主线已经明确为浏览器登录态通道。
- 官方 OpenAPI、小程序基础能力、手动导入都不是当前 MVP 主线。
- 第一阶段已完成：
  - `src/douyin_creator_mcp/browser/session.py`
  - `tests/test_browser_session.py`
  - 18 个测试通过。
- 当前工作区包含第一阶段未提交改动，本阶段在其上继续叠加，不回退已有文件。

## 本阶段目标

实现“浏览器通道可调用闭环”：

- 浏览器页面抽取器。
- `BrowserService`。
- MCP browser tools。
- `server.py` 注册。
- 单元测试。
- 文档交接更新。

## 明确不做

- 不绕过登录、验证码、扫码、风控。
- 不返回 Cookie、localStorage、sessionStorage 或 profile 内容。
- 不做官方 OpenAPI 新开发。
- 不承诺精准解析每条视频的完整经营指标。
- 不自动启动真实浏览器作为单元测试。

## 验收标准

- `python -m compileall src` 通过。
- `python -m pytest` 通过。
- `python -m unittest discover -s tests` 通过。
- MCP 工具注册代码存在并有单元测试覆盖。
- `sync_creator_data` 能把抽取快照写入 `browser_snapshots`。
