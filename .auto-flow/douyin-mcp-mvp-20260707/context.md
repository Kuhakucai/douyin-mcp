# 四阶段上下文：douyin-mcp MVP 落地

## 1. 用户需求

用户要求根据项目中的方案文档和 PRD，用四阶段方式完成 `douyin-mcp`。

本次采用 `auto-plan-and-execute` 手动四阶段流程：

1. 编写计划。
2. 审查计划。
3. 实施计划。
4. 审查实施。

按照项目指令，进入代码实施前需要先给出调整方案并等待用户确认。

## 2. 需求来源

- `douyin-creator-mcp方案...md`
- `douyin-creator-mcp-PRD.md`

## 3. 仓库现状

当前工作目录为 `<workspace>/douyin-mcp`。

需求/方案文档：

- `douyin-creator-mcp.md`
- `douyin-creator-mcp-PRD.md`

当前已关联 Git 仓库：

- 分支：`main`
- 远程：`git@github.com:Kuhakucai/douyin-mcp.git`

实施前已有文件：

- `README.md`
- `douyin-creator-mcp.md`
- `douyin-creator-mcp-PRD.md`
- `.auto-flow/` 阶段文档

未发现源码、测试或 Python 项目配置。实施审查阶段应使用 `git status` 和 `git diff`。

## 4. 核心目标摘要

本项目应落地为一个本地/私有化 Python MCP Server，用于在用户明确授权与合规边界内读取抖音开放平台数据，并向 AI Agent 暴露结构化、只读、可降级的数据能力。

MVP 必须覆盖：

- Python 项目初始化。
- FastMCP Server 骨架。
- 默认 stdio 传输。
- HTTP 模式安全约束。
- 抖音 OAuth 授权 URL 生成。
- 开发模式手动 code 换 token。
- 正式 HTTPS callback 模式的服务入口。
- OAuth state 与 auth_session_id 管理。
- token 本地加密保存与刷新。
- SQLite 本地缓存。
- 账号资料读取。
- 能力探测。
- 可用数据同步框架。
- 账号摘要与 Markdown 复盘报告。
- 统一成功/错误返回格式。
- 敏感信息不进入 MCP 返回值、日志和报告。

## 5. 安全与合规约束

- 只使用抖音开放平台 OAuth 和 OpenAPI。
- 不读取浏览器 Cookie。
- 不保存抖音账号密码。
- 不模拟创作者中心页面。
- 不绕过验证码、风控、签名或私有接口。
- 不做发布视频、评论、私信、关注等写操作。
- 不向 Agent 暴露 `code`、`access_token`、`refresh_token`、`client_secret`。
- `client_secret` 只来自环境变量，不落库。
- HTTP 模式必须具备访问控制，不能只依赖 query 参数识别调用方。
- 未确认权限的高级能力不能伪实现，必须返回结构化能力缺失错误。

## 6. 已知限制

- 当前仓库没有源码，实施将从零初始化项目。
- 当前环境没有抖音开放平台真实应用凭证，无法做真实线上 OAuth 和 OpenAPI 验证。
- 官方接口的最新字段与审核条件需要用户在抖音开放平台后台最终确认。
- 实施阶段可通过 mock HTTP、SQLite 和本地单元测试验证代码行为；真实接口连通性需要用户配置正式凭证后再验收。
