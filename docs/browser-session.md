# 浏览器登录态通道

## 工作方式

```text
Agent -> MCP 工具 -> BrowserService -> 专用 Chrome profile
      -> 抖音创作者中心可见页面 -> 可信解析 -> SQLite 快照 -> Agent 查询
```

用户只在首次使用、登录过期或抖音要求验证时操作可见 Chrome。MCP 不读取日常 Chrome profile，也不向 Agent 返回任何认证材料。

## 登录

```powershell
douyin-mcp login --timeout 180
```

MCP 中对应：

1. 调用 `douyin_browser_login_start`；
2. 用户扫码或完成安全验证；
3. 调用 `douyin_browser_login_status`；
4. 登录成功后继续同步。

同步遇到登录过期时返回 `status=user_action_required`，并通过 `next_action.type` 区分 `scan_login` 或 `complete_verification`。

## 列表同步

`douyin_browser_sync_creator_data` 打开作品管理页，每轮滚动前后都提取当前可见卡片，并按平台作品 ID 或来源指纹累计去重。这种方式兼容虚拟列表：即使旧卡片离开 DOM，也不会丢失已经收集的数据。

返回值包括页面声明数量、实际收集数量、停止原因、覆盖率、parser version、采集时间和新鲜度。

首次成功同步会用作品标题和发布时间生成摘要，再以本地随机盐二次哈希，建立轻量账号指纹。后续全量列表与已存锚点没有交集时返回 `account_mismatch`，并在浏览器快照和业务数据写入前停止。升级旧数据库时，只有当前列表与已有作品存在共同锚点才会建立首次绑定。

指纹表不保存昵称、Cookie 或原始作品字段；MCP 状态也不返回锚点哈希、随机盐、本机路径、PID 或锁所有者。账号确认依赖作品集合，因此账号清空全部作品或页面严重加载不完整时可能安全失败，此时应先重新加载；确认更换账号则执行 `douyin-mcp purge --yes` 后重新绑定。

## 详情同步

`douyin_browser_sync_video_details`：

- 默认选择最近 20 条，最多选择 50 条；
- 每次处理 1～10 条，默认 10 条；
- 返回 `next_cursor` 时由 Agent 继续下一批；
- 24 小时内已有可信详情快照时默认命中缓存；
- 指定 `force=true` 才重新打开详情；
- 导航后用详情 URL、平台作品 ID 或标题确认目标作品；
- 无法确认、解析降级或登录过期时不写入详情快照。

真实联调：

```powershell
douyin-browser-smoke sync
douyin-browser-smoke videos --limit 20
douyin-browser-smoke details --recent-limit 20 --batch-size 5
douyin-browser-smoke details --recent-limit 20 --batch-size 5 --cursor 5
```

## 并发和清除

浏览器同步使用 `DATA_DIR/.douyin-mcp.lock` 做跨进程互斥。同一时刻只有一个进程能操作专用 profile。

`douyin-mcp purge --yes` 会先关闭当前浏览器，再取得同一把锁，然后清除 SQLite、备份、报告、导出和 profile，避免与同步并发。

## 配置

```env
DOUYIN_BROWSER_PROFILE_DIR=./data/browser-profile
DOUYIN_BROWSER_AUTO_CLOSE=true
DOUYIN_BROWSER_CHANNEL=chrome
DOUYIN_BROWSER_PAGE_SETTLE_MS=5000
DOUYIN_LIST_CACHE_TTL_HOURS=24
DOUYIN_DETAIL_CACHE_TTL_HOURS=24
DOUYIN_DETAIL_BATCH_SIZE=10
DOUYIN_PROFILE_LOCK_FILENAME=.douyin-mcp.lock
```

`background_first` 可以尝试无头采集，但遇到登录或验证时仍需回到可见浏览器。轻量 V1 不承诺后台模式在所有账号上稳定成功。
