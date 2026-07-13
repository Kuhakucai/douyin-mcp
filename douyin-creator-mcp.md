# 抖音创作者 MCP 实施方案（浏览器登录态版）

> 修订依据：以抖音开放平台官方 OpenAPI 文档为主，第三方示例和 SDK 只作为辅助参考。  
> 修订日期：2026-07-11
> 修订说明：明确浏览器登录态通道为唯一当前主线；官方 OpenAPI、小程序基础能力、手动导入均不作为当前 MVP 方向。

## 1. 结论

这个项目可以做，但当前不能继续押注官方 OpenAPI 通道。首版应按“浏览器登录态通道”收敛：

```text
浏览器登录态通道：个人创作者本地使用，复用本机浏览器 profile 读取自己可见的数据
```

原方案中的 MCP 架构、FastMCP、SQLite、本地缓存、只读型工具边界都可行。需要修正的是：不要继续把官方 OpenAPI 作为当前 MVP 主线。

补充判断：该方案符合 MCP 的设计思路。MCP Server 不应把抖音平台能力和敏感凭证直接暴露给 Agent，而应作为受控的数据连接器，负责授权、API 调用、缓存、脱敏、错误标准化和工具化返回。

推荐把项目定位为：

```text
抖音创作者本地数据 MCP
  |
  |-- 浏览器登录态通道
  |-- 本地浏览器 profile 与数据缓存
  |-- 登录态检测与页面解析
  |-- 账号复盘与内容分析报告
```

首版让 Agent 稳定完成：

```text
打开可见浏览器 -> 用户完成登录/验证 -> 复用本地 profile -> 同步创作者中心可见视频数据 -> 本地缓存 -> 生成复盘
```

## 1.1 当前实现状态

浏览器登录态 MVP 已形成完整数据闭环：

```text
扫码登录 -> 持久化 profile -> 全量滚动作品列表 -> DOM 结构化解析
-> 幂等写入 videos / video_metrics -> Agent 分页读取 -> 生成快照报告
```

当前已支持页面可见的标题、发布时间、时长、封面、播放、点赞、评论、分享和收藏。真实账号已验证声明数、加载数和解析数一致，重复同步不会增加视频或同日指标记录；真实数量不进入仓库。

Agent 主要使用：

- `douyin_browser_sync_creator_data`
- `douyin_browser_list_videos`
- `douyin_browser_refresh_report`

页面没有公开稳定作品 ID，因此当前使用确定性本地哈希 ID。页面未展示的指标不填充、不推测。

## 1.2 当前方向边界

| 方向 | 结论 | 说明 |
|---|---|---|
| 浏览器登录态 | 当前唯一主线 | 复用本机 profile，读取用户自己可见的创作者中心数据 |
| 官方 OpenAPI | 当前否决 | 个人主体无法创建网站应用，短期无法接入视频数据 |
| 小程序基础能力 | 当前否决 | 无法提供创作者本人视频经营数据 |
| 手动导入 | 当前否决 | 不符合 MCP 自动更新目标 |

浏览器登录态通道不应绕过登录、验证码或风控。它只读取用户在本机可见页面中的数据；如果登录失效或需要验证，必须打开可见浏览器让用户处理。

## 2. 历史方案说明

以下 OAuth、OpenAPI、API Client 等内容属于历史方案记录，当前 MVP 不再沿这些方向开发。后续开发应以 `docs/agent-handoff.md` 和 `docs/browser-session.md` 为准，优先实现浏览器登录态通道。

## 3. 关键历史修订点

### 3.1 OAuth 回调不能只依赖本地 HTTP

原方案使用：

```env
DOUYIN_REDIRECT_URI=http://127.0.0.1:8787/oauth/callback
```

这适合作为本地调试设想，但不应作为正式方案。抖音开放平台 Web OAuth 的 `redirect_uri` 要和应用后台配置一致，正式接入通常需要 HTTPS 回调地址。

修订后提供两种模式：

```text
正式模式：
  用户配置 HTTPS 回调地址，例如 https://your-domain.com/oauth/douyin/callback

本地开发模式：
  生成授权 URL -> 用户浏览器授权 -> 用户复制 code -> 调用 douyin_auth_complete(code)
```

这样可以避免在 MVP 阶段卡死在本地回调地址限制上。

### 3.2 视频列表和视频指标要做能力探测

原方案默认存在：

```text
video.list
douyin_sync_videos
douyin_get_video_metrics
```

但实际落地时，视频列表、视频经营数据、播放量、完播率、平均观看时长等能力可能依赖更高等级权限、数据经营能力授权、PC 端授权或特定接口条件。

修订策略：

- 不在 MCP 层硬编码未经验证的 `video.list` 路径。
- 新增 `douyin_check_capabilities`，先检查当前应用配置和账号授权具备哪些能力。
- `douyin_sync_videos` 只同步“当前已确认可用接口”能返回的数据。
- `video_metrics` 表保留，但字段来源必须记录 `source` 和 `capability_key`。
- 如果视频指标 API 不可用，报告工具基于已有缓存、账号信息、粉丝数据或用户导入数据生成降级版报告。

### 3.3 API Client 必须按接口配置请求方式

原方案示例里统一把 `access_token` 和 `open_id` 放入 query params：

```python
params.setdefault("access_token", token["access_token"])
params.setdefault("open_id", token["open_id"])
```

这不够稳。抖音 OpenAPI 不同接口可能使用 query、form、json body 或 header，例如有的接口使用 `access-token` header。

修订后 `DouyinApiClient` 应使用接口映射配置：

```python
{
  "name": "get_user_info",
  "method": "GET",
  "path": "/oauth/userinfo/",
  "auth": {
    "access_token": "query",
    "open_id": "query"
  },
  "scope": "user_info"
}
```

每个接口的 path、method、scope、鉴权位置、分页方式、限流策略都写在 `docs/api-mapping.md`。

### 3.4 Python 直接调用 REST API

官方 SDK 主要覆盖 Java、Node.js、Go。Python 版 MCP 建议直接用 `httpx` 调官方 REST API，不强依赖第三方 SDK。

## 3. 官方 API 能力映射

以下是当前项目优先关注的官方能力。具体字段、scope、审核条件以抖音开放平台最新文档和应用后台为准。

| 能力 | 官方接口/路径 | 用途 | MVP 状态 |
|---|---|---|---|
| 获取授权码 | `/platform/oauth/connect/` | 生成用户授权 URL | 必做 |
| 换取 access token | `/oauth/access_token/` | code 换 token | 必做 |
| 刷新 access token | `/oauth/refresh_token/` | access token 过期前刷新 | 必做 |
| 刷新 refresh token | `/oauth/renew_refresh_token/` | 延长 refresh token 生命周期 | 建议做 |
| 获取用户公开信息 | `/oauth/userinfo/` | 账号昵称、头像、open_id 等 | 必做 |
| 粉丝列表 | `/fans/list/` | 查询粉丝列表 | 权限确认后做 |
| 粉丝画像数据 | `/api/douyin/v1/user/fans_data/` | 粉丝画像与趋势分析 | 权限确认后做 |
| 粉丝来源 | `/data/extern/fans/source/` | 粉丝来源分析 | 权限确认后做 |
| 视频基础信息 | `/api/douyin/v1/video/video_basic_info/` | 查询已知视频 ID 的基础信息 | 可选 |
| 视频搜索 | `/dy_open_api/v2/search/video/` | 搜索公开视频 | 非首版核心 |
| 数据经营能力 | `video.list` / `video.data` / `fans.data` 等 | 创作者经营数据 | 作为增强能力验证 |

注意：

- 粉丝画像类能力可能有粉丝数、授权时间、权限审核等限制。
- 视频基础信息接口通常需要已知 `item_id` 或 `video_id`，不能假设它等价于“拉取账号全部作品列表”。
- 数据经营能力如果需要 PC 端授权或额外审批，MCP 工具要返回清晰的 `capability_missing` 错误，而不是伪装成同步失败。

## 4. 总体架构

```text
本地 Agent / MCP Client
  |
  | stdio / http
  v
douyin-creator-mcp
  |
  | MCP tools
  v
业务服务层
  |-- AuthService
  |-- CapabilityService
  |-- DouyinApiClient
  |-- SyncService
  |-- MetricService
  |-- ReportService
  |
  v
抖音开放平台 OpenAPI
  |
  v
本地安全存储
  |-- SQLite
  |-- encrypted token store
  |-- api capability cache
  |-- sync logs
  |-- generated reports
```

核心原则：

- Agent 不直接接触 `client_secret`、`access_token`、`refresh_token`。
- MCP 工具只暴露受控的只读能力。
- OAuth、Token 刷新、错误处理、限流重试全部封装在服务层。
- 所有外部数据先进入本地缓存，再由 Agent 查询和分析。
- 所有接口都通过 `docs/api-mapping.md` 维护，禁止在工具函数里散落硬编码。
- 对权限缺失、接口不可用、数据延迟做明确降级。

### 4.1 MCP 设计边界

需要明确区分两类授权：

| 授权类型 | 说明 | 典型凭证 | 作用范围 |
|---|---|---|---|
| MCP Server 访问授权 | MCP Client / Agent 是否能调用本 MCP Server | 本地进程权限、HTTP API Key、反向代理认证等 | 保护 MCP Server |
| 抖音 OpenAPI 授权 | 本 MCP Server 是否能代表用户访问抖音数据 | code、access_token、refresh_token | 访问抖音开放平台 |

不能把“用户已授权抖音账号”等同于“任意 MCP Client 都可以调用本服务”。

### 4.2 MCP 传输模式要求

| 传输模式 | 使用场景 | 安全要求 |
|---|---|---|
| `stdio` | 本机 MCP Client 按需拉起，本地单用户使用 | 默认依赖本机进程边界；凭证从环境变量或本地加密存储读取 |
| `http` | 常驻服务、团队共享、远程访问 | 必须增加访问控制，如 API Key、内网限制、反向代理认证或 MCP 授权机制 |

首版建议默认使用 `stdio`。`http` 模式只能作为高级部署方式，不能仅依赖 query 参数中的 `name`、`role` 识别用户。

## 5. 推荐项目结构

```text
douyin-creator-mcp/
  ├─ pyproject.toml
  ├─ README.md
  ├─ .env.example
  ├─ docs/
  │   ├─ install.md
  │   ├─ oauth.md
  │   ├─ scopes.md
  │   ├─ api-mapping.md
  │   └─ limitations.md
  ├─ src/
  │   └─ douyin_creator_mcp/
  │       ├─ server.py
  │       ├─ config.py
  │       ├─ tools/
  │       │   ├─ auth_tools.py
  │       │   ├─ account_tools.py
  │       │   ├─ sync_tools.py
  │       │   └─ report_tools.py
  │       ├─ services/
  │       │   ├─ auth_service.py
  │       │   ├─ capability_service.py
  │       │   ├─ douyin_api.py
  │       │   ├─ sync_service.py
  │       │   ├─ metric_service.py
  │       │   └─ report_service.py
  │       └─ storage/
  │           ├─ db.py
  │           ├─ token_store.py
  │           └─ schemas.sql
  ├─ data/
  │   ├─ douyin.sqlite
  │   ├─ logs/
  │   └─ reports/
  └─ tests/
      ├─ test_auth_service.py
      ├─ test_token_store.py
      └─ test_report_service.py
```

如果要极快验证，也可以先做单文件版 `douyin_mcp_server.py`。但正式项目建议直接拆分，因为 OAuth、Token、能力探测和报告逻辑的边界比较敏感。

## 6. 配置设计

`.env.example`：

```env
MCP_TRANSPORT=stdio
DATA_DIR=./data
LOG_LEVEL=INFO

# http 模式下必须配置访问控制；stdio 模式可留空
MCP_HTTP_API_KEY=

DOUYIN_CLIENT_KEY=your_client_key
DOUYIN_CLIENT_SECRET=your_client_secret
DOUYIN_REDIRECT_URI=https://your-domain.com/oauth/douyin/callback
DOUYIN_SCOPES=user_info

# local_manual_code: 用户授权后手动粘贴 code
# https_callback: 使用正式 HTTPS 回调地址
DOUYIN_OAUTH_MODE=local_manual_code

TOKEN_ENCRYPTION_KEY=generate_a_local_random_key
HTTP_TIMEOUT_SECONDS=20
SYNC_PAGE_SIZE=20
API_MAPPING_FILE=./docs/api-mapping.md
```

本地 Agent MCP 配置示例：

```toml
[mcp_servers.douyin_creator]
command = "python"
args = ["-m", "douyin_creator_mcp.server"]

[mcp_servers.douyin_creator.env]
MCP_TRANSPORT = "stdio"
DATA_DIR = "D:\\AIProject\\AI项目\\douyin-mcp\\data"
DOUYIN_CLIENT_KEY = "your_client_key"
DOUYIN_CLIENT_SECRET = "your_client_secret"
DOUYIN_REDIRECT_URI = "https://your-domain.com/oauth/douyin/callback"
DOUYIN_SCOPES = "user_info"
DOUYIN_OAUTH_MODE = "local_manual_code"
TOKEN_ENCRYPTION_KEY = "your_local_encryption_key"
```

## 7. OAuth 授权流程

### 7.1 本地开发模式

```text
Agent 调用 douyin_auth_start
  |
  v
MCP 生成授权 URL、state 和 auth_session_id
  |
  v
用户在浏览器打开 URL 并完成授权
  |
  v
用户复制回调地址中的 code
  |
  v
Agent 调用 douyin_auth_complete(code, state)
  |
  v
AuthService 使用 code 换取 token
  |
  v
TokenStore 加密保存 token
  |
  v
Agent 调用 douyin_auth_status 验证授权状态
```

限制：

- 该模式仅建议本地开发使用。
- OAuth `code` 可能进入模型上下文，因此正式环境不推荐。
- 即使是开发模式，工具返回值和日志也不能记录 `code`。

### 7.2 正式 HTTPS 回调模式

```text
Agent 调用 douyin_auth_start
  |
  v
MCP 生成授权 URL、state 和 auth_session_id
  |
  v
用户浏览器授权
  |
  v
抖音回调 HTTPS redirect_uri?code=xxx&state=yyy
  |
  v
Callback 服务通知本地 MCP 或写入安全队列
  |
  v
AuthService 换 token 并加密保存
  |
  v
Agent 调用 douyin_auth_status(auth_session_id) 查询授权结果
```

安全要求：

- 必须校验 `state`，防止 CSRF。
- `client_secret` 只在服务端使用，不能暴露给 Agent。
- 正式模式下 OAuth `code` 不应由 Agent 转交，Agent 只查询授权状态。
- 日志中禁止记录 `code`、`access_token`、`refresh_token`。
- `refresh_token` 失效时返回 `authorization_expired`，引导用户重新授权。

## 8. Token 管理

Token 管理独立成 `TokenStore`。

Token 不是因为会上传云端才需要加密。按默认设计，token 不上传云端，只保存在本地或私有化部署环境中。

需要加密保存的原因是：token 本身就是访问抖音账号数据的敏感凭证。如果 `access_token` 或 `refresh_token` 泄露，攻击者可能在有效期内调用开放平台接口读取账号数据，甚至通过 refresh token 续期访问能力。

需要防范的场景包括：

- 本地 SQLite 数据库被误传、备份、拷贝或提交到仓库。
- 本机其他进程、插件、脚本或恶意程序读取数据文件。
- 服务端部署时数据库备份泄露。
- 开发调试时对象被打印到日志。
- Agent 或模型上下文意外拿到敏感字段。

```text
TokenStore.get_valid_token(account_id)
  |
  |-- access_token 未过期：返回解密后的短期 token
  |-- access_token 即将过期：调用 /oauth/refresh_token/
  |-- refresh_token 也需要续期：按权限调用 /oauth/renew_refresh_token/
  |-- 刷新失败：返回 authorization_expired
```

存储要求：

- `access_token` 和 `refresh_token` 加密后写入 SQLite。
- `client_secret` 不落库，只来自环境变量或系统密钥管理。
- 所有 MCP tool 返回值都不能包含 token。
- 审计日志只记录脱敏账号、接口名、状态码、错误类型、耗时。

## 9. API 调用链路

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

错误类型建议统一为：

```text
authorization_required
authorization_expired
capability_missing
scope_missing
mcp_access_denied
api_rate_limited
api_error
network_error
invalid_response
data_not_available
```

## 10. 数据库设计

建议使用 SQLite。

```sql
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY,
  open_id TEXT NOT NULL UNIQUE,
  nickname TEXT,
  avatar TEXT,
  authorized_scopes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
  account_id TEXT PRIMARY KEY,
  access_token_encrypted TEXT NOT NULL,
  refresh_token_encrypted TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  refresh_expires_at INTEGER,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_states (
  state TEXT PRIMARY KEY,
  auth_session_id TEXT NOT NULL UNIQUE,
  redirect_uri TEXT NOT NULL,
  scopes TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  consumed_at TEXT
);

CREATE TABLE IF NOT EXISTS api_capabilities (
  id TEXT PRIMARY KEY,
  account_id TEXT,
  capability_key TEXT NOT NULL,
  scope TEXT,
  status TEXT NOT NULL,
  last_checked_at TEXT NOT NULL,
  detail_json TEXT
);

CREATE TABLE IF NOT EXISTS videos (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  item_id TEXT,
  video_id TEXT,
  title TEXT,
  publish_time INTEGER,
  cover_url TEXT,
  video_url TEXT,
  duration INTEGER,
  source TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS video_metrics (
  id TEXT PRIMARY KEY,
  video_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  metric_date TEXT NOT NULL,
  play_count INTEGER,
  like_count INTEGER,
  comment_count INTEGER,
  share_count INTEGER,
  collect_count INTEGER,
  complete_rate REAL,
  avg_watch_duration REAL,
  follower_gain INTEGER,
  source TEXT NOT NULL,
  capability_key TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_jobs (
  id TEXT PRIMARY KEY,
  account_id TEXT,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  error_type TEXT,
  error_message TEXT
);

CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  period TEXT NOT NULL,
  date_start TEXT,
  date_end TEXT,
  report_path TEXT,
  summary_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id TEXT PRIMARY KEY,
  account_id TEXT,
  action TEXT NOT NULL,
  status TEXT NOT NULL,
  detail_json TEXT,
  created_at TEXT NOT NULL
);
```

## 11. MCP 工具清单

### 11.1 首版必做工具

```python
douyin_auth_start(scopes: list[str] | None = None)
```

生成授权 URL、`state`、`auth_session_id`、授权说明。

```python
douyin_auth_complete(code: str, state: str | None = None)
```

本地开发模式下手动提交授权 code，完成 token 交换。正式 HTTPS 回调模式不应依赖该工具传递 `code`。

```python
douyin_auth_status(auth_session_id: str | None = None)
```

返回指定授权会话或本地账号的授权状态、账号列表、scope、过期时间、能力状态摘要。

```python
douyin_list_accounts()
```

列出本地已授权账号，不返回任何 token。

```python
douyin_check_capabilities(account_id: str)
```

检查当前账号和应用已具备哪些接口能力。

```python
douyin_get_account_profile(account_id: str)
```

调用官方用户信息接口，获取账号基础资料并写入缓存。

```python
douyin_sync_available_data(account_id: str)
```

根据能力探测结果同步所有可用数据，例如账号信息、粉丝数据、已知视频基础信息。

```python
douyin_get_account_summary(account_id: str, start_date: str | None = None, end_date: str | None = None)
```

从本地缓存汇总账号周期数据。

```python
douyin_generate_creator_report(account_id: str, period: str = "7d")
```

生成 Markdown 账号复盘报告。数据不足时必须标注“哪些指标缺失、为什么缺失、如何补齐权限”。

### 11.2 权限确认后扩展工具

```python
douyin_sync_videos(account_id: str, start_date: str | None = None, end_date: str | None = None)
douyin_list_videos(account_id: str, start_date: str | None = None, end_date: str | None = None)
douyin_get_video_metrics(account_id: str, video_id: str)
douyin_sync_fans(account_id: str, date_range: str)
douyin_get_fans_trend(account_id: str, start_date: str, end_date: str)
douyin_compare_videos(account_id: str, video_ids: list[str])
```

这些工具不应在未确认权限时伪实现。未开通时返回：

```json
{
  "status": "error",
  "error_type": "capability_missing",
  "message": "当前账号或应用暂未开通 video.data 能力，请先在抖音开放平台完成权限申请和授权。",
  "retryable": false,
  "required_capability": "video.data"
}
```

## 12. 返回格式规范

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

## 13. 同步策略

同步不应让 Agent 逐页调用 API，而应由 MCP Server 内部完成。

策略：

- 首次同步：先同步账号资料和能力状态。
- 权限可用时：同步粉丝数据、视频基础信息或数据经营指标。
- 增量同步：按最近发布时间、接口游标或最近一次同步时间窗口同步。
- 指标同步：按天落库，保留历史快照。
- 限流处理：统一指数退避，记录失败任务。
- 数据延迟：报告中标注数据截止时间和可能的 T+1 延迟。
- 权限降级：没有权限时不阻塞整个报告，只降低数据质量等级。

## 14. 报告生成策略

报告分三档：

```text
完整报告：
  账号信息 + 视频列表 + 视频指标 + 粉丝趋势 + 粉丝画像

标准报告：
  账号信息 + 可用视频基础信息 + 部分互动指标

降级报告：
  账号信息 + 授权状态 + 能力缺口 + 后续接入建议
```

报告必须包含：

- 数据来源。
- 数据时间范围。
- 已使用的官方接口。
- 缺失指标说明。
- 账号表现总结。
- 内容建议。
- 下一步需要申请的 scope 或能力。

## 15. 安全与合规边界

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
- HTTP 模式必须具备 MCP Server 访问控制，不能仅依赖 URL 参数识别调用方。
- 正式 OAuth 模式下，Agent 不应接触 `code`，只通过 `auth_session_id` 查询授权结果。

## 16. MVP 实施计划

### 阶段 0：官方能力确认

- 创建 `docs/api-mapping.md`。
- 按官方文档整理接口、scope、请求方式、限制条件。
- 在抖音开放平台应用后台确认已申请权限。
- 明确本地开发 OAuth 模式和正式 HTTPS 回调模式。

### 阶段 1：服务骨架

- 初始化 Python 项目。
- 初始化 FastMCP。
- 默认支持 `MCP_TRANSPORT=stdio`。
- 可选支持 `MCP_TRANSPORT=http`，并实现访问控制。
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

### 阶段 3：账号与能力探测

- 实现 `douyin_get_account_profile`。
- 实现 `douyin_list_accounts`。
- 实现 `douyin_check_capabilities`。
- 将账号信息和能力状态写入 SQLite。

### 阶段 4：可用数据同步

- 实现 `douyin_sync_available_data`。
- 对每个可用接口做分页、重试、错误标准化。
- 不可用能力写入 `api_capabilities`，供报告解释。

### 阶段 5：报告与 Agent 集成

- 实现 `douyin_get_account_summary`。
- 实现 `douyin_generate_creator_report`。
- 写 MCP 配置示例。
- 验证 Agent 可调用工具。
- 验证 token 不出现在模型上下文和日志中。

### 阶段 6：增强能力

- 在权限审批通过后启用视频数据、粉丝数据、趋势分析。
- 实现 `douyin_sync_videos`、`douyin_get_video_metrics`、`douyin_get_fans_trend`。
- 支持多账号横向对比。

## 17. 首版验收标准

首版完成时应满足：

- 能生成抖音授权 URL。
- 能生成并查询 `auth_session_id`。
- 能通过手动 code 或 HTTPS callback 完成授权。
- 能加密保存并刷新 token。
- 能获取用户公开信息。
- 能列出本地已授权账号。
- 能检查并返回当前账号能力状态。
- 能在权限不足时返回结构化错误。
- 能生成一份带数据质量说明的 Markdown 报告。
- stdio 模式可被本地 MCP Client 按需拉起。
- HTTP 模式具备访问控制。
- 日志和 MCP 返回值中不包含 token、code、client_secret。

## 18. 后续扩展

可扩展能力：

- 粉丝画像分析。
- 视频选题聚类。
- 爆款视频因子分析。
- 账号日报/周报自动生成。
- 多账号横向对比。
- 团队运营备注与知识库。
- 与飞书/企业微信通知集成。
- 与本地内容选题库联动。
- 用户手动导入经营数据作为官方 API 不足时的补充数据源。

不建议早期扩展：

- 自动发布视频。
- 自动评论或私信。
- 使用非官方接口采集数据。
- 依赖浏览器登录态模拟创作者中心页面。
- 绕过开放平台权限限制获取经营指标。

## 19. 最终建议

推荐按以下路线落地：

```text
第一步：stdio 本地 MCP + OAuth 授权 + Token 安全
第二步：用户信息 + 能力探测 + 本地缓存
第三步：可用数据同步 + 降级报告
第四步：HTTP 部署访问控制 + 正式 HTTPS callback
第五步：申请并验证 video.data / fans.data 等增强能力
第六步：扩展视频指标、粉丝趋势、多账号分析
```

这样项目不会因为某个高级数据接口暂时不可用而停摆，也能保持合规、安全和可演进。
