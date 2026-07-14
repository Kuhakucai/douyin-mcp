# douyin-mcp Agent Handoff

这是给后续接手开发的 agent 的入口文档。

请优先阅读：

1. `docs/agent-handoff.md` — 当前上下文、关键决策、下一步开发顺序。
2. `docs/browser-session.md` — 浏览器登录态通道设计。
3. `docs/project-structure.md` — 当前项目结构和下一步结构调整。
4. `douyin-creator-mcp-PRD.md` — PRD，已更新为浏览器登录态主线。
5. `douyin-creator-mcp.md` — 实施方案，已更新为浏览器登录态主线。

当前最重要的结论：

- 当前开发主线只做浏览器登录态通道。
- 官方 OpenAPI 通道已经否决为当前主线；保留的 OpenAPI 代码只是历史骨架，不要继续沿这个方向开发。
- 小程序基础能力不能满足“AI 获取用户自己的创作者视频数据”的目标。
- 手动导入 CSV/JSON 不符合项目目标，也不要作为主线。
- 下一步重点是浏览器登录态通道：可见浏览器、用户手动登录、持久化本地 profile、读取创作者中心用户自己可见的视频数据。
- 不要把 Cookie、localStorage、sessionStorage、验证码、账号密码返回给 Agent。

当前验证状态：

- `python -m compileall src` 通过。
- `python -m pytest` 87 passed，另有 6 个参数化子测试通过。
- `python -m unittest discover -s tests` 87 passed。
- `douyin-browser-smoke` 支持 `login`、`status`、`sync`、`report`、`latest-snapshot`、`videos`。
- 真实 Chrome 联调已通过：扫码登录、profile 复用、视频管理页同步和报告生成均成功。
- 真实账号已验证页面声明数、加载数与解析数一致；重复同步后作品与当日指标记录保持幂等，真实数量不进入仓库。

下一步建议：

1. 增强页面结构变化后的解析诊断和选择器回退。
2. 研究页面是否会公开稳定作品 ID，并设计本地合成 ID 的迁移策略。
3. 基于结构化历史快照生成趋势与选题复盘。
