# 实现计划：浏览器结构化视频数据（第四阶段）

## 1. 背景与目标

浏览器通道已经能复用本地登录态并保存页面文本快照，但 Agent 仍无法可靠获取逐条视频数据。真实页面探测证明作品以无限滚动卡片展示，每张卡片具备独立标题、发布时间、时长和公开互动指标，可以通过 DOM 结构解析。

本阶段目标是加载当前作品列表的全部卡片，将可确认字段幂等写入结构化表，并新增分页读取工具。实现必须保持浏览器凭证隔离，不依赖私有接口，不把真实页面样本提交到仓库。

## 2. 需求范围

### 2.1 包含

- 自动滚动作品管理页，直到达到页面声明总数或连续多轮无新增。
- 基于卡片边界解析标题、发布时间、时长、状态、封面和播放/点赞/评论/分享/收藏。
- 解析“万/亿/逗号/短横线”等指标格式。
- 使用“账号 + 发布时间 + 标题”的 SHA-256 确定性本地视频 ID。
- 幂等 upsert `videos` 和当日 `video_metrics`。
- 新增 `douyin_browser_list_videos` MCP 工具及 CLI `videos` 子命令。
- 更新浏览器快照摘要和报告结构化统计。
- 增加脱敏结构夹具、单元测试、真实浏览器验证和文档。

### 2.2 不包含

- 不调用抖音私有内部 API，不提取 React 内部状态。
- 不承诺获得页面未展示的完播率、平均观看时长、粉丝增长等指标。
- 不提供写操作，不发布、编辑或删除作品。
- 不提交真实账号数据、真实标题或完整 HTML。
- 不解决官方 item ID 缺失问题；后续若页面公开稳定 ID 再迁移。

## 3. 现状分析

`extract_page_snapshot()` 当前只读取 body 文本并用关键词产生候选，无法保持作品卡片边界。`BrowserService.sync_creator_data()` 保存快照后即完成任务，没有写入 `videos` 或 `video_metrics`。两个结构化表已存在，主键均为文本，适合使用确定性 ID 完成无迁移 upsert。

真实页面首屏渲染 12 个卡片，滚动后依次增加到 24、36、48、60、62，连续滚动后稳定。卡片可通过发布时间叶子节点反向找到同时包含一组指标节点的最近祖先；标题、时间、状态和指标具有稳定语义及类名前缀。页面没有稳定公开 ID，因此需要本地合成 ID，并在文档中明确标题或发布时间被编辑时可能生成新记录。

## 4. 方案设计

### 4.1 总体思路

将浏览器页面处理拆为三层：滚动加载、DOM 原始字段抽取、Python 标准化。JavaScript 只负责按卡片边界读取当前可见 DOM；数值、日期、时长、URL清理和字段校验在 Python 中完成，便于单元测试。服务层只接收标准化记录并在单事务中 upsert。

### 4.2 详细设计

- `load_all_video_cards(page, max_scrolls=30, stable_rounds=3, wait_ms=1000)`：读取页面总数和当前卡片数，滚动到底部；达到总数或连续稳定后停止，返回加载统计。
- `extract_structured_videos(page)`：执行只读 JS，按日期节点定位最近卡片祖先；提取标题、发布时间文本、时长文本、状态、封面 URL 和指标标签/值。
- `parse_metric_count()`：支持整数、逗号、小数万/亿和 `-`。
- `parse_duration_seconds()`：支持 `MM:SS`、`HH:MM:SS`。
- `parse_publish_time()`：按 Asia/Shanghai 解析页面时间并转 Unix 秒。
- DOM 输出字段：`title`、`publish_time`、`duration`、`status`、`cover_url`、`play_count`、`like_count`、`comment_count`、`share_count`、`collect_count`。
- `BrowserService.sync_creator_data()`：先做登录判断；已登录时加载全部卡片、重新抽取快照和结构化记录，再保存快照及 upsert 数据。
- 视频 ID：`sha256("browser_dom|account_id|publish_time|title")`。
- 指标 ID：`sha256("browser_dom|video_id|metric_date")`，同日重复同步覆盖最新值。
- `BrowserService.list_videos(account_id, limit, offset)`：返回总数及按发布时间倒序的视频和最新一条指标；limit 限制为 1-100，offset 不小于 0。
- MCP 工具 `douyin_browser_list_videos` 调用服务方法。
- CLI `videos --account-id --limit --offset` 输出同一结构。
- 快照摘要增加 `structured_video_count`、页面声明总数和实际加载数。
- 浏览器报告增加结构化视频数及播放/点赞/评论/分享汇总，不再仅依赖候选文本。
- 封面 URL 在保存前移除查询参数和片段；不输出页面全文。

### 4.3 涉及变更的文件清单

- `src/douyin_creator_mcp/browser/extractors.py` — 滚动加载、DOM 抽取和字段标准化。
- `src/douyin_creator_mcp/services/browser_service.py` — 结构化 upsert、分页读取、同步结果和报告汇总。
- `src/douyin_creator_mcp/tools/browser_tools.py` — 注册视频分页读取工具。
- `src/douyin_creator_mcp/browser_smoke.py` — 增加 `videos` 子命令。
- `src/douyin_creator_mcp/storage/schemas.sql` — 增加结构化表账号索引。
- `tests/fixtures/browser_video_cards.json` — 脱敏原始卡片字段夹具。
- `tests/test_browser_extractors.py` — 数值、日期、时长、滚动和标准化测试。
- `tests/test_browser_service.py` — 幂等落库、指标更新、分页及报告测试。
- `tests/test_browser_tools.py` — 新 MCP 工具测试。
- `tests/test_browser_smoke.py` — CLI `videos` 测试。
- `README.md`、`docs/browser-session.md`、`docs/project-structure.md`、`docs/agent-handoff.md`、`AGENT_HANDOFF.md` — 更新能力、字段、验证和交接信息。

## 5. 实施步骤

1. **实现字段标准化函数** — 用脱敏夹具覆盖数值、时间、时长和 URL。
2. **实现滚动和 DOM 卡片抽取** — 模拟页面序列验证达到总数与稳定轮次两种停止条件。
3. **实现结构化幂等落库** — 单事务 upsert 视频和同日指标，重复同步验证记录数不增长、值可更新。
4. **增加 Agent 读取入口** — 注册 MCP 工具和 CLI 子命令，验证分页与参数边界。
5. **升级快照和报告** — 输出结构化数量、加载统计和指标汇总。
6. **更新文档和四阶段交接** — 明确可用字段、合成 ID 风险和下一步。
7. **运行自动化验证** — compileall、pytest、unittest、CLI help、diff check。
8. **运行真实 Chrome 验证** — 同步全部作品并使用 `videos` 读取，确认落库数量、幂等和代表性指标。

## 6. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| CSS module 哈希变化 | 选择器失效 | 使用语义文本、结构边界和类名前缀组合；零解析时保留快照并返回说明 |
| 无限滚动网络缓慢 | 未加载全部作品 | 总数目标 + 最大轮数 + 连续稳定轮次三重收敛，并返回加载统计 |
| 无官方作品 ID | 标题编辑后可能重复 | 确定性本地 ID，明确限制；保留 item_id/video_id 为空 |
| 指标单位解析错误 | 数据数量级错误 | 对万、亿、小数、逗号、短横线建立表驱动测试 |
| 同步中途异常 | 部分写入 | 页面抽取完成后再用单事务 upsert |
| 封面 URL 含签名参数 | 泄露或失效 | 保存前移除 query 和 fragment，无法安全保留时置空 |
| 页面有 0 个作品 | 被误判为解析失败 | 同时读取页面声明总数；总数为 0 时视为合法空结果 |

## 7. 测试策略

- 单元测试：标准化函数、DOM 原始结果映射、滚动收敛、参数校验。
- 服务测试：首次 upsert、同日重复 upsert、指标更新、多账号隔离、分页排序、空账号。
- 工具测试：MCP 注册和成功/异常响应。
- CLI 测试：`videos` 参数分发和 JSON 输出。
- 回归测试：全部现有测试继续通过。
- 手动验证：真实页面加载数达到页面声明总数，重复同步后 `videos` 数量不增加，并能读取最新指标；真实数量不写入仓库。

## 8. 回滚预案

回退 extractor、service、tool、CLI 和文档改动，删除新增测试夹具与测试。新增索引可保留且不影响旧逻辑；如需完全回滚可删除索引。已写入的 `source=browser_dom` 视频和指标可按 source 条件从本地数据库删除，不影响历史 OpenAPI 数据。
