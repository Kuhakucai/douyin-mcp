# 实施计划：浏览器通道可调用闭环

## 1. 目标

在第一阶段 `BrowserSession` 基础上，实现服务层和 MCP 工具层，让本地 Agent 能调用浏览器登录态通道完成以下流程：

1. 打开可见浏览器到抖音创作者中心。
2. 检测当前页面大致登录状态。
3. 打开视频管理页并抽取页面快照。
4. 将快照写入 SQLite 的 `browser_snapshots` 表。
5. 基于最新快照生成基础复盘报告。

## 2. 范围

### 包含

- 新增 `src/douyin_creator_mcp/browser/extractors.py`。
- 新增 `src/douyin_creator_mcp/services/browser_service.py`。
- 新增 `src/douyin_creator_mcp/tools/browser_tools.py`。
- 更新 `src/douyin_creator_mcp/server.py` 注册 browser service 和 tools。
- 新增测试：
  - `tests/test_browser_extractors.py`
  - `tests/test_browser_service.py`
  - `tests/test_browser_tools.py`
- 更新 README、交接文档、项目结构文档。

### 不包含

- 不启动真实浏览器进行自动测试。
- 不解析真实页面里的所有指标字段。
- 不修改 OpenAPI 历史工具主线。
- 不提交或读取浏览器 profile 内容。

## 3. 设计

### 3.1 抽取器

`browser/extractors.py` 提供纯函数和轻量页面适配：

- `detect_login_status(text, url, title)`：
  - 返回 `logged_in`、`login_required`、`verification_required` 或 `unknown`。
- `extract_text_lines(text, limit)`：
  - 清洗页面文本，保留有限行数。
- `extract_video_candidates(lines, limit)`：
  - 从页面文本中保守筛出疑似视频行。
- `extract_page_snapshot(page)`：
  - 从 Playwright page 或 fake page 中提取 title、url、body 文本和候选视频文本。

抽取结果不包含 Cookie、storage、请求头、账号密码。

### 3.2 BrowserService

`BrowserService` 负责业务编排：

- `login_start()`：打开创作者中心首页，返回页面状态。
- `login_status()`：检查当前运行中的浏览器页面状态；未运行时返回 `not_started`。
- `sync_creator_data(account_id="browser-default")`：
  - 打开视频管理页。
  - 抽取页面快照。
  - 写入 `browser_snapshots`。
  - 写入 `sync_jobs` 状态。
- `refresh_report(account_id="browser-default", period="latest")`：
  - 读取最近快照。
  - 生成基础 Markdown 报告。
  - 写入 `reports`。

### 3.3 MCP Tools

新增工具：

- `douyin_browser_login_start`
- `douyin_browser_login_status`
- `douyin_browser_sync_creator_data`
- `douyin_browser_refresh_report`

工具层只做参数转发、成功响应包装、异常响应包装。

## 4. 实施步骤

1. 新增抽取器并覆盖纯函数测试。
2. 新增 `BrowserService` 并覆盖 fake session 测试。
3. 新增 browser tools 并覆盖注册测试。
4. 更新 `server.py`。
5. 更新 README 和交接文档。
6. 运行三条验证命令。

## 5. 风险与控制

| 风险 | 影响 | 控制 |
|---|---|---|
| 抖音页面结构变化 | 候选文本不稳定 | 本阶段只保存原始快照和候选文本，不做强字段映射 |
| 登录态过期 | 同步被阻塞 | 返回 `login_required` 或 `verification_required` |
| 真实浏览器环境不稳定 | 自动测试失败 | 单元测试只使用 fake page/session |
| 泄露敏感数据 | 安全风险 | 不读取或返回 Cookie/storage/profile |

## 6. 验证

```powershell
python -m compileall src
python -m pytest
python -m unittest discover -s tests
```
