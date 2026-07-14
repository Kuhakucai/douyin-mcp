# AI 安装指南（Windows）

本指南让支持终端和 MCP 配置的 Agent 安装本项目。用户可以直接说：

```text
帮我克隆并安装 https://github.com/Kuhakucai/douyin-mcp 项目
```

## Agent 执行边界

用户提出上述请求后，可执行克隆、创建项目内 `.venv`、安装依赖、创建缺失的 `.env`、运行诊断，并读取 `init` 的配置输出。

以下操作必须获得用户明确同意后再进行：修改 Agent/MCP 客户端配置文件、启动可见浏览器、登录或扫码、同步真实抖音数据、覆盖已有 `.env`、删除 `data/`。

不要读取、展示、提交或发送 `.env`、`data/`、浏览器 profile、Cookie 或其他认证材料。

## 自动安装流程

1. 确认系统是 Windows，且已安装 Python 3.11+ 和 Google Chrome。
2. 克隆仓库并进入项目目录：

   ```powershell
   git clone https://github.com/Kuhakucai/douyin-mcp.git
   cd douyin-mcp
   ```

3. 运行安装脚本。若当前 PowerShell 的执行策略阻止脚本，使用以下命令；该设置仅作用于这一次调用：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\easy-install.ps1
   ```

   开发或测试项目时使用：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\easy-install.ps1 -Dev
   ```

4. 安装脚本会创建或复用 `.venv`，安装运行依赖，且只在 `.env` 不存在时从 `.env.example` 创建它。脚本随后运行 `init` 和 `doctor`，输出 JSON MCP 配置。

5. 向用户展示 `init` 输出的 `mcp_config`，询问是否将其写入正在使用的 MCP 客户端。写入时必须使用输出中的绝对 Python 路径和数据目录，不能假定其他人的安装路径。

## MCP 配置与验收

在用户确认配置完成并重启 MCP 客户端后，让 Agent：

1. 列出 `douyin_browser_*` 工具；
2. 调用 `douyin_browser_get_status`；
3. 未登录时，询问用户是否打开可见浏览器扫码；
4. 登录成功后，调用 `douyin_browser_sync_creator_data`；
5. 调用 `douyin_browser_list_videos`，`limit` 设为 3。

成功标准是 MCP 工具可被发现，响应包含 `ok: true`，并且未登录时返回可处理的 `user_action_required` 而不是服务崩溃。

## 故障处理

- 找不到 `douyin-mcp`：使用 `./.venv/Scripts/douyin-mcp.exe`，或在当前窗口执行 `./.venv/Scripts/Activate.ps1` 后重试。
- Python 版本不足：安装 Python 3.11+，重新运行脚本。
- Chrome 相关错误：确认 Google Chrome 已安装；只有在需要 Playwright 自带 Chromium 时才重新运行脚本并加 `-InstallChromium`。
- `doctor` 未通过：不要绕过错误。将其 JSON 输出提供给维护者或 Agent 继续诊断。
