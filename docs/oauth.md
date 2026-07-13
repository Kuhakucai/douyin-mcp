# OAuth 流程（历史保留）

当前项目主线已经改为浏览器登录态通道。本文档仅保留官方 OpenAPI/OAuth 历史方案，后续开发不要以本文件作为当前 MVP 主线。

## local_manual_code

开发模式适合没有 HTTPS callback 的本地调试。

```text
Agent 调用 douyin_auth_start
用户打开 authorization_url 并完成授权
用户复制回调地址中的 code
Agent 调用 douyin_auth_complete(code, state)
MCP Server 换 token 并加密保存
Agent 调用 douyin_auth_status 查询结果
```

限制：

- 仅建议本地开发使用。
- OAuth code 可能进入模型上下文，正式环境不推荐。
- 工具返回值和日志不记录 code。

## https_callback

正式模式适合生产部署。

```text
Agent 调用 douyin_auth_start
用户打开 authorization_url 并授权
抖音回调 HTTPS redirect_uri?code=...&state=...
Callback 服务校验 state 并换 token
Agent 调用 douyin_auth_status(auth_session_id)
```

要求：

- `redirect_uri` 必须与抖音开放平台后台一致。
- 必须使用 HTTPS。
- 必须校验 state。
- Agent 不接触 code、access_token、refresh_token。
