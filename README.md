# douyin-mcp

`douyin-mcp` 是一个轻量、单账号、本地运行的 MCP Server。它通过专用 Chrome profile 读取用户在抖音创作者中心真实可见的数据，写入本地 SQLite，再向 Agent 提供结构化查询、比较和复盘能力。

它解决的是“创作者中心数据与 Agent 隔离”的特殊场景，不建设多账号平台、云端服务或私有接口抓取系统。

## 核心能力

- 用户首次使用或登录过期时扫码，平时复用专用浏览器 profile。
- 增量收集虚拟滚动作品列表，不因 DOM 回收遗漏已看过的作品卡片。
- 保存播放、点赞、评论、分享、收藏等列表指标。
- 按需分批采集指定或最近作品详情，支持完播率、5 秒完播率、平均观看时长、曝光、涨粉等页面可见指标。
- 原始指标以 append-only 快照保存，详情与列表来源不混写。
- 页面未展示的字段保存为 `null`，同时记录缺失原因；解析器无法识别时标记降级，不猜测数据。
- 本地计算点赞率、收藏率、评论率、分享率、播放率和互动率，并记录公式版本。
- 返回新鲜度、字段覆盖率、质量警告和证据快照，便于 Agent 判断结论可信度。
- 首次成功列表同步建立不可逆的轻量账号指纹，后续发现误切账号时在写入前拒绝同步。
- 默认 MCP 不依赖抖音 OpenAPI 密钥，也不向 Agent 暴露 `account_id`。

## 安装

需要 Python 3.11+ 和本机 Google Chrome。

```powershell
git clone https://github.com/Kuhakucai/douyin-mcp.git
cd douyin-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
douyin-mcp init
douyin-mcp doctor
```

macOS/Linux 激活虚拟环境：

```bash
source .venv/bin/activate
```

浏览器主线无需配置 `TOKEN_ENCRYPTION_KEY`、`DOUYIN_CLIENT_KEY` 或 `DOUYIN_CLIENT_SECRET`。这些变量只供历史 OpenAPI 兼容入口使用。

推荐配置：

```env
MCP_TRANSPORT=stdio
DATA_DIR=./data
DOUYIN_BROWSER_PROFILE_DIR=./data/browser-profile
DOUYIN_BROWSER_CHANNEL=chrome
DOUYIN_BROWSER_AUTO_CLOSE=true
DOUYIN_BROWSER_PAGE_SETTLE_MS=5000
DOUYIN_LIST_CACHE_TTL_HOURS=24
DOUYIN_DETAIL_CACHE_TTL_HOURS=24
DOUYIN_DETAIL_BATCH_SIZE=10
```

## 首次使用

```powershell
# 打开可见 Chrome，扫码后自动保存登录 profile
douyin-mcp login --timeout 180

# 同步作品列表
douyin-mcp sync

# 查看本地作品，取得 video_id
douyin-mcp videos --limit 20

# 分批同步最近 20 条详情；每次默认最多处理 10 条
douyin-mcp details --recent-limit 20

# 返回 next_cursor 时继续下一批
douyin-mcp details --recent-limit 20 --cursor 10
```

只同步指定作品：

```powershell
douyin-mcp details --video-id <video_id> --force
douyin-mcp performance <video_id> --period 30d
```

登录过期时，同步结果会返回 `user_action_required` 和 `next_action`。再次运行 `douyin-mcp login` 扫码后重试即可。

首次成功同步会自动绑定当前扫码账号。指纹只由作品标题和发布时间的摘要再加本地随机盐生成，不保存昵称作为身份，也不向 Agent 返回原始锚点或随机盐。若误切账号，列表同步返回 `account_mismatch` 且不写入快照或作品数据；确认要更换账号时，先执行 `douyin-mcp purge --yes`，再重新扫码和同步。

## 接入 MCP 客户端

`douyin-mcp init` 会输出适合当前 Python 环境的 JSON 配置。通用配置示例：

```json
{
  "mcpServers": {
    "douyin-creator": {
      "command": "D:/path/to/douyin-mcp/.venv/Scripts/python.exe",
      "args": ["-m", "douyin_creator_mcp.server"],
      "env": {
        "MCP_TRANSPORT": "stdio",
        "DATA_DIR": "D:/path/to/douyin-mcp/data",
        "DOUYIN_BROWSER_PROFILE_DIR": "D:/path/to/douyin-mcp/data/browser-profile"
      }
    }
  }
}
```

默认入口 `douyin_creator_mcp.server` 只注册浏览器单账号工具。历史 OpenAPI 容器保留在代码中作为兼容入口，不会出现在默认工具集。

## MCP 工具

| 工具 | 用途 |
|---|---|
| `douyin_browser_login_start` | 打开可见 Chrome，处理首次登录或过期重登 |
| `douyin_browser_login_status` | 查询当前浏览器会话登录状态 |
| `douyin_browser_get_status` | 查询本地新鲜度、任务、覆盖率和 profile 锁 |
| `douyin_browser_sync_if_needed` | 按 24 小时 TTL 同步列表、详情或全部 |
| `douyin_browser_sync_creator_data` | 同步作品列表和列表指标 |
| `douyin_browser_sync_video_details` | 分批同步指定或近期作品详情指标 |
| `douyin_browser_list_videos` | 分页查询作品及最新列表快照 |
| `douyin_browser_get_video_performance` | 查询单作品列表/详情快照和派生指标 |
| `douyin_browser_compare_videos` | 对比 2～20 条作品 |
| `douyin_browser_get_metric_coverage` | 查询各字段覆盖率和缺失原因 |
| `douyin_browser_rank_video_potential` | 使用透明、带版本的规则做轻量排序 |
| `douyin_browser_generate_review` | 生成供 Agent 复盘的证据化上下文 |
| `douyin_browser_export_data` | 导出 JSON 或 CSV |

所有工具固定使用内部账号键 `browser-default`。数据响应包含 `ok`；业务状态可能是 `completed`、`partial`、`cache_hit` 或 `user_action_required`。

## 建议的 Agent 使用方式

可以直接告诉 Agent：

```text
检查我的抖音数据状态。如果列表或详情缓存已过期，更新作品列表并分批同步最近 20 条详情；
然后按最近 30 天比较完播率、5 秒完播率和互动率，给出复盘结论。
每条结论都说明数据时间、字段覆盖率、缺失项和对应视频/快照证据。
如果登录过期，打开浏览器让我扫码。
```

Agent 应先调用 `get_status` / `sync_if_needed`，再查询或复盘，避免无差别重复打开详情页。

## 数据可靠性约定

- “页面显示什么就保存什么”；未显示就是 `null`，不会用 0 或推测值填充。
- 写入详情前校验详情 URL、平台作品 ID 或页面标题；无法确认作品身份时拒绝写入。
- 同一批次、同一作品、同一来源最多写入一个快照；历史可信快照不会被失败同步覆盖。
- 派生比率只使用同一个原始快照的分子和分母。
- `period=30d` 等周期按快照采集时间筛选；`all` 表示全部本地历史。
- 潜力分低于 10 条样本时仅供参考，不等同于平台官方评分。

## 本地数据与清除

```text
data/
├── browser-profile/    # 专用 Chrome 登录状态
├── douyin.sqlite       # 作品、指标快照、任务和报告索引
├── exports/            # JSON/CSV 导出
├── reports/            # 兼容报告
└── logs/
```

V1 首次升级旧数据库前会使用 SQLite backup API 在数据库同目录创建时间戳备份。

导出和清除：

```powershell
douyin-mcp export --format json --period all
douyin-mcp export --format csv --period 30d

# 不加 --yes 只返回确认提示
douyin-mcp purge
douyin-mcp purge --yes
```

`purge --yes` 会删除数据库、备份、报告、导出和专用浏览器 profile。CSV/Markdown 公式注入转义不在当前轻量 V1 范围内；不要将导出文件交给不可信来源自动执行。

`data/`、浏览器 profile、数据库及备份、登录状态文件、真实账号逐值验收报告和浏览器诊断产物均由 `.gitignore` 排除。请保持 `DATA_DIR` 与 `DOUYIN_BROWSER_PROFILE_DIR` 在仓库的 `data/` 下，不要使用 `git add -f` 强制提交这些文件。

本 MCP 不把 Cookie、localStorage、sessionStorage、验证码或账号密码返回给 Agent；但作品标题和用户主动查询的创作数据会进入所连接的 Agent 上下文。若 Agent 使用云端模型，这些业务数据会受对应模型服务的数据政策约束。

## 真实联调边界

- 作品列表通道已用真实账号验证过页面声明总数、加载数和解析数一致，并保持重复同步幂等；真实作品数量和指标不进入仓库。
- 详情解析、身份门禁、缓存、批次、快照和派生公式已有自动化测试。
- 抖音详情页 DOM、字段可见性和连续访问触发验证的情况会随账号与平台变化；发布前仍需使用当前目标账号运行 `douyin-browser-smoke details` 做真实 smoke。未在真实页面展示的指标必须保持 `null`，不得改走未公开私有 API。

## 开发验证

```powershell
python -m compileall -q src tests
python -m pytest -q
```

当前自动化基线：79 项测试通过，另有 6 个参数化子测试通过。

相关文档：

- [产品闭环 PRD](docs/user-usability-closure-plan.md)
- [技术可行性报告](docs/user-usability-technical-feasibility-report.md)
- [浏览器会话说明](docs/browser-session.md)
- [限制说明](docs/limitations.md)
