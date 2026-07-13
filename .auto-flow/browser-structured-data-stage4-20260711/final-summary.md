# 项目需求实现总结：浏览器结构化视频数据（第四阶段）

## 1. 需求概述

本阶段将浏览器通道从页面文本快照升级为 Agent 可直接消费的结构化视频数据。同步会加载当前作品列表中的全部卡片，解析页面可见字段，幂等写入 SQLite，并通过 MCP 工具和 CLI 分页读取。

## 2. 最终方案

页面加载采用总数目标、连续稳定轮次和最大滚动次数三重收敛。DOM JavaScript 只读取卡片原始字段，Python 负责时间、时长、万/亿数值和 URL 标准化。无官方作品 ID 时，以账号、发布时间和标题生成 SHA-256 本地 ID；同日指标使用视频 ID 和日期生成确定性主键。

同步状态区分 `completed`、`partial` 和 `blocked`。完整加载后 upsert `videos`、`video_metrics`；Agent 通过 `douyin_browser_list_videos` 或 CLI `videos` 分页获取最新结构化数据。

## 3. 实施成果

- 全量无限滚动和 DOM 卡片解析。
- 标题、发布时间、时长、状态、封面和五类互动指标标准化。
- 视频和同日指标幂等落库。
- 新增 MCP `douyin_browser_list_videos`。
- 新增 CLI `videos`。
- 快照摘要和报告增加结构化数量、加载统计及指标汇总。
- 新增脱敏夹具和结构化链路测试，总计 53 项测试及 5 个参数化子测试。
- README、PRD、实施方案和 Agent 交接文档已同步。

## 4. 验证结果

- 自动化：compileall、pytest、unittest、diff check 全部通过。
- 真实 Chrome：从首屏加载到页面声明的全部作品。
- 首次同步：声明数、加载数和解析数一致。
- 重复同步：作品与当日指标记录数保持不变，幂等通过；真实数量不进入仓库。
- 分页读取和最新快照报告生成成功。
- Git 未包含真实数据库、profile、页面快照或报告。

## 5. 遗留问题与后续建议

本地 ID 依赖标题和发布时间，作品编辑后可能产生新记录；后续应在页面出现稳定 ID 时设计迁移。DOM 类名前缀变化会使同步进入 `partial`，下一阶段应增加结构版本诊断和回退选择器。页面未展示的深度经营指标保持为空。

## 6. 关键决策回顾

- 只读取用户可见 DOM，不调用私有内部 API。
- JavaScript 负责结构边界，Python 负责可测试的字段标准化。
- 只有完整加载的数据才视为 `completed`，部分结果保留快照并标记 `partial`。
- 同日指标更新而不重复插入，不同日期保留历史快照。
- 真实数据只进入被忽略的本地 `data/`。

## 7. 回滚预案

回退 extractor、service、tool、CLI、schema 索引和文档改动，删除新增夹具与测试。已同步的 `source=browser_dom` 数据可按 source 从本地 `videos`、`video_metrics` 删除，不影响历史 OpenAPI 骨架数据。
