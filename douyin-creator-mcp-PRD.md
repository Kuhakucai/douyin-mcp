# 抖音创作者 MCP 产品需求文档

> 文档版本：V0.5
> 创建日期：2026-07-07  
> 文档状态：PRD 迭代稿  
> 依据文档：`douyin-creator-mcp.md`  
> 修订说明：明确浏览器登录态通道为唯一当前主线，官方 OpenAPI、小程序基础能力、手动导入均不作为当前 MVP 方向。

## 1. 文档信息

| 项目 | 内容 |
|---|---|
| 产品名称 | 抖音创作者 MCP |
| 产品类型 | 本地 / 私有化 MCP Server |
| 面向对象 | AI Agent、内容运营人员、抖音创作者、开发者 |
| 文档版本 | V0.5 |
| 创建日期 | 2026-07-07 |
| 当前状态 | 浏览器结构化数据 MVP 已完成 |

## 2. 产品定位

抖音创作者 MCP 是一个面向 AI Agent 的本地 MCP Server，用于通过本机浏览器登录态读取用户自己在抖音创作者中心可见的视频数据，并将账号信息、视频数据、本地缓存和复盘报告以结构化工具形式提供给 MCP Client。

产品当前只支持浏览器登录态主线。官方 OpenAPI 方向已因个人主体无法创建网站应用而否决为当前 MVP；小程序基础能力不能获取创作者本人视频经营数据；手动导入 CSV/JSON 不符合 MCP 自动更新目标。MCP 不向 Agent 暴露 Cookie、Token、验证码或账号密码，而是把浏览器会话保存在本机 profile 中，并只返回结构化后的账号数据。

产品目标是提供一个稳定、安全、可降级的数据连接器，让 Agent 能完成：

```text
打开可见浏览器 -> 用户登录/验证 -> 复用本地 profile -> 同步创作者中心可见视频数据 -> 本地缓存查询 -> 生成账号复盘
```

### 2.1 当前实现状态

- 已实现可见 Chrome 登录、扫码/验证和持久化 profile 复用。
- 已实现作品管理页无限滚动，能够加载页面声明的全部作品。
- 已实现标题、发布时间、时长、封面及播放/点赞/评论/分享/收藏的结构化解析。
- 已实现 `videos`、`video_metrics` 幂等写入和分页读取。
- 已提供 `douyin_browser_sync_creator_data`、`douyin_browser_list_videos`、`douyin_browser_refresh_report` 等 MCP 工具。
- 真实账号已完成全量一致性与重复同步幂等验证；真实作品数量不进入仓库。

页面未公开稳定作品 ID，当前使用“账号 + 发布时间 + 标题”生成本地哈希 ID。页面未展示的指标保持为空，不做推测。

### 2.2 已否决方向

| 方向 | 否决原因 | 是否保留 |
|---|---|---|
| 官方 OpenAPI | 个人主体无法创建网站应用，当前拿不到创作者视频数据 API 接入条件 | 代码历史骨架暂保留，不作为当前开发方向 |
| 小程序基础能力 | 小程序服务端能力不等于创作者中心视频经营数据 | 不作为当前开发方向 |
| 手动导入 | 不符合“Agent 通过 MCP 自动更新数据”的项目目标 | 不作为当前开发方向 |

浏览器登录态通道不是绕过权限。它只读取用户已登录账号在创作者中心可见的数据；需要登录、扫码、验证码或风控确认时，必须由用户本人在可见浏览器窗口中完成。

## 3. MCP 设计一致性说明

### 3.1 设计结论

当前方案符合 MCP 的设计思路。MCP Server 的职责不是把所有外部系统能力原样暴露给模型，而是将外部平台能力封装成安全、可控、结构化、可审计的工具。

本产品的 MCP 边界如下：

| 层级 | 职责 | 是否暴露给 Agent |
|---|---|---|
| MCP Client / Agent | 发起工具调用，读取结构化结果，生成分析内容 | 是 |
| MCP Tool | 暴露受控能力，如授权状态、账号摘要、报告生成 | 是 |
| Service 层 | 浏览器会话、登录状态检测、页面解析、缓存写入、报告生成 | 否 |
| Storage 层 | SQLite、本地缓存、浏览器 profile、同步日志、报告文件 | 否 |
| 抖音创作者中心 | 外部页面数据来源 | 否，必须经 Service 层访问 |

### 3.2 与 Lanhu MCP 的对照

参考同工作区 Lanhu MCP 的设计：使用 FastMCP 暴露工具，通过环境变量读取凭证，使用本地 `data` 目录缓存数据，并支持 `stdio` / `http` 两种传输方式。

抖音创作者 MCP 可复用以下设计思想：

- 使用 FastMCP 作为 MCP Server 框架。
- 工具函数只返回面向 Agent 消费的结构化结果。
- 外部平台凭证不作为工具参数传入。
- 数据和缓存默认存储在本地。
- 支持 `stdio` 作为本地 MCP Client 按需拉起方式。
- 支持 `http` 作为团队或服务化部署方式。

但抖音方案不能简单照搬 Lanhu MCP。浏览器通道包含本机登录态和风控交互，因此本产品必须强化：

- 浏览器 Cookie / profile 不进入 MCP 返回值、日志、报告和模型上下文。
- 浏览器登录、扫码、验证码、风控确认必须由用户本人在可见浏览器中完成。
- HTTP 传输模式必须增加 MCP Server 访问控制。
- 页面解析失败、登录态失效、风控拦截必须返回明确错误，不伪造数据。

## 4. 背景与问题

当前 AI Agent 若要帮助抖音创作者做账号复盘、选题分析、内容优化，通常缺少稳定、合规、结构化的数据入口。

常见问题包括：

| 问题 | 影响 |
|---|---|
| 手工截图或手动导出数据 | 效率低，数据不可持续沉淀 |
| 依赖浏览器 Cookie 或非官方接口 | 安全与合规风险高 |
| Agent 直接接触敏感凭证 | token 泄露风险高 |
| 官方接口权限复杂 | Agent 难以判断数据缺失原因 |
| 高级经营指标不一定可用 | 报告容易失败或误导用户 |

本产品通过 MCP Server 封装本地浏览器会话、登录态检测、页面解析与本地缓存，向 Agent 暴露有限、只读、可解释的数据能力。

## 5. 产品目标

### 5.1 MVP 目标

- 支持用户通过可见浏览器完成抖音创作者中心登录。
- 支持复用本地浏览器 profile 更新数据。
- 支持检测登录态是否有效。
- 支持同步用户自己可见的视频列表和可见指标。
- 支持基于本地缓存生成 Markdown 账号复盘报告。
- 支持在登录失效、需要验证、页面解析失败时清楚说明原因和下一步处理方式。

### 5.2 非目标

- 不做自动发布视频。
- 不做自动评论、私信、关注等写操作。
- 不绕过登录、验证码、扫码、风控或权限限制。
- 不向 Agent 暴露 Cookie、localStorage、sessionStorage、账号密码或验证码。
- 不读取用户在创作者中心不可见的数据。
- 不承诺首版获取完整视频经营数据、完播率、平均观看时长等高级指标，先以页面可见数据为准。

## 6. 目标用户

| 用户类型 | 核心诉求 |
|---|---|
| 个人抖音创作者 | 快速了解账号近况，获得复盘和内容建议 |
| 内容运营人员 | 管理多个账号，周期性生成数据报告 |
| AI Agent 使用者 | 让 Agent 具备合规读取抖音账号数据的能力 |
| 开发者 | 扩展抖音 OpenAPI 能力，构建私有化数据工具 |

## 7. 用户场景

### 7.1 首次授权

用户希望让 Agent 分析自己的抖音账号，但不希望把账号密码、Cookie 或 token 暴露给模型。

流程：

```text
用户调用授权工具
  -> MCP Server 生成授权 URL
  -> 用户在浏览器完成抖音授权
  -> MCP Server 接收授权结果
  -> 本地加密保存 token
  -> Agent 查询授权状态
```

### 7.2 周期复盘

用户希望 Agent 生成最近 7 天或 30 天账号复盘。

流程：

```text
Agent 调用同步工具
  -> MCP Server 检查 token 和能力状态
  -> 同步当前可用数据
  -> 写入本地缓存
  -> Agent 调用报告工具
  -> MCP Server 返回报告路径和摘要
```

### 7.3 权限不足降级

用户尚未开通视频经营数据权限，但仍希望了解账号可用信息。

系统应返回：

- 当前可用能力。
- 缺失能力。
- 缺失字段。
- 数据质量等级。
- 需要申请的 scope 或平台能力。
- 降级版报告。

## 8. 授权与安全边界

### 8.1 两类授权必须区分

本产品存在两类授权，PRD、实现和文档中必须明确区分。

| 授权类型 | 说明 | 典型凭证 | 作用范围 |
|---|---|---|---|
| MCP Server 访问授权 | MCP Client / Agent 是否能调用本 MCP Server | 本地进程权限、HTTP API Key、反向代理认证等 | 保护 MCP Server |
| 抖音 OpenAPI 授权 | 本 MCP Server 是否能代表用户访问抖音数据 | code、access_token、refresh_token | 访问抖音开放平台 |

不能把“用户已授权抖音账号”等同于“任意 MCP Client 可以调用本服务”。

### 8.2 MCP 传输模式安全要求

| 传输模式 | 使用场景 | 安全要求 |
|---|---|---|
| `stdio` | 本机 MCP Client 按需拉起，本地单用户使用 | 默认依赖本机进程边界；凭证从环境变量或本地加密存储读取 |
| `http` | 常驻服务、团队共享、远程访问 | 必须增加访问控制，如 API Key、内网限制、反向代理认证或 MCP 授权机制 |

首版建议：

- 默认推荐 `stdio`。
- `http` 只作为高级部署方式。
- HTTP 模式不能仅依赖 query 参数中的 `name`、`role` 识别用户。
- HTTP 模式下所有敏感工具必须校验调用方权限。

### 8.3 抖音 OAuth 授权模式

首版支持两种 OAuth 模式。

#### 开发模式：local_manual_code

用于本地调试或没有 HTTPS callback 的场景。

```text
Agent 调用 douyin_auth_start
  -> 返回 authorization_url、state、auth_session_id
用户打开浏览器完成授权
用户复制回调地址中的 code
Agent 调用 douyin_auth_complete(code, state)
MCP Server 换 token 并加密保存
```

限制：

- 仅建议本地开发使用。
- `code` 可能进入模型上下文，因此正式环境不推荐。
- 工具返回和日志仍必须避免记录 `code`。

#### 正式模式：https_callback

用于正式部署。

```text
Agent 调用 douyin_auth_start
  -> 返回 authorization_url、auth_session_id
用户打开浏览器完成授权
抖音回调 HTTPS redirect_uri?code=xxx&state=yyy
Callback 服务接收 code
AuthService 换 token 并加密保存
Agent 调用 douyin_auth_status(auth_session_id)
  -> 查询授权是否完成
```

正式模式要求：

- `redirect_uri` 必须与抖音开放平台应用后台配置一致。
- 必须使用 HTTPS callback。
- 必须校验 `state`。
- OAuth `code` 不应由 Agent 转交。
- Agent 只查询授权状态，不接触 `code`、`access_token`、`refresh_token`。

## 9. Token 安全管理

### 9.1 为什么需要加密保存 token

Token 不是因为会上传云端才需要加密。按产品默认设计，token 不上传云端，只保存在本地或私有化部署环境中。

需要加密保存的原因是：token 本身就是访问抖音账号数据的敏感凭证。如果 `access_token` 或 `refresh_token` 泄露，攻击者可能在有效期内调用开放平台接口读取账号数据，甚至通过 refresh token 续期访问能力。

需要防范的场景包括：

- 本地 SQLite 数据库被误传、备份、拷贝或提交到仓库。
- 本机其他进程、插件、脚本或恶意程序读取数据文件。
- 服务端部署时数据库备份泄露。
- 开发调试时对象被打印到日志。
- Agent 或模型上下文意外拿到敏感字段。

### 9.2 Token 存储要求

- `access_token` 和 `refresh_token` 必须加密后写入 SQLite。
- `client_secret` 不落库，只来自环境变量或系统密钥管理。
- MCP tool 返回值不能包含 token、code、client_secret。
- 日志只记录账号 ID、接口名、状态码、错误类型、耗时。
- 调试模式也不能打印 token 明文。

### 9.3 Token 刷新逻辑

```text
TokenStore.get_valid_token(account_id)
  |
  |-- access_token 未过期：返回解密后的短期 token，仅供服务层内部使用
  |-- access_token 即将过期：调用 /oauth/refresh_token/
  |-- refresh_token 需要续期：按权限调用 /oauth/renew_refresh_token/
  |-- 刷新失败：返回 authorization_expired
```

## 10. 核心功能需求

### 10.1 授权工具

#### `douyin_auth_start(scopes: list[str] | None = None)`

生成抖音授权 URL、`state`、`auth_session_id` 和授权说明。

返回要求：

- 返回 `authorization_url`。
- 返回 `auth_session_id`。
- 返回本次申请的 scopes。
- 不返回任何 secret。

#### `douyin_auth_complete(code: str, state: str | None = None)`

仅用于 `local_manual_code` 开发模式，手动提交授权 code，完成 token 交换。

返回要求：

- 成功时返回授权账号摘要。
- 失败时返回标准错误。
- 正式模式下应拒绝调用或提示使用 callback。

#### `douyin_auth_status(auth_session_id: str | None = None)`

查询授权状态、账号列表、scope、token 过期状态和能力状态摘要。

返回要求：

- 不返回 token。
- 支持查询指定授权会话状态。
- 支持返回是否需要重新授权。

### 10.2 账号工具

#### `douyin_list_accounts()`

列出本地已授权账号。

#### `douyin_get_account_profile(account_id: str)`

调用官方用户信息接口，获取账号基础资料并写入缓存。

### 10.3 能力探测工具

#### `douyin_check_capabilities(account_id: str)`

检查当前账号和应用已具备哪些接口能力。

能力状态：

| 状态 | 说明 |
|---|---|
| `available` | 已确认可用 |
| `missing` | 已确认缺失权限或能力 |
| `unknown` | 未确认，需调用或后台确认 |
| `limited` | 可用但有条件限制 |

### 10.4 数据同步工具

#### `douyin_sync_available_data(account_id: str)`

根据能力探测结果同步所有可用数据。

同步原则：

- Agent 不逐页调用外部 API。
- 分页、限流、重试由 MCP Server 内部处理。
- 每次同步生成 `sync_jobs` 记录。
- 部分数据缺失不阻断整体同步。

### 10.5 汇总与报告工具

#### `douyin_get_account_summary(account_id: str, start_date: str | None = None, end_date: str | None = None)`

从本地缓存汇总账号周期数据。

#### `douyin_generate_creator_report(account_id: str, period: str = "7d")`

生成 Markdown 账号复盘报告。

报告必须包含：

- 数据来源。
- 数据时间范围。
- 已使用的官方接口。
- 缺失指标说明。
- 数据质量等级。
- 账号表现总结。
- 内容建议。
- 下一步需要申请的 scope 或能力。

## 11. 权限确认后扩展工具

以下工具只有在对应能力确认可用后才启用，不能伪实现。

| 工具 | 前提 |
|---|---|
| `douyin_sync_videos` | 视频列表或经营数据能力可用 |
| `douyin_list_videos` | 视频基础数据已同步 |
| `douyin_get_video_metrics` | 视频指标能力可用 |
| `douyin_sync_fans` | 粉丝数据能力可用 |
| `douyin_get_fans_trend` | 粉丝趋势能力可用 |
| `douyin_compare_videos` | 多视频指标数据可用 |

权限缺失时返回：

```json
{
  "status": "error",
  "error_type": "capability_missing",
  "message": "当前账号或应用暂未开通 video.data 能力，请先在抖音开放平台完成权限申请和授权。",
  "retryable": false,
  "required_capability": "video.data"
}
```

## 12. API 调用链路

```text
MCP tool
  |
  v
Service
  |
  v
CapabilityService.ensure(scope/capability)
  |
  v
DouyinApiClient.request(api_key, account_id, payload)
  |
  v
TokenStore.get_valid_token(account_id)
  |
  v
按 docs/api-mapping.md 的接口配置组装请求
  |
  v
httpx 调用抖音 OpenAPI
  |
  v
统一错误处理、限流退避、响应标准化
  |
  v
写入本地缓存并返回 MCP 结构化结果
```

接口配置必须维护在 `docs/api-mapping.md`，不能在工具函数中散落硬编码。

配置项至少包括：

- API key。
- 官方路径。
- HTTP method。
- 所需 scope。
- 鉴权字段位置。
- 请求参数位置。
- 分页方式。
- 限流策略。
- 响应字段映射。
- 可用性限制。

## 13. 数据库设计

首版建议使用 SQLite。

核心表：

| 表名 | 说明 |
|---|---|
| `accounts` | 本地授权账号 |
| `tokens` | 加密 token |
| `oauth_states` | OAuth state 和授权会话 |
| `api_capabilities` | 账号能力状态 |
| `videos` | 视频基础信息 |
| `video_metrics` | 视频指标快照 |
| `sync_jobs` | 同步任务记录 |
| `reports` | 生成报告记录 |
| `audit_logs` | 审计日志 |

`oauth_states` 建议增加 `auth_session_id`，用于正式 callback 模式下让 Agent 查询授权状态。

## 14. 返回格式规范

成功返回示例：

```json
{
  "status": "success",
  "account": {
    "id": "local_account_id",
    "nickname": "账号名"
  },
  "capabilities": {
    "user_info": "available",
    "fans.data": "missing",
    "video.data": "unknown"
  },
  "date_range": {
    "start": "2026-07-01",
    "end": "2026-07-06"
  },
  "summary": {
    "video_count": 12,
    "total_play": null,
    "total_like": 5200
  },
  "analysis_notes": [
    "已完成账号基础信息同步。",
    "播放量字段暂不可用，原因是当前应用未确认 video.data 能力。"
  ],
  "data_quality": {
    "level": "partial",
    "missing_fields": ["play_count", "complete_rate", "avg_watch_duration"]
  }
}
```

错误返回示例：

```json
{
  "status": "error",
  "error_type": "authorization_expired",
  "message": "授权已过期，请重新调用 douyin_auth_start 完成授权。",
  "retryable": true
}
```

统一错误类型：

| 错误类型 | 说明 |
|---|---|
| `authorization_required` | 尚未授权 |
| `authorization_expired` | 授权已过期 |
| `capability_missing` | 能力缺失 |
| `scope_missing` | scope 缺失 |
| `mcp_access_denied` | MCP Server 调用方无权限 |
| `api_rate_limited` | 抖音 OpenAPI 限流 |
| `api_error` | 官方 API 返回错误 |
| `network_error` | 网络错误 |
| `invalid_response` | 响应结构异常 |
| `data_not_available` | 数据暂不可用 |

## 15. 数据同步策略

- 首次同步：先同步账号资料和能力状态。
- 权限可用时：同步粉丝数据、视频基础信息或经营指标。
- 增量同步：按最近发布时间、接口游标或最近一次同步时间窗口同步。
- 指标同步：按天落库，保留历史快照。
- 限流处理：统一指数退避，记录失败任务。
- 数据延迟：报告中标注数据截止时间和可能的 T+1 延迟。
- 权限降级：没有权限时不阻塞整个报告，只降低数据质量等级。

## 16. 报告生成策略

报告分三档：

| 报告类型 | 数据范围 |
|---|---|
| 完整报告 | 账号信息 + 视频列表 + 视频指标 + 粉丝趋势 + 粉丝画像 |
| 标准报告 | 账号信息 + 可用视频基础信息 + 部分互动指标 |
| 降级报告 | 账号信息 + 授权状态 + 能力缺口 + 后续接入建议 |

报告必须避免把“无数据”误写成“表现不好”。当数据缺失由权限、接口限制或同步失败导致时，必须明确标注原因。

## 17. 安全与合规边界

必须遵守：

- 使用抖音开放平台 OAuth 授权。
- 不保存用户账号密码。
- 不读取浏览器 Cookie 作为认证方式。
- 不模拟创作者中心页面。
- 不绕过验证码、风控、签名或私有接口。
- 不向 Agent 暴露 token。
- 默认只做读取型工具。
- 发布视频、删除视频、发评论、私信等写操作不纳入 MVP。
- 所有敏感字段默认脱敏。
- 同步日志不记录 token、code、隐私内容。
- HTTP 模式必须具备 MCP Server 访问控制。

## 18. MVP 实施计划

### 阶段 0：官方能力确认

- 创建 `docs/api-mapping.md`。
- 按官方文档整理接口、scope、请求方式、限制条件。
- 在抖音开放平台应用后台确认已申请权限。
- 明确本地开发 OAuth 模式和正式 HTTPS callback 模式。

### 阶段 1：服务骨架

- 初始化 Python 项目。
- 初始化 FastMCP。
- 默认支持 `MCP_TRANSPORT=stdio`。
- 可选支持 `MCP_TRANSPORT=http`。
- 加载 `.env`。
- 初始化 `DATA_DIR`。
- 初始化 SQLite。

### 阶段 2：授权闭环

- 实现 `douyin_auth_start`。
- 实现 `auth_session_id`。
- 实现 OAuth state 存储与校验。
- 实现开发模式 `douyin_auth_complete`。
- 实现正式模式 HTTPS callback 处理。
- 实现 code 换 token。
- 实现 token 加密存储。
- 实现 token 自动刷新。
- 实现 `douyin_auth_status`。

### 阶段 3：MCP 访问控制

- stdio 模式读取本地环境配置。
- HTTP 模式增加访问控制。
- 敏感工具校验调用方权限。
- 增加 `mcp_access_denied` 错误类型。

### 阶段 4：账号与能力探测

- 实现 `douyin_get_account_profile`。
- 实现 `douyin_list_accounts`。
- 实现 `douyin_check_capabilities`。
- 将账号信息和能力状态写入 SQLite。

### 阶段 5：可用数据同步

- 实现 `douyin_sync_available_data`。
- 对每个可用接口做分页、重试、错误标准化。
- 不可用能力写入 `api_capabilities`，供报告解释。

### 阶段 6：报告与 Agent 集成

- 实现 `douyin_get_account_summary`。
- 实现 `douyin_generate_creator_report`。
- 写 MCP 配置示例。
- 验证 Agent 可调用工具。
- 验证 token 不出现在模型上下文和日志中。

### 阶段 7：增强能力

- 在权限审批通过后启用视频数据、粉丝数据、趋势分析。
- 实现 `douyin_sync_videos`、`douyin_get_video_metrics`、`douyin_get_fans_trend`。
- 支持多账号横向对比。

## 19. 首版验收标准

首版完成时应满足：

- 能生成抖音授权 URL。
- 能生成并查询 `auth_session_id`。
- 能通过开发模式手动 code 完成授权。
- 能通过正式 HTTPS callback 完成授权。
- 能加密保存并刷新 token。
- 能获取用户公开信息。
- 能列出本地已授权账号。
- 能检查并返回当前账号能力状态。
- 能在权限不足时返回结构化错误。
- 能生成一份带数据质量说明的 Markdown 报告。
- stdio 模式可被本地 MCP Client 按需拉起。
- HTTP 模式具备访问控制。
- 日志和 MCP 返回值中不包含 token、code、client_secret。

## 20. 关键风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 官方接口权限不稳定 | 部分指标无法获取 | 先做能力探测和降级报告 |
| 本地 OAuth 回调不可用于正式环境 | 授权链路受阻 | 支持手动 code 开发模式和 HTTPS callback 正式模式 |
| Agent 暴露敏感 code/token | 安全风险高 | 正式模式中 Agent 不接触 code/token |
| HTTP MCP Server 被未授权调用 | 数据泄露 | HTTP 模式强制访问控制 |
| 视频经营数据不可用 | 报告价值下降 | 数据质量说明 + 权限补齐建议 |
| 接口请求方式差异大 | API 调用失败 | 使用 `docs/api-mapping.md` 统一维护接口配置 |

## 21. 后续扩展

可扩展能力：

- 粉丝画像分析。
- 视频选题聚类。
- 爆款视频因子分析。
- 账号日报 / 周报自动生成。
- 多账号横向对比。
- 团队运营备注与知识库。
- 与飞书 / 企业微信通知集成。
- 与本地内容选题库联动。
- 用户手动导入经营数据作为官方 API 不足时的补充数据源。

不建议早期扩展：

- 自动发布视频。
- 自动评论或私信。
- 使用非官方接口采集数据。
- 依赖浏览器登录态模拟创作者中心页面。
- 绕过开放平台权限限制获取经营指标。

## 22. 最终建议

推荐按以下路线落地：

```text
第一步：stdio 本地 MCP + OAuth 授权 + Token 安全
第二步：用户信息 + 能力探测 + 本地缓存
第三步：可用数据同步 + 降级报告
第四步：HTTP 部署访问控制 + 正式 HTTPS callback
第五步：申请并验证 video.data / fans.data 等增强能力
第六步：扩展视频指标、粉丝趋势、多账号分析
```

该路线符合 MCP 的工具封装思想，也能避免项目因为某个高级数据接口暂时不可用而停摆，同时保持合规、安全和可演进。
