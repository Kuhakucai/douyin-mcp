# 实现计划：douyin-mcp MVP 落地

## 1. 背景与目标

当前仓库只有 PRD 和实施方案，没有源码、测试或项目配置。本次目标是从零落地一个可运行、可测试、范围克制的 Python 版抖音创作者 MCP Server MVP。

产品定位是合规的抖音开放平台数据连接器。MCP Server 负责 OAuth 授权、token 安全存储、OpenAPI 调用、本地缓存、能力探测、错误标准化和报告生成；Agent 只能调用受控工具，不能直接接触敏感凭证或官方 API 细节。

成功标准是：项目具备完整 Python 包结构，默认可通过 stdio 被 MCP Client 拉起；核心服务层和存储层有自动化测试；在没有真实抖音凭证时也能通过 mock 验证授权、加密、能力降级、报告生成等关键逻辑；用户配置真实凭证后可进入实际联调。

## 2. 需求范围

### 2.1 包含

- 初始化 Python 项目结构与依赖声明。
- 新增安装、OAuth、scope、API 映射、限制说明等文档。
- 新增 `.env.example` 和 MCP Client 配置示例。
- 实现配置加载、路径初始化、日志基础设置。
- 实现 SQLite schema 和数据库初始化。
- 实现 token 加密存储，使用 `cryptography.fernet.Fernet`，不自造弱加密。
- 实现 OAuth state、auth_session_id、开发模式 `douyin_auth_complete` 和正式 callback 服务入口。
- 实现 Douyin API Client，按 `docs/api-mapping.md` 中的接口配置决定 path、method、鉴权字段位置、请求参数位置。
- 实现统一响应与错误类型。
- 实现 MCP 工具：
  - `douyin_auth_start`
  - `douyin_auth_complete`
  - `douyin_auth_status`
  - `douyin_list_accounts`
  - `douyin_get_account_profile`
  - `douyin_check_capabilities`
  - `douyin_sync_available_data`
  - `douyin_get_account_summary`
  - `douyin_generate_creator_report`
- 实现 HTTP 模式访问控制约束：HTTP 启动时必须配置 API Key，并提供 ASGI 中间件/启动守卫；如运行环境无法挂载中间件，文档明确要求反向代理或 FastMCP 原生 auth。
- 实现降级报告：当视频/粉丝经营数据不可用时，不把“无数据”误判为“表现差”，而是说明权限缺失和补齐路径。
- 实现单元测试，覆盖配置、数据库、token 加密、OAuth state、报告、敏感字段过滤和 API 映射请求组装。

### 2.2 不包含

- 不实现自动发布视频、删除视频、评论、私信、关注等写操作。
- 不使用 Cookie、爬虫、私有接口或创作者中心页面模拟。
- 不承诺首版拉取完整视频经营数据、完播率、平均观看时长等高级指标。
- 不在没有官方权限确认的情况下启用 `douyin_sync_videos`、`douyin_get_video_metrics`、`douyin_get_fans_trend` 等增强工具。
- 不硬编码用户真实 client_key、client_secret、token 或账号数据。
- 不把真实线上抖音接口联调作为本轮自动化测试的必要条件。

## 3. 现状分析

仓库目前只有 README 和两份 Markdown 需求/方案文档：

- `README.md`：当前仅有项目标题，需要扩展为安装、运行和安全说明。
- `douyin-creator-mcp.md`：给出官方 OpenAPI 修订后的实施方案，建议项目结构、配置、OAuth、Token、数据库、工具清单和验收标准。
- `douyin-creator-mcp-PRD.md`：定义产品定位、MCP 边界、安全边界、核心工具、数据库表、返回格式、同步策略和报告策略。

当前没有 `pyproject.toml`、`src/`、`tests/`、`docs/`、`.env.example` 或运行入口。实施需要从空项目开始搭建，不涉及兼容既有代码。

当前目录已关联 Git 仓库，实施复审阶段需要使用 `git status` 和 `git diff` 核查实际变更。

## 4. 方案设计

### 4.1 总体思路

采用分层结构：

```text
MCP tools / callback app
  -> services
  -> DouyinApiClient
  -> TokenStore / SQLite repositories
  -> local data and reports
```

工具层只做参数校验、访问控制和结构化返回；OAuth、API 请求、同步、报告生成放在 service 层；SQLite 和 token 加密放在 storage 层。

外部依赖选择：

- `fastmcp`：MCP Server 框架。
- `httpx`：抖音 OpenAPI HTTP 客户端。
- `cryptography`：Fernet 对称加密 token。
- `pydantic-settings` 或轻量配置模块：读取环境变量。
- `fastapi`/`uvicorn`：正式 HTTPS callback 可部署入口；生产环境应放在 HTTPS 反向代理后。
- `pytest`：自动化测试。

核心安全策略：

- `client_secret` 只读环境变量。
- token 只在 service/storage 内部明文存在，落库必须加密。
- MCP 返回值统一经过敏感字段过滤。
- 日志、报告和错误消息不包含 code/token/client_secret。
- `local_manual_code` 仅用于本地开发；`https_callback` 模式下 `douyin_auth_complete` 拒绝接收 code 并提示使用 callback。
- HTTP 模式启动时强制要求 `MCP_HTTP_API_KEY` 或显式配置反向代理认证说明。

### 4.2 详细设计

#### 配置模块

`src/douyin_creator_mcp/config.py`

定义 `Settings`：

- `mcp_transport: str = "stdio"`
- `mcp_host: str = "127.0.0.1"`
- `mcp_port: int = 8787`
- `mcp_http_api_key: str | None`
- `data_dir: Path = Path("./data")`
- `log_level: str = "INFO"`
- `douyin_client_key: str | None`
- `douyin_client_secret: str | None`
- `douyin_redirect_uri: str`
- `douyin_scopes: list[str]`
- `douyin_oauth_mode: Literal["local_manual_code", "https_callback"]`
- `token_encryption_key: str`
- `http_timeout_seconds: int`
- `sync_page_size: int`
- `api_mapping_file: Path`

配置加载支持 `.env`，并提供：

- `load_settings()`
- `ensure_runtime_dirs(settings)`
- `validate_for_auth(settings)`
- `validate_for_http(settings)`

#### 统一错误与响应

`src/douyin_creator_mcp/errors.py`

定义错误类型常量和 `AppError`，包含：

- `authorization_required`
- `authorization_expired`
- `capability_missing`
- `scope_missing`
- `mcp_access_denied`
- `api_rate_limited`
- `api_error`
- `network_error`
- `invalid_response`
- `data_not_available`
- `configuration_error`
- `validation_error`

`src/douyin_creator_mcp/responses.py`

提供：

- `success_response(**payload)`
- `error_response(error_type, message, retryable=False, **extra)`
- `sanitize_payload(payload)`

`sanitize_payload` 递归移除或脱敏字段名包含 `token`、`secret`、`code`、`authorization` 的内容。

#### 数据库与 schema

`src/douyin_creator_mcp/storage/schemas.sql`

按 PRD 建表：

- `accounts`
- `tokens`
- `oauth_states`
- `api_capabilities`
- `videos`
- `video_metrics`
- `sync_jobs`
- `reports`
- `audit_logs`

`src/douyin_creator_mcp/storage/db.py`

提供：

- `Database(path: Path)`
- `init_schema()`
- `connect()`
- `execute()`
- `query_one()`
- `query_all()`
- `transaction()`

SQLite 参数使用绑定变量，禁止拼接 SQL。

#### Token 加密存储

`src/douyin_creator_mcp/storage/token_store.py`

提供：

- `TokenStore.save_tokens(account_id, access_token, refresh_token, expires_at, refresh_expires_at)`
- `TokenStore.get_tokens(account_id)`
- `TokenStore.get_valid_token(account_id, refresh_callback)`
- `TokenStore.delete_tokens(account_id)`

加密方式：

- 要求 `TOKEN_ENCRYPTION_KEY` 为 Fernet key，或支持从用户提供的 passphrase 派生 Fernet key。
- 数据库只保存 `access_token_encrypted` 和 `refresh_token_encrypted`。
- 返回给 service 的明文 token 不进入工具响应。

#### OAuth 服务

`src/douyin_creator_mcp/services/auth_service.py`

提供：

- `start_auth(scopes: list[str] | None = None) -> dict`
- `complete_auth(code: str, state: str | None = None) -> dict`
- `handle_callback(code: str, state: str) -> dict`
- `get_auth_status(auth_session_id: str | None = None) -> dict`
- `refresh_token(account_id: str) -> dict`

`start_auth`：

- 生成随机 `state` 和 `auth_session_id`。
- 写入 `oauth_states`，状态为 `pending`。
- 拼接 `/platform/oauth/connect/` 授权 URL。
- 返回 `authorization_url`、`auth_session_id`、`scopes`、`oauth_mode`。
- 不返回 secret。

`complete_auth`：

- 仅 `local_manual_code` 允许调用。
- 校验 state 存在且未消费。
- 调用 `DouyinApiClient.exchange_code_for_token`。
- 写入 account 和 encrypted tokens。
- 更新 OAuth state 为 `completed`。
- 返回账号摘要。

`handle_callback`：

- 用于 `https_callback`。
- 校验 state。
- 换 token 并落库。
- 返回 callback 服务可展示的安全结果。

#### Douyin API Client

`src/douyin_creator_mcp/services/douyin_api.py`

提供：

- `request(api_key, account_id=None, params=None, json=None, form=None)`
- `exchange_code_for_token(code)`
- `refresh_access_token(refresh_token)`
- `renew_refresh_token(refresh_token)`
- `get_user_info(account_id)`

请求组装遵循 `docs/api-mapping.md` 和 `src/douyin_creator_mcp/api_mapping.py` 解析后的配置：

- `method`
- `path`
- `required_scope`
- `auth.access_token`
- `auth.open_id`
- `request.params`
- `request.form`
- `request.json`
- `pagination`
- `rate_limit`

HTTP 错误、限流、网络错误统一映射为标准错误。

#### 能力探测服务

`src/douyin_creator_mcp/services/capability_service.py`

提供：

- `check_capabilities(account_id) -> dict`
- `ensure(account_id, capability_key) -> None`
- `record_capability(account_id, capability_key, status, detail)`

首版能力状态来源：

- 授权 scope 与 `docs/api-mapping.md` 的 required_scope 对照。
- 对 `user_info` 做可选真实探测。
- 对未确认高级能力返回 `unknown` 或 `missing`，并写入 `api_capabilities`。

#### 账号、同步、报告服务

`account_service.py`

- `list_accounts()`
- `get_account_profile(account_id)`
- `upsert_account(profile)`

`sync_service.py`

- `sync_available_data(account_id)`
- 创建 `sync_jobs`。
- 先同步账号资料和能力状态。
- 对缺失能力记录 `api_capabilities`，不阻断整体同步。

`report_service.py`

- `get_account_summary(account_id, start_date=None, end_date=None)`
- `generate_creator_report(account_id, period="7d")`
- 生成 Markdown 到 `data/reports/`。
- 写入 `reports`。
- 根据可用数据输出 `full`、`partial`、`limited` 数据质量等级。

报告必须包含：

- 数据来源。
- 数据时间范围。
- 已使用接口。
- 缺失指标说明。
- 数据质量等级。
- 账号表现总结。
- 内容建议。
- 下一步需要申请的 scope 或能力。

#### MCP 工具

`src/douyin_creator_mcp/server.py`

初始化 FastMCP，并注册工具函数。每个工具调用 service，返回统一结构。

工具文件：

- `tools/auth_tools.py`
- `tools/account_tools.py`
- `tools/sync_tools.py`
- `tools/report_tools.py`

工具禁止返回敏感字段，所有返回通过 `sanitize_payload`。

#### HTTP callback 与访问控制

`src/douyin_creator_mcp/callback_app.py`

提供 FastAPI app：

- `GET /oauth/douyin/callback`
- 校验 `state` 和 `code`。
- 调用 `AuthService.handle_callback`。
- 返回不含敏感信息的 HTML/JSON。

`src/douyin_creator_mcp/security.py`

提供：

- `require_http_api_key(headers, settings)`
- `McpAccessMiddleware`
- HTTP 模式启动守卫。

首版策略：

- stdio 默认允许本机进程调用。
- HTTP 模式必须配置 `MCP_HTTP_API_KEY`。
- callback 路由本身不要求 API Key，但必须校验 OAuth `state`。
- MCP HTTP 工具接口如无法直接挂载中间件，则启动文档必须要求通过反向代理添加 API Key 或 FastMCP 原生 auth；程序启动时对缺失配置报错。

### 4.3 涉及变更的文件清单

- `pyproject.toml` — 新增 Python 项目配置、依赖、测试配置。
- `README.md` — 新增项目说明、运行方式、MCP 配置示例、授权流程。
- `.env.example` — 新增环境变量示例。
- `.gitignore` — 忽略虚拟环境、缓存、数据文件、报告和密钥。
- `docs/install.md` — 安装与启动说明。
- `docs/oauth.md` — local_manual_code 与 https_callback 授权流程。
- `docs/scopes.md` — scope 与能力说明。
- `docs/api-mapping.md` — 官方 API 映射配置说明和首版接口表。
- `docs/limitations.md` — 权限、数据、合规和真实联调限制。
- `src/douyin_creator_mcp/__init__.py` — 包元信息。
- `src/douyin_creator_mcp/config.py` — 配置加载与校验。
- `src/douyin_creator_mcp/errors.py` — 错误类型和异常。
- `src/douyin_creator_mcp/responses.py` — 统一响应和敏感字段过滤。
- `src/douyin_creator_mcp/api_mapping.py` — API 映射解析。
- `src/douyin_creator_mcp/security.py` — HTTP 访问控制辅助。
- `src/douyin_creator_mcp/server.py` — MCP Server 入口和工具注册。
- `src/douyin_creator_mcp/callback_app.py` — HTTPS callback ASGI 入口。
- `src/douyin_creator_mcp/tools/__init__.py` — tools 包。
- `src/douyin_creator_mcp/tools/auth_tools.py` — 授权工具。
- `src/douyin_creator_mcp/tools/account_tools.py` — 账号与能力工具。
- `src/douyin_creator_mcp/tools/sync_tools.py` — 同步工具。
- `src/douyin_creator_mcp/tools/report_tools.py` — 摘要与报告工具。
- `src/douyin_creator_mcp/services/__init__.py` — services 包。
- `src/douyin_creator_mcp/services/auth_service.py` — OAuth 与 token 刷新。
- `src/douyin_creator_mcp/services/douyin_api.py` — OpenAPI 客户端。
- `src/douyin_creator_mcp/services/capability_service.py` — 能力探测。
- `src/douyin_creator_mcp/services/account_service.py` — 账号资料。
- `src/douyin_creator_mcp/services/sync_service.py` — 可用数据同步。
- `src/douyin_creator_mcp/services/report_service.py` — 摘要与报告。
- `src/douyin_creator_mcp/storage/__init__.py` — storage 包。
- `src/douyin_creator_mcp/storage/db.py` — SQLite 封装。
- `src/douyin_creator_mcp/storage/token_store.py` — 加密 token 存储。
- `src/douyin_creator_mcp/storage/schemas.sql` — 数据库表结构。
- `tests/conftest.py` — 测试夹具。
- `tests/test_config.py` — 配置与目录测试。
- `tests/test_responses.py` — 敏感字段过滤测试。
- `tests/test_token_store.py` — token 加密/解密测试。
- `tests/test_auth_service.py` — OAuth state 与授权完成 mock 测试。
- `tests/test_api_mapping.py` — API 映射与请求组装测试。
- `tests/test_report_service.py` — 降级报告测试。

## 5. 实施步骤

1. **初始化项目骨架** — 新增 `pyproject.toml`、`.env.example`、`.gitignore`、`README.md`、`docs/`、`src/`、`tests/`；验证 `python -m compileall src` 基础通过。
2. **实现配置、错误和响应基础设施** — 完成 `config.py`、`errors.py`、`responses.py`；验证配置读取、目录创建和敏感字段过滤测试通过。
3. **实现 SQLite 与 token 加密存储** — 完成 schema、`db.py`、`token_store.py`；验证建表、token 落库不含明文、解密读取正常。
4. **实现 API 映射和 DouyinApiClient** — 完成 `docs/api-mapping.md`、`api_mapping.py`、`douyin_api.py`；验证不同鉴权位置和错误映射可测试。
5. **实现 OAuth 服务和 callback 入口** — 完成 `auth_service.py`、`callback_app.py`；验证授权 URL、state 校验、开发模式换 token、正式模式拒绝手动 code 的路径。
6. **实现账号、能力探测、同步和报告服务** — 完成 account/capability/sync/report service；验证无高级权限时生成降级报告且说明缺失原因。
7. **实现 MCP 工具注册和 HTTP 访问控制守卫** — 完成 `server.py`、tools、`security.py`；验证工具返回结构化结果且不含敏感字段，HTTP 模式缺少 API Key 时拒绝启动。
8. **补齐文档和 MCP 配置示例** — 更新 README 和 docs，说明真实凭证联调步骤、OAuth 两种模式、scope 申请、限制边界。
9. **运行验证** — 执行 `python -m compileall src` 和 `python -m pytest`；如环境缺少依赖，先记录失败原因，再按需请求安装依赖或调整为可离线验证的核心测试。
10. **撰写实施日志** — 按四阶段模板记录变更、测试结果、偏离计划与遗留问题。

## 6. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 当前环境没有真实抖音凭证 | 无法验证真实 OAuth 和 OpenAPI | 自动化测试使用 mock；文档列出真实联调步骤和验收点 |
| 官方 API 字段或路径可能变化 | 实际联调失败 | 所有接口集中维护在 `docs/api-mapping.md`，实现支持按配置调整 |
| FastMCP HTTP 访问控制挂载方式受版本影响 | HTTP 模式访问控制可能无法在代码内完全接管 | 启动守卫强制 API Key 配置；优先使用 FastMCP 原生 auth/ASGI middleware；文档要求反向代理认证兜底 |
| `cryptography` 等依赖可能未安装且网络受限 | 测试无法完整运行 | 先运行离线可执行检查；如必须安装依赖，按权限流程请求用户批准 |
| 高级视频/粉丝数据权限不可用 | 报告数据价值下降 | 能力探测 + 降级报告 + 明确补齐 scope/能力路径 |
| Agent 误拿敏感字段 | 安全事故 | 统一响应过滤、测试覆盖 token/code/secret 不出现在工具返回 |
| 从空项目一次性新增文件较多 | 审查成本高 | 按分层实施，保持文件职责单一，实施日志列明完整清单 |

## 7. 测试策略

- 单元测试：
  - 配置加载、目录初始化、HTTP 模式 API Key 校验。
  - `sanitize_payload` 对 token/code/secret 的递归过滤。
  - SQLite schema 初始化和基础查询。
  - TokenStore 加密落库和解密读取，断言数据库不含明文 token。
  - OAuth state 生成、消费、防重放、手动 code 模式限制。
  - API 映射解析和请求组装。
  - 能力缺失时的结构化错误。
  - 降级报告内容包含缺失指标、数据质量和补齐建议。
- 集成/端到端测试：
  - 用 mock Douyin API Client 完成 `auth_start -> auth_complete -> auth_status -> list_accounts -> generate_report`。
  - 用临时 SQLite 数据库验证同步任务和报告记录落库。
- 手动验证：
  - `python -m douyin_creator_mcp.server` 在 stdio 配置下可加载。
  - HTTP 模式未配置 API Key 时拒绝启动。
  - 配置真实抖音凭证后，由用户执行授权 URL 打开、回填 code 或 callback 联调。
- 验证命令：
  - `python -m compileall src`
  - `python -m pytest`

## 8. 回滚预案

当前仓库已关联 Git。若实施后需要回滚，优先使用 Git 对未提交改动做按文件回退；也可删除本轮新增的项目文件和目录：

- `pyproject.toml`
- `README.md`
- `.env.example`
- `.gitignore`
- `docs/`
- `src/`
- `tests/`
- `data/` 中由运行产生的本地缓存和报告

四阶段产物保留在 `.auto-flow/douyin-mcp-mvp-20260707/`，用于追溯计划和实施过程。
