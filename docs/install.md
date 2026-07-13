# 安装说明

## 环境要求

- Python 3.11 或更高版本。
- 抖音开放平台应用。
- 本地或私有化部署环境。

## 安装步骤

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m douyin_creator_mcp.config
```

将生成的 Fernet key 写入 `.env` 的 `TOKEN_ENCRYPTION_KEY`。

## 启动 MCP Server

默认使用 stdio：

```powershell
python -m douyin_creator_mcp.server
```

HTTP 模式用于高级部署。启用 HTTP 模式时必须配置 `MCP_HTTP_API_KEY`，并建议放在内网或带认证的反向代理之后。

```env
MCP_TRANSPORT=http
MCP_HTTP_API_KEY=change_me
```

## 数据目录

默认数据目录为 `./data`，其中会包含：

- `douyin.sqlite`
- `reports/`
- `logs/`

不要把 `data/` 提交到仓库。
