<div align="center">
  <h1>douyin-mcp</h1>
  <p><strong>让 AI 读懂你的抖音创作数据</strong></p>
  <p>本地运行 · 数据可追溯 · 面向个人创作者的 MCP Server</p>

  <p>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.11+"></a>
    <img src="https://img.shields.io/badge/Platform-Windows-0078D4?logo=windows11&amp;logoColor=white" alt="Windows">
    <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/MCP-Compatible-22C55E" alt="MCP Compatible"></a>
    <a href="https://github.com/jlowin/fastmcp"><img src="https://img.shields.io/badge/FastMCP-Powered-FF6B35" alt="FastMCP Powered"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-8B5CF6" alt="AGPL-3.0"></a>
    <a href="https://github.com/Kuhakucai/douyin-mcp/stargazers"><img src="https://img.shields.io/github/stars/Kuhakucai/douyin-mcp?style=flat&amp;logo=github&amp;label=Stars" alt="GitHub Stars"></a>
  </p>

  <p>
    <a href="#快速开始">快速开始</a> ·
    <a href="#核心能力">核心能力</a> ·
    <a href="#推荐用法">推荐用法</a> ·
    <a href="#mcp-工具">MCP 工具</a> ·
    <a href="#工作原理">工作原理</a> ·
    <a href="#安全合规与许可">安全合规</a>
  </p>
</div>

![douyin-mcp：从创作者数据到 AI 分析](assets/douyin-mcp-hero.svg)

> [!IMPORTANT]
> **这是非官方社区工具。** 本项目未获抖音或其关联公司授权、认可或背书。项目使用 Playwright 操作浏览器；即使只读取本人账号中真实可见的数据，也可能违反平台条款或触发账号风控。使用前请阅读[平台合规与非官方声明](PLATFORM_COMPLIANCE.md)，确认已取得所需授权，并先执行风险确认。AGPL 只许可项目代码，不授予任何平台访问权、数据权或商标权。

## 一分钟了解

`douyin-mcp` 在你的电脑上复用专用 Chrome 登录状态，将抖音创作者中心页面中真实可见的作品和经营指标保存到本地 SQLite，再通过 MCP 提供给支持 MCP 的 AI Agent。

| | |
|---|---|
| 📊 **读取真实可见数据**<br>增量同步作品列表、播放、点赞、评论、分享、收藏、完播率和涨粉等页面可见指标。 | 🧠 **让 Agent 查询和分析**<br>查询作品、对比表现、计算互动指标、生成复盘上下文，并导出 JSON 或 CSV。 |
| 🧾 **结论附带证据**<br>返回采集时间、缓存新鲜度、字段覆盖率、缺失原因和质量警告，不用猜测值填空。 | 🔒 **登录凭证留在本地**<br>Cookie 与浏览器状态保存在专用 profile 中，MCP 不向 Agent 返回认证材料。 |

它解决的是一个具体问题：

```text
抖音创作者中心  →  本地结构化数据  →  MCP  →  AI Agent
```

当前范围为 **Windows、本机运行、单用户、单抖音账号**。本项目不提供多账号托管、云端采集、数据转售、未公开接口抓取，或绕过登录、安全验证、权限及风控的能力。

## 快速开始

### 环境要求

- Windows 10/11
- Python 3.11 或更高版本
- Google Chrome
- 一个支持 MCP 和终端操作的 Agent

### 方式一：让 Agent 安装

直接告诉 Agent：

```text
帮我克隆并安装 https://github.com/Kuhakucai/douyin-mcp 项目
```

Agent 应克隆项目并运行 `easy-install.ps1`。脚本会创建项目专用 `.venv`、安装依赖、生成默认 `.env`、初始化数据库并执行环境诊断。

> [!NOTE]
> 首次扫码登录、修改 MCP 客户端配置或同步真实数据前，Agent 应先展示平台风险并征得你的明确确认。

### 方式二：手动安装

<details>
<summary><strong>展开查看 PowerShell 安装命令</strong></summary>

```powershell
git clone https://github.com/Kuhakucai/douyin-mcp.git
cd douyin-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item .env.example .env
douyin-mcp init
douyin-mcp doctor
```

也可以在已克隆的项目中运行一键脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\easy-install.ps1
```

</details>

### 确认平台风险

阅读 [PLATFORM_COMPLIANCE.md](PLATFORM_COMPLIANCE.md) 后，由你明确授权 Agent 执行：

```powershell
douyin-mcp acknowledge-platform-risk --yes
```

### 接入 MCP 客户端

安装结束后，`douyin-mcp init` 会输出包含本地绝对路径的 `mcp_config`。将它加入 MCP 客户端，然后重启客户端或新建会话。

通用配置结构如下，实际使用时请以 `init` 输出为准：

```json
{
  "mcpServers": {
    "douyin-creator": {
      "command": "D:/path/to/douyin-mcp/.venv/Scripts/python.exe",
      "args": ["-m", "douyin_creator_mcp.server"],
      "env": {
        "MCP_TRANSPORT": "stdio",
        "DATA_DIR": "D:/path/to/douyin-mcp/data",
        "DOUYIN_BROWSER_PROFILE_DIR": "D:/path/to/douyin-mcp/data/browser-profile",
        "TRANSCRIPT_INGESTION_ENABLED": "false",
        "TRANSCRIPT_AUTO_WARMUP_ENABLED": "true",
        "TRANSCRIPT_WARMUP_RECENT_LIMIT": "5",
        "TRANSCRIPT_AUTO_INGEST_NEW_VIDEOS": "true",
        "TRANSCRIPT_AUTO_NEW_VIDEO_LIMIT": "20",
        "TRANSCRIPT_AUTO_PREPARE_ANALYSIS": "true"
      }
    }
  }
}
```

### 完成首次同步

连接成功并完成风险确认后，可以直接对 Agent 说：

```text
检查我的抖音数据状态。如果还没有登录，打开浏览器让我扫码；
登录后同步作品列表，再分批同步最近 20 条作品详情。
列表同步完成后不要等待全部历史视频转写；在后台预热最近 5 条公开视频文案。
完成后告诉我数据时间、字段覆盖率、缺失项和质量警告。
```

首次需要登录时会打开可见 Chrome。完成扫码或安全验证后，请保持项目专用 Chrome 打开；可以切换到其他软件，但不要切换账号、手动跳转页面或关闭窗口。

## 核心能力

### 获取真实可见的数据

- 首次使用或登录失效时打开可见 Chrome，由用户扫码或完成安全验证。
- 后续复用项目专用浏览器 profile，通常不需要重复登录。
- 增量读取虚拟滚动作品列表，保存播放、点赞、评论、分享和收藏等页面可见指标。
- 按需分批读取作品详情，采集完播率、5 秒完播率、平均观看时长、曝光和涨粉等页面可见指标。

### 查询、对比与复盘

- 查询作品列表、单条作品表现和历史快照。
- 对比 2～20 条作品的关键指标。
- 从视频音轨文案中识别选题、内容结构、关键观点、教程步骤和行动价值。
- 分析或对比所需文案尚未入库时，自动创建后台任务，完成后继续分析。
- 计算点赞率、收藏率、评论率、分享率、播放率和互动率。
- 使用透明、带版本的规则进行轻量潜力排序。
- 生成带数据时间、覆盖率、缺失项和证据引用的复盘上下文。
- 导出 JSON 或 CSV，便于进一步分析或备份。

### 判断结论是否可信

- 返回缓存新鲜度、字段覆盖率、缺失原因和质量警告。
- 页面未显示的值保存为 `null`，不会用 0 或猜测值填充。
- 列表与详情分别保存为快照，不会混写数据来源。
- 派生比率只使用同一原始快照中的分子和分母，并记录公式版本。
- 首次成功同步后绑定当前账号，检测到误切账号时拒绝写入。

## 推荐用法

日常使用时，建议先检查缓存新鲜度，再决定是否打开浏览器同步：

```text
检查我的抖音数据状态。只在缓存过期时更新作品列表和最近 20 条详情；
然后按最近 30 天比较完播率、5 秒完播率和互动率，给出复盘结论。
每条结论都说明数据时间、覆盖率、缺失项和对应作品证据。
```

也可以直接提出具体问题：

- “找出最近 30 天互动率最高的 5 条作品，并说明共同点。”
- “对比这 3 条视频的完播率、收藏率和涨粉表现。”
- “结合这 3 条视频的音轨文案和表现数据，对比选题、开头钩子、内容结构、行动价值与互动差异；缺少文案时自动补齐。”
- “提取这条视频的教程步骤和关键结论，并标注对应时间段。”
- “哪些作品值得做续集？说明排序依据和数据局限。”
- “导出全部历史快照为 JSON。”

## CLI

常用命令：

```powershell
# 首次登录或登录失效
douyin-mcp login --timeout 180

# 同步作品列表
douyin-mcp sync

# 查看作品并取得 video_id
douyin-mcp videos --limit 20

# 每批最多处理 10 条；根据 next_cursor 继续
douyin-mcp details --recent-limit 20
douyin-mcp details --recent-limit 20 --cursor 10

# 查询单条作品表现
douyin-mcp performance <video_id> --period 30d

# 查看登录、缓存、任务和覆盖率
douyin-mcp status
```

<details>
<summary><strong>查看完整 CLI 命令表</strong></summary>

| 命令 | 用途 |
|---|---|
| `init` | 初始化目录和数据库，输出 MCP 配置 |
| `doctor` | 检查运行环境，不打开浏览器 |
| `acknowledge-platform-risk` | 确认已阅读并理解平台自动化访问风险 |
| `login` | 打开浏览器并等待登录 |
| `status` | 查看登录、缓存、同步任务和覆盖率 |
| `sync` | 同步作品列表和列表页指标 |
| `details` | 分批同步指定或近期作品详情 |
| `videos` | 分页查询本地作品 |
| `performance` | 查询单条作品快照和派生指标 |
| `export` | 导出 JSON 或 CSV |
| `purge` | 清除本地数据和专用浏览器 profile |

</details>

## MCP 工具

默认入口保留原有 13 个浏览器数据工具，并新增 9 个本地视频文案流水线工具。

<details open>
<summary><strong>查看 MCP 工具列表</strong></summary>

| 工具 | 用途 |
|---|---|
| `douyin_browser_login_start` | 打开可见 Chrome，处理首次登录或重新登录 |
| `douyin_browser_login_status` | 查询当前浏览器登录状态 |
| `douyin_browser_get_status` | 查询新鲜度、任务、覆盖率、账号绑定和 profile 锁 |
| `douyin_browser_sync_if_needed` | 按 TTL 同步列表、详情或全部数据 |
| `douyin_browser_sync_creator_data` | 同步作品列表和列表指标 |
| `douyin_browser_sync_video_details` | 分批同步指定或近期作品详情指标 |
| `douyin_browser_list_videos` | 分页查询作品和最新指标 |
| `douyin_browser_get_video_performance` | 查询单作品快照和派生指标 |
| `douyin_browser_compare_videos` | 对比 2～20 条作品 |
| `douyin_browser_get_metric_coverage` | 查询字段覆盖率和缺失原因 |
| `douyin_browser_rank_video_potential` | 使用透明、带版本的规则进行轻量排序 |
| `douyin_browser_generate_review` | 生成带证据和警告的复盘上下文 |
| `douyin_browser_export_data` | 导出 JSON 或 CSV |
| `douyin_browser_submit_transcript_run` | 提交指定/近期文案任务；`all_public=true` 时显式回溯全部公开视频 |
| `douyin_browser_get_transcript_run` | 查询逐视频阶段和逐 run 计数 |
| `douyin_browser_list_transcript_runs` | 分页列出历史文案任务 |
| `douyin_browser_cancel_transcript_run` | 取消当前 run 的需求，不误停共享 job |
| `douyin_browser_retry_transcript_run` | 为失败视频创建新的重试 run |
| `douyin_browser_get_transcript_capabilities` | 诊断 FFmpeg、FFprobe、本地模型和功能门禁 |
| `douyin_browser_get_transcript_backfill_plan` | 只读预估全量历史回溯的数量、耗时和存储 |
| `douyin_browser_get_video_transcript` | 按不可变 revision 分页返回原始时间戳分片 |
| `douyin_browser_get_video_analysis_context` | 返回确定性分析段落；默认自动排队补齐缺失文案 |

</details>

所有工具使用内部账号键 `browser-default`，Agent 无需传递账号 ID。常见业务状态包括 `completed`、`partial`、`cache_hit` 和 `user_action_required`。

### 启用视频文案流水线

该能力默认关闭。仅在确认当前账号、作品权限、平台条款和本地运行依赖后，在 `.env`
设置 `TRANSCRIPT_INGESTION_ENABLED=true`。安装本地 ASR 可选依赖：

```powershell
python -m pip install -e ".[asr]"
```

同时配置已存在的本地模型目录 `TRANSCRIPT_ASR_MODEL_DIR`，并确保 `ffmpeg`、
`ffprobe` 可执行。运行时不会联网下载模型。MCP 提交只创建持久 run 并立即返回；
后台 worker 按视频复用全局 job。`analysis_ready` 和 `no_speech` 都是成功终态，
标点恢复或语义分段不会阻塞分析。

启用后默认采用混合策略，而不是首次启动就处理全部历史视频：

| 场景 | 默认行为 |
|---|---|
| 首次成功同步 | 元数据同步立即返回；后台只预热最近 5 条缺失文案的公开视频 |
| 后续同步发现新公开视频 | 每次最多自动排队 20 条新视频 |
| Agent 请求尚未入库的视频分析上下文 | 自动创建文案任务并返回 `run_id`，完成后重新读取上下文 |
| 历史视频 | 默认按需处理，不自动全量回溯 |
| 全量历史回溯 | 用户明确要求后调用 `douyin_browser_submit_transcript_run(all_public=true)` |

对应配置为：

```dotenv
TRANSCRIPT_AUTO_WARMUP_ENABLED=true
TRANSCRIPT_WARMUP_RECENT_LIMIT=5
TRANSCRIPT_AUTO_INGEST_NEW_VIDEOS=true
TRANSCRIPT_AUTO_NEW_VIDEO_LIMIT=20
TRANSCRIPT_AUTO_PREPARE_ANALYSIS=true
```

`TRANSCRIPT_INGESTION_ENABLED=false` 时，上述自动策略全部不会启动。命令行
`douyin-mcp sync` 只负责同步作品列表；需要后台预热和自动补齐时，应让保持运行的
MCP Server 调用 `douyin_browser_sync_creator_data` 或
`douyin_browser_sync_if_needed`。完整流程、状态说明、Agent 提示词和全量回溯方法见
[视频文案提取使用说明](docs/TRANSCRIPT_USAGE.md)。

文案分页固定到不可变 revision，游标在进程重启后仍有效；默认只返回标题、时长和
带时间戳分片。签名媒体 URL、Cookie、Authorization、媒体二进制和绝对本地路径
不会进入 MCP 响应或持久错误。

## 工作原理

浏览器操作、数据结构化和 Agent 推理被分成三个清晰层次：

1. **浏览器层**：Playwright 操作项目专用 Chrome，读取用户在创作者中心页面中真实可见的内容，不能绕过登录、权限或平台验证。
2. **数据层**：本地服务将页面内容规范化为作品、指标快照、同步任务和质量状态，并保存到本机 SQLite。
3. **MCP 层**：FastMCP 暴露同步、查询、对比和复盘工具，Agent 不需要直接操作 Cookie 或理解页面 DOM。

```text
MCP Client / Agent
        │ stdio
        ▼
douyin_creator_mcp.server
        │
        ├── BrowserExecutor ─ Playwright ── 专用 Chrome profile
        ├── TranscriptCoordinator ─ FFmpeg / local ASR
        └── Database ────────────── data/douyin.sqlite
```

列表同步负责发现作品和采集列表页指标，详情同步按批次访问作品详情页。两种来源分别保存为快照，不会互相覆盖。

### 数据可靠性

- 页面显示什么就保存什么；未显示的值为 `null`，不会用 0 或推测值填充。
- 写入详情前校验作品身份；无法确认时拒绝写入。
- 同一批次、同一作品、同一来源只写一个快照；失败同步不会覆盖历史可信快照。
- `period=30d` 等周期按快照采集时间筛选，`all` 表示全部本地历史。
- 部分作品可能被平台标记为暂不支持详情数据，此时返回 `partial`、失败原因和续跑游标。
- 潜力排序在样本少于 10 条时仅供参考，不代表平台官方评分。
- 页面 DOM、字段可见性和风控策略可能变化，使用时应关注覆盖率和质量警告。

### 登录态、账号与并发

- 登录状态保存在 `data/browser-profile/`，再次启动通常不需要重新扫码。
- 首次成功列表同步会基于作品标题和发布时间摘要建立不可逆账号指纹，不保存昵称、原始标题或作品 ID 作为身份信息。
- 检测到账号变化时返回 `account_mismatch` 并拒绝写入。
- 同一 profile 同时只允许一个同步进程；死亡进程留下的锁会在安全确认后自动恢复。
- 确认要更换账号时，运行 `douyin-mcp purge --yes`，然后重新登录和同步。

### 本地数据与隐私

```text
data/
├── browser-profile/    # 专用 Chrome 登录状态
├── douyin.sqlite       # 作品、指标快照和同步任务
├── media/               # 通过校验的本地转写源（不通过 MCP 返回）
├── staging/             # 可恢复阶段的临时文件
├── exports/            # JSON/CSV 导出
├── reports/            # 本地复盘产物
└── logs/
```

以上目录、数据库、备份和浏览器诊断产物均被 `.gitignore` 排除，请勿使用 `git add -f` 提交。

MCP 不会把 Cookie、`localStorage`、`sessionStorage`、验证码或账号密码返回给 Agent。但作品信息以及你主动查询的创作数据会进入 Agent 上下文；如果 Agent 使用云端模型，还应遵守对应模型服务的数据政策。

导出与清除：

```powershell
douyin-mcp export --format json --period all
douyin-mcp export --format csv --period 30d

# 不带 --yes 时只显示确认提示
douyin-mcp purge
douyin-mcp purge --yes
```

> [!CAUTION]
> `purge --yes` 会删除数据库、数据库备份、导出、报告和专用浏览器 profile，此操作不可恢复。

## 常见问题

<details>
<summary><strong>PowerShell 找不到 <code>douyin-mcp</code></strong></summary>

激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

或者直接运行：

```powershell
.\.venv\Scripts\douyin-mcp.exe doctor
```

</details>

<details>
<summary><strong>PowerShell 阻止安装脚本</strong></summary>

下面的执行策略只作用于本次命令：

```powershell
powershell -ExecutionPolicy Bypass -File .\easy-install.ps1
```

</details>

<details>
<summary><strong>登录后仍提示需要操作</strong></summary>

保持项目专用 Chrome 打开，完成扫码、验证码或安全验证，再重试同步。不要使用日常 Chrome profile 替换项目专用 profile。

</details>

<details>
<summary><strong>返回 <code>profile_in_use</code></strong></summary>

另一个同步进程仍在使用专用浏览器。等待它结束后重试；如果原进程已经退出，锁会在安全确认后自动恢复。

</details>

<details>
<summary><strong>详情同步返回 <code>partial</code></strong></summary>

查看 `failures`、`coverage` 和 `next_cursor`。常见原因包括作品暂不支持详情、页面未展示某项指标，或当前批次仍需继续。

</details>

## 开发者指南

<details>
<summary><strong>展开开发环境、项目结构和验收说明</strong></summary>

### 开发环境

```powershell
git clone https://github.com/Kuhakucai/douyin-mcp.git
cd douyin-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m pip install pytest
Copy-Item .env.example .env
```

### 项目结构

```text
src/douyin_creator_mcp/
├── server.py                 # 无副作用 FastMCP 构造
├── runtime.py                # 实例锁、迁移、executor/worker 生命周期
├── cli.py                    # 用户 CLI
├── config.py                 # 环境配置
├── browser/
│   ├── session.py            # Playwright 与持久化 profile
│   ├── executor.py           # 唯一同步 Playwright 所有者线程
│   ├── commands.py           # 纯值浏览器命令
│   ├── media_observer.py     # 多 representation 收敛
│   ├── extractors.py         # 列表/详情提取和规范化
│   └── profile_lock.py       # 跨进程 profile 锁
├── services/
│   ├── browser_service.py    # 同步、查询、对比、复盘和导出
│   ├── transcript_coordinator.py # 持久后台 job、lease 与恢复
│   ├── transcript_policy.py  # 首次预热、增量入队和分析按需补齐
│   ├── transcript_query.py   # revision 游标与分析上下文
│   └── metrics.py            # 派生指标与排序公式
├── storage/
│   ├── db.py                 # SQLite、迁移与备份
│   ├── transcripts.py        # run/job/asset/revision 事务仓储
│   ├── migrations/           # 有序、带校验和的不可变迁移
│   └── schemas.sql           # 数据表结构
├── content/
│   ├── media.py              # 受控下载、FFprobe 与轨道选择
│   └── asr.py                # FFmpeg 与本地 faster-whisper
└── tools/
    ├── browser_tools.py      # 原 13 个 MCP 工具契约
    └── transcript_tools.py   # 9 个文案 MCP 工具契约

easy-install.ps1              # Windows 一键安装
```

### 扩展原则

1. 页面读取和 DOM 处理放在 `browser/`。
2. 可测试的业务逻辑放在 `services/`。
3. MCP 工具只做参数声明、服务调用和统一错误响应。
4. 原始数据、派生指标和不同采集来源分开保存。
5. 新字段必须定义缺失语义、数据来源、解析版本和测试样例。
6. 不读取或返回浏览器认证材料，不接入未公开私有接口。

### 基础验证

```powershell
python -m compileall -q src
python -m unittest discover -s tests -v
python scripts/validate_transcript_pipeline.py --batch-db data/asr-batch-v1.2/batch.sqlite --read-only
```

### 真实浏览器验收

涉及 DOM、指标提取或浏览器生命周期的变更，还应使用测试账号运行：

```powershell
douyin-mcp doctor
douyin-mcp login --timeout 180
douyin-mcp sync
douyin-mcp details --recent-limit 5
douyin-mcp status
```

验收时应核对登录态复用、页面声明数量、加载数量、解析数量、重复同步幂等性、详情身份校验、覆盖率和失败原因。真实账号数据和验收产物不得提交到仓库。

</details>

## 安全、合规与许可

`douyin-mcp` 是独立维护的第三方开源项目，不是抖音、字节跳动或其关联公司的官方、授权、认证或合作产品。

抖音用户服务协议第 2.4、5.1、5.3 和 7.1 条涉及非商业许可、自动化访问、平台外处理或展示信息、向第三方提供信息以及账号处置风险。用户必须自行确认拥有合法账号、数据访问权，以及自动化访问、平台外处理或展示、向 Agent 或模型服务提供数据所需的全部书面授权。完整说明见[平台合规与非官方声明](PLATFORM_COMPLIANCE.md)。

本项目不会通过 MCP 返回 Cookie、`localStorage`、`sessionStorage`、验证码或账号密码，但作品信息和经营数据可能进入 MCP 客户端及 Agent 上下文。“本机运行”不代表业务数据一定不会离开本机。

项目基于 [GNU Affero General Public License v3.0](LICENSE)（`AGPL-3.0-only`）开源。AGPL 允许商业使用代码，但修改版分发及网络交互场景需要按许可证提供对应源代码。许可证不授予抖音平台访问权、数据权、商业使用平台或数据的权利，也不授予商标权。

Copyright (C) 2026 Kuhakucai。`Kuhakucai` 与 `Puppetsho` 是同一作者使用的 Git 提交身份，详见 [AUTHORS.md](AUTHORS.md)。

## 参与贡献

欢迎提交 Issue 和 Pull Request。开始前请阅读：

- [贡献指南](CONTRIBUTING.md)
- [平台合规声明](PLATFORM_COMPLIANCE.md)
- [开源许可证](LICENSE)

<div align="center">
  <p><strong>如果这个项目对你有帮助，欢迎点一个 Star。</strong></p>
  <p>Built for local, evidence-backed creator analytics.</p>
</div>
