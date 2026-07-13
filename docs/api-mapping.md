# API 映射（历史保留）

当前项目主线已经改为浏览器登录态通道。本文档仅保留官方 OpenAPI 历史骨架，后续开发不要以本文件作为当前 MVP 主线。

所有官方接口路径、scope、请求方式和限制以抖音开放平台最新文档与应用后台为准。本文件用于集中维护 MCP Server 的接口配置，避免在工具函数中散落硬编码。

<!-- api-mapping-json -->
```json
{
  "oauth_access_token": {
    "method": "POST",
    "path": "/oauth/access_token/",
    "scope": null,
    "capability": "oauth.access_token",
    "auth": {},
    "request": {"form": ["client_key", "client_secret", "code", "grant_type"]},
    "pagination": null,
    "rate_limit": "platform_default",
    "mvp_status": "required"
  },
  "oauth_refresh_token": {
    "method": "POST",
    "path": "/oauth/refresh_token/",
    "scope": null,
    "capability": "oauth.refresh_token",
    "auth": {},
    "request": {"form": ["client_key", "grant_type", "refresh_token"]},
    "pagination": null,
    "rate_limit": "platform_default",
    "mvp_status": "required"
  },
  "oauth_renew_refresh_token": {
    "method": "POST",
    "path": "/oauth/renew_refresh_token/",
    "scope": null,
    "capability": "oauth.renew_refresh_token",
    "auth": {},
    "request": {"form": ["client_key", "refresh_token"]},
    "pagination": null,
    "rate_limit": "platform_default",
    "mvp_status": "recommended"
  },
  "get_user_info": {
    "method": "GET",
    "path": "/oauth/userinfo/",
    "scope": "user_info",
    "capability": "user_info",
    "auth": {"access_token": "query", "open_id": "query"},
    "request": {"params": []},
    "pagination": null,
    "rate_limit": "platform_default",
    "mvp_status": "required"
  },
  "fans_list": {
    "method": "GET",
    "path": "/fans/list/",
    "scope": "fans.list",
    "capability": "fans.list",
    "auth": {"access_token": "query", "open_id": "query"},
    "request": {"params": ["cursor", "count"]},
    "pagination": "cursor",
    "rate_limit": "platform_default",
    "mvp_status": "after_permission"
  },
  "fans_data": {
    "method": "GET",
    "path": "/api/douyin/v1/user/fans_data/",
    "scope": "fans.data",
    "capability": "fans.data",
    "auth": {"access_token": "header", "open_id": "query"},
    "request": {"params": []},
    "pagination": null,
    "rate_limit": "platform_default",
    "mvp_status": "after_permission"
  },
  "video_basic_info": {
    "method": "POST",
    "path": "/api/douyin/v1/video/video_basic_info/",
    "scope": "video.basic",
    "capability": "video.basic",
    "auth": {"access_token": "header", "open_id": "json"},
    "request": {"json": ["item_ids"]},
    "pagination": null,
    "rate_limit": "platform_default",
    "mvp_status": "optional"
  },
  "video_data": {
    "method": "GET",
    "path": "capability-only:video.data",
    "scope": "video.data",
    "capability": "video.data",
    "auth": {},
    "request": {},
    "pagination": null,
    "rate_limit": "platform_default",
    "mvp_status": "after_permission"
  }
}
```
