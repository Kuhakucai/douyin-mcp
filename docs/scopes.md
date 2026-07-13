# Scope 与能力

首版默认 scope：

```text
user_info
```

能力状态：

| 状态 | 含义 |
|---|---|
| available | 当前授权和应用能力已确认可用 |
| missing | 当前 scope 或应用权限缺失 |
| unknown | 尚未确认，需要后台申请或真实接口探测 |
| limited | 可用但存在条件限制 |

首版只把 `user_info` 作为必做能力。视频经营数据、粉丝画像、粉丝来源等能力需要在抖音开放平台后台确认后再启用。
