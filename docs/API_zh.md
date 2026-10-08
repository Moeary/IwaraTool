# IwaraTool API 说明（简体中文）

[English](./API.md) | [日本語](./API_ja.md)

本文档说明 IwaraTool 使用的 Iwara REST API，以及每个功能会发出哪些请求。Iwara 没有官方公开 API：以下内容来自对线上站点的实测、本项目的测试，以及开源客户端 [LoveIwara](https://github.com/FoxSensei001/LoveIwara) 与 [gallery-dl](https://github.com/mikf/gallery-dl/blob/master/gallery_dl/extractor/iwara.py) 的实现。每个接口标注 **已验证**（本项目对线上接口实测过）或 **参考**（取自其他客户端源码，本项目未用真实账号实测）。

目录：[1 约定与认证](#1-约定与认证) · [2 读取接口](#2-读取接口) · [3 账号操作](#3-账号操作) · [4 功能与请求对照](#4-功能与请求对照) · [5 搜索下载](#5-搜索下载) · [6 Fluent 搜索页](#6-fluent-搜索页) · [7 筛选](#7-筛选) · [8 UI 支持的输入 URL 类型](#8-ui-支持的输入-url-类型) · [9 标签抓取脚本](#9-标签抓取脚本为后续一键标签筛选做准备) · [10 NFO 生成与 Emby 兼容](#10-nfo-生成与-emby-兼容)

## 1. 约定与认证

### 主机与请求头
- API 主机：`https://api.iwara.tv`。`https://apiq.iwara.tv` 提供同样的路由，用作视频详情、关注列表和标签抓取的备用地址。
- 媒体主机：`https://i.iwara.tv`（封面、头像、图片作品的图片）。
- 每个请求都带 `Accept: application/json, text/plain, */*`、`Origin: https://www.iwara.tv`、`Referer: https://www.iwara.tv/` 和 `X-Site: www.iwara.tv`；HTTP 会话使用 `cloudscraper`（Chrome / Windows 配置），以通过 Cloudflare 的浏览器校验。
- 错误以 JSON `{"message": "errors.xxx"}` 返回（如 `errors.privateVideo`、`errors.notFound`、`errors.badRequest`）。客户端会把 HTTP 与网络失败转成可读的提示，不会把失败当作空结果。

### 请求节奏与重试
- 同一主机的请求至少间隔 `request_min_interval_ms`（默认 200 毫秒）。
- `429`、`502`、`503`、`504` 与临时网络错误最多重试 `request_max_retries` 次（默认 2），指数退避（1 秒、2 秒、4 秒…最长 30 秒）；若响应带 `Retry-After`，最多按 30 秒等待。
- 这两项在“应用设置 → 系统与维护 → 请求节奏”。后台任务（首页栏目、封面、订阅刷新）每个工作线程使用独立的 HTTP 会话，沿用相同的 token 与代理，互不排队。

### 登录 — 已验证
- `POST /user/login`，请求体 `{"email": "<用户名或邮箱>", "password": "<密码>"}`；成功返回 `{"token": "<jwt>"}`。
- 所有需要登录的请求带 `Authorization: Bearer <token>`。
- Token 缓存在 `data/config.ini`（`auth_token`、`auth_token_saved_at`）。启动时优先使用缓存 token，没有 token 才回退账号密码登录。
- 公开内容无需登录；登录后可访问私有视频、订阅流，以及下文的账号字段（`liked`、`following`）。

## 2. 读取接口

### 视频与图片详情 — 已验证
- `GET /video/{id}`：单个视频。程序读取的字段：`id`、`title`、`body`、`rating`（`general` | `ecchi`）、`numLikes`、`numViews`、`numComments`、`createdAt`、`user`（`id`、`name`、`username`、`avatar`）、`tags[]`（`id`、`type`）、`file`（`id`、`duration`、`height`）、`fileUrl`、`thumbnail`（封面序号）、`embedUrl`（YouTube 嵌入无法下载），登录后还有 `liked`。
- `GET /image/{id}`：单个图片作品，字段同上，另有 `numImages` 与 `files[]`（`id`、`name`、`width`、`height`）。图片地址：`https://i.iwara.tv/image/original/{file.id}/{file.name}` 与 `https://i.iwara.tv/image/thumbnail/{file.id}/{file.id}.jpg`。
- 封面：`https://{host}/image/original/{file.id}/thumbnail-{NN}.jpg`；首页卡片把 `original` 换成 `thumbnail`（约 10 KB 的小图）。头像：`https://i.iwara.tv/image/avatar/{id}/{name}`。
- `GET /video/{id}/related?limit=12`、`GET /image/{id}/related?limit=12`：相关作品（忽略 SFW / NSFW 选择，程序在本地过滤）。
- `GET /video/{id}/comments?page=0&limit=20`、`GET /image/{id}/comments?...`：评论；加 `&parent={评论id}` 返回该评论的回复。响应 `{count, results[]}`。

### 下载源 — 已验证
- 输入：视频详情中的 `fileUrl`。携带 `Authorization`（已登录时）和 `X-Version` 请求头对它发 `GET`，返回 `{name, src: {view, download}}` 列表。
- `X-Version = sha1("{文件 uuid}_{expires}_{salt}")`，uuid 是 `/file/` 之后的路径段，`expires` 来自 URL 查询串。站点会不定期更换 salt，所以客户端依次尝试内置 salt 与用户补充的 salt（应用设置，或 `data/x_version_salts.json`）。
- 画质回退：从偏好画质起 `Source → 540 → 360`。`fileUrl` 缺失表示私有视频（`errors.privateVideo`）、外站嵌入或作品不可用。

### 列表：`/videos` 与 `/images` — 已验证
`GET /videos` 与 `GET /images` 参数相同：

| 参数 | 含义 |
|---|---|
| `page`、`limit` | 从 0 开始的页码与每页数量（程序发送 1–100；首页取 24，搜索页 32，“查看更多”列表 32）。 |
| `sort` | `date`、`trending`、`popularity`、`views`、`likes`。 |
| `rating` | `general`（SFW）或 `ecchi`（NSFW）；省略则两者都返回。 |
| `tags` | 逗号分隔的标签 ID，必须全部命中。**复数**：线上接口忽略单数 `tag`（程序会把旧输入转换成 `tags`）。`/images` 不支持。 |
| `user` | 用户 ID（不是用户名），先用 `GET /profile/{username}` 解析。 |
| `subscribed=true` | 已登录账号的订阅流（需要 token）。 |

响应：`{count, limit, page, results[]}`。`count` **不是**稳定的总数：满页时为 `(page + 1) × limit + 1`，程序把它当作“还有下一页”，而不是显示页数。不存在的标签可能返回 HTTP 500，因此服务器错误不会被当作“没有结果”。

### 文本搜索：`/search` — 已验证
`GET /search?type={videos|images|users|playlists}&query=…&sort=…&page=…&limit=…`

- 各类型的排序：`videos`、`images` 支持 `relevance`、`date`、`views`、`likes`；`users`、`playlists` 只支持 `relevance`、`date`（`views` / `likes` 会返回 `errors.badRequest`，客户端会退回相关度）。
- 该接口忽略 `rating`，所以 SFW / NSFW 在结果上本地过滤。
- 空 `query` 不会发送：视频浏览用 `/videos`，图片浏览用 `/images`。

### 用户 — 已验证
- `GET /profile/{username}` → `{user: {id, name, username, avatar, …}}`。已登录时 `user` 还带 `following`（你是否关注了他）、`followedBy`、`friend`——即程序里的“已在 Iwara 关注”状态。
- `GET /user/{id}/following?page=…&limit=50`：用户关注的账号（“导入关注作者”使用）。

### 播放列表 — 已验证
- `GET /playlist/{id}?page={n}`：播放列表中的视频。播放列表卡片来自 `/search?type=playlists`。

### 标签 — 已验证
- `GET https://apiq.iwara.tv/tags?filter={A-Z0-9}&page={n}`：标签目录（见第 9 节）。

## 3. 账号操作

下面三类都需要 token；未登录时客户端不会发送，而是提示“请先登录”。任何 `2xx` 响应都视为成功，不使用响应体。**参考**（来自 LoveIwara 客户端；本项目的测试使用模拟会话，没有用真实账号）。

| 操作 | 请求 |
|---|---|
| 给视频 / 图片点赞 | `POST /video/{id}/like` · `POST /image/{id}/like` |
| 取消点赞 | `DELETE /video/{id}/like` · `DELETE /image/{id}/like` |
| 在网站上关注作者 | `POST /user/{userId}/followers` |
| 取消关注 | `DELETE /user/{userId}/followers` |

- 详情页显示的点赞状态是 `GET /video/{id}` / `/image/{id}` 里的 `liked`；点击成功后点赞数在本地即时变化。
- 网站上的关注与程序的**本地订阅**（`data/subscriptions.db` 中的一行）是两回事：作者页并排显示两种状态，并分别修改。“导入关注作者”会把网站上的关注复制成本地订阅。

## 4. 功能与请求对照

| 功能 | 请求 |
|---|---|
| 首页 — “我的订阅” | `/videos?subscribed=true&sort=date[&rating]` 与 `/images?…`（需登录） |
| 首页 — 热门栏 | `/videos?sort=trending\|popularity\|date[&rating]`、`/images?…` |
| 首页 — 标签栏 | `/videos?tags=<解析后的 ID>&sort=…[&rating]`（名称先经本地标签词典） |
| 首页 — 关键词栏 | `/search?type=videos\|images&query=…&sort=…` |
| 首页 — 作者栏、作者页 | 每个会话先 `/profile/{username}` 取一次 ID，再 `/videos?user={id}&sort=date` / `/images?…` |
| 首页 — 条数与缓存 | 每栏取 24 条（第 0 页）。每栏、每个标签页、每种分级各存一份在 `data/home_feed_cache.json`；缓存的“签名”是按顺序排列的作品 ID，所以重新检查得到相同的 ID 时不会动卡片。重新检查的间隔：应用设置 → 首页（默认 15 分钟，0 = 仅手动）。 |
| “在搜索页查看更多” | 对应的搜索页查询（类型、标签或关键词、排序）；作者栏打开程序内作者页 |
| 作品详情 | `/video/{id}` 或 `/image/{id}`，然后 `/related`、`/comments`；点赞 → `POST|DELETE …/like` |
| 作者页 / 状态栏 | `/profile/{username}`（关注状态）+ `/videos?user=…`；关注 → `POST|DELETE /user/{id}/followers` |
| 订阅刷新 | `/videos?user={id}&sort=date`（增量：遇到第一个已知 ID 即停）、`/playlist/{id}`、`/videos?subscribed=true`；列表行没有文件 ID 时用 `/video/{id}` 补封面 |
| 搜索页 | `/videos`、`/images`、`/search`、`/profile/{username}`、`/playlist/{id}`（见第 6 节） |
| 下载 | `/video/{id}` → `fileUrl` → 下载源（第 2 节） |

## 5. 搜索下载（新增）

程序已支持在下载输入框直接粘贴搜索 URL。

### 支持格式
- `https://api.iwara.tv/videos?...`
- `https://www.iwara.tv/videos?...`
- 示例：
  - `https://api.iwara.tv/videos?tags=2d&sort=date`
- `https://www.iwara.tv/videos?tags=2d&sort=date`

网页路由与 JSON 接口都使用复数 `tags`（2026-10-03 实测：单数 `tag` 会被忽略并返回未筛选的结果）。IwaraTool
仍接受旧的单数写法，并在 API 边界统一转换为 `tags`。

### 行为
- 解析 URL 查询参数
- 分页请求 `GET /videos`
- 自动将结果中的视频 ID 入队

### 搜索专属数量上限
- 设置项：`Search Download Limit`
- 配置键：
  - `search_limit_enabled`（bool）
  - `search_limit_count`（int）
- 启用后只会入队前 `N` 条搜索结果
- 若 URL 自带 `limit`，最终上限为 `min(url limit, setting limit)`

## 6. Fluent 搜索页

主窗口侧栏中的“搜索”页是面向浏览和批量入队的 Fluent 搜索界面，和下载工作台里的“搜索 URL 入队”互不替代。数据源默认是 Oreno3D 在线搜索，也可以切换为 Iwara 实时 API。

### 搜索类型

- 关键词搜索：Iwara 非空关键词调用 `GET https://api.iwara.tv/search?type=videos&query=...&sort=...&page=0`，原样保留引号短语及其他输入；留空则调用 `/videos` 浏览列表。Oreno3D 模式请求 `GET https://oreno3d.com/search?keyword=...&sort=latest&page=1`，打开结果时解析为 Iwara 页面。
- 作者：切换到 Iwara 实时 API 后按用户名请求作者主页，展示作者简介和视频数量；无关键词格式的自由文本走 `/search?type=users`。
- 打开作者页：右键菜单“打开作者页”在能确定 Iwara 账号时进入程序内作者页（显示是否已在程序内订阅、是否已在 Iwara 关注，见第 3 节），否则回退到来源站的作者页；“在浏览器打开作者页”始终打开来源站。
- 标签搜索：Iwara 使用 `/videos?tags=...`，多个标签用逗号连接，要求同时命中。完整中日英译名会通过词典精确映射为标签 ID；同一译名对应多个 ID 时要求选择具体候选，不按词典顺序盲选；未识别的原始 ID 保留，不作模糊替换。Oreno3D 使用其独立的标签映射与实体路由，未知名称回退到其关键词入口。
- 每个数据源和搜索类型各自保留输入与排序草稿；切换模式不会把标题关键词当作标签提交。搜索历史仍记录原始输入及其类型。
- 播放列表：输入播放列表 ID 或 `/playlist/{id}` 链接，展示其中的视频。

### 排序与下载规则

- Iwara 关键词排序支持 `date`、`relevance`、`views`、`likes`；视频列表及标签支持 `date`、`trending`、`popularity`、`views`、`likes`。界面随模式更新选项。
- Iwara 结果保留服务端顺序，不对单页重新按日期或热度排序，也不使用本地关键词子串匹配删去简介命中等有效结果。下一页沿用相同查询与排序；尚未提交的新条件在翻页时从第一页开始。
- `/videos` 的 `count` 可能是“页偏移 + 页容量 + 1”的下页标记；`/search` 的真实总数独立处理。服务器采用不同页容量时，仅保留可确认的下一页导航。HTTP 错误或无效结果结构显示失败原因，不伪装成零结果。
- 搜索页不再复制下载工作台的高级筛选字段；页面内的“下载规则”选择器直接复用现有规则。
- 选中规则后会立即应用项目已有的元数据筛选、命名模板、封面/NFO 和下载行为，并同步为默认规则。

### Iwara 搜索核验（2026-10-03）

使用项目 `IwaraAPI` 的公开、未登录会话进行只读请求，未下载媒体：

- `/videos?q=zz_iwaratool_no_such_query_13` 与无关键词列表返回相同的 4 个 ID；`/search?type=videos&query=zz_iwaratool_no_such_query_13` 返回 0 条。旧关键词接口实为未筛选列表。
- `animation` 在 `/search` 的前两页各返回 8 条、总数均为 5796，页间 ID 无重复；`date`、`views`、`likes` 按相应字段降序，`relevance` 与日期顺序不同。总数仅代表核验当时的快照。
- 单标签 `tags=genshin_impact` 的 8 条结果全部带此标签，改成 `tag` 仅 1 条带此标签；`tags=genshin_impact,hatsune_miku` 的 16 条全部包含两者。当前接口使用复数 `tags`，旧的单数转换已移除；批量查询入口同步修复。
- 不存在的 `tags` 值可能返回 HTTP 500，不能将服务器错误当作零匹配；优先选择标签候选中的有效 ID。
- 本地词典的“原神”对应多个 ID，原先优先得到 `ganshin`，与 `hatsune_miku` 组合会返回 0 条；明确使用 `genshin_impact,hatsune_miku` 能返回结果。提交时已增加歧义检查，避免把不同标签混为一谈。
- 交叉参考：[LoveIwara 搜索请求实现](https://github.com/FoxSensei001/LoveIwara/blob/master/lib/app/services/search_service.dart)、[排序定义](https://github.com/FoxSensei001/LoveIwara/blob/master/lib/app/ui/pages/search/widgets/filter_config.dart)、[gallery-dl Iwara 接口实现](https://github.com/mikf/gallery-dl/blob/master/gallery_dl/extractor/iwara.py)。

### 图片缓存

- 视频封面与作者头像按需下载。
- 搜索页图片缓存目录：`data/img/search/`。
- 订阅页视频封面缓存目录：`data/img/sub/`；会优先复用历史记录中已有的 Iwara 封面并复制到此目录，不覆盖下载规则生成的封面。旧版本的 `data/img/sub_video/` 会在读取对应条目时迁移式复用。
- 用户正在看的封面会排在后台封面（如订阅总览里各行的封面条）之前请求；下载失败的封面在下次被请求时会重试。
- 作者、订阅流等列表接口若只返回 Iwara ID，刷新封面时会调用 `GET /video/{id}` 补齐 `file.id`、`fileUrl` 和 `thumbnail`，再按 `https://{file-host}/image/original/{file-id}/thumbnail-{index:02d}.jpg` 下载到上述目录；成功解析的地址会回写订阅数据库。
- 订阅作者头像缓存目录：`data/img/avatar/`；新文件以 `username` 开头。启动时会把旧版 `data/img/avatar_*` 文件复制迁移到新目录，并更新订阅源记录，旧文件不会被强制删除。
- 文件使用 URL 指纹命名，并通过临时文件原子替换，网络失败只保留占位图，不影响搜索结果。

### 刷新与缓存并发设置

- 设置页的“刷新与封面性能”卡片控制以下 UI 配置键：
  - `cover_download_workers_v1`：搜索页和订阅页封面获取并发数，范围为 `1–16`，默认 `6`；Oreno3D 缩略图与 Iwara API 补齐的封面都使用该并发池。
  - `subscription_refresh_workers_v1`：启用订阅源刷新并发数，范围为 `1–8`，默认 `3`；每个刷新任务使用独立的 API 会话，并复用当前 token 与代理。
  - `subscription_incremental_refresh_v1`：账户订阅增量刷新开关，默认开启。
  - `home_cache_minutes_v1`：首页栏目缓存多久后才重新向网站检查，默认 `15` 分钟，`0` 表示只在手动刷新时检查。
- 增量刷新只对账户订阅流和从账户导入的作者生效：接口按最新排序分页，遇到本地已知的 Iwara 视频 ID 即停止；首次没有已知 ID 时仍会建立完整本地索引。播放列表和本地作者源保持原有完整拉取语义。
- 并发仅作用于网络请求；SQLite 写入仍由订阅存储层串行保护，避免多个刷新任务破坏本地数据。

### 结果视图

- 网格模式使用与首页、订阅页共用的“封面大小”滑块（同一个控件、同一份保存的大小），每行列数由可用宽度自动计算。
- 列表模式不加载缩略图，以表格展示 Iwara ID、标题、作者、播放量、点赞数、评论数、发布时间、标签和链接；字段可通过“字段设置”自定义。
- “字段设置”复用订阅页的列显示、顺序和宽度持久化机制。

### Oreno3D 在线搜索

- 搜索页只请求当前页，不把 Oreno3D 的几十万条视频复制到本地数据库；本地仅保存搜索图片缓存。
- 当前结果页先按需请求 Oreno3D 详情页获取 Iwara ID，再调用 `GET https://api.iwara.tv/video/{iwara_id}` 补齐 Iwara 标题、作者、播放量、点赞数、评论数、标签和缩略图；最终链接直接拼成 `https://www.iwara.tv/video/{iwara_id}`，不解析 Iwara 网页按钮，也不会把 Oreno3D 详情页作为最终页面。这仍然只覆盖用户当前浏览的页面，不会建立全站镜像。
- 搜索结果的 Iwara 桥接按条目异步回传：先拿到某条 Iwara ID 就可以打开或入队，元数据随后独立补齐。设置页可在“后台预解析”和“点击或下载时解析”之间切换，并可将 Oreno3D ID 解析并发数设为 1–8；按需模式只在打开或下载时请求详情页。
- Oreno3D 结果保留原站缩略图作为唯一搜索封面；Iwara 元数据中的缩略图只记录在原始数据中，不再次下载，避免同一张卡片先显示 Oreno3D 图片、随后又切换为 Iwara 图片。
- 搜索类型会随数据源约束：Oreno3D 只显示视频和标签，作者/播放列表由 Iwara 实时 API 提供；切换数据源时不支持的类型会自动回到视频。
- Oreno3D 原生每页约 36 条结果；搜索页保留其页边界，使用下载规则下方结果工具栏中的左右按钮切换页面，而不是将所有页面堆叠到同一列表。
- Oreno3D 搜索表单实测为 `GET /search`，表单只提交 `keyword`；排序和分页由额外的 `sort`、`page` 查询参数控制。已确认的排序值为 `hot`（急上昇）、`favorites`（高評価）、`latest`（新着）和 `popularity`（人気）。`page` 从 1 开始，结果页通常返回 36 条卡片。
- `keyword` 是原站的自由文本检索，会匹配标题、作者或标签显示文本；可直接输入日文原生标签（如 `ダンス有り`、`淫乱`、`アナル責め`）或英文关键词（实测 `ass` 可返回结果）。空格和逗号组合在实测中可用于多词检索，例如 `ダンス有り 淫乱` 与 `ダンス有り,淫乱` 返回相同数量的结果；`#标签` 和 `|` 不是已确认的特殊语法，应按普通字符处理。
- Oreno3D 的标签目录不是 `/search` 的 `tag_id` 参数：总目录为 `/tags`，分组页为 `/tag-groups/{group_id}`，具体标签页为 `/tags/{tag_id}?sort=latest&page=1`。因此需要精确按 Oreno3D 标签筛选时，应使用标签页的数字 ID；搜索页的标签模式则使用 Oreno3D 的 `keyword` 自由文本入口。
- [iwara-search](https://github.com/beautifulrem/iwara-search) 选择本地 SQLite/FTS5 镜像是为了实现原站没有的复杂布尔和范围筛选；本项目当前优先保持在线结果与 Oreno3D 同步，不启用该大规模镜像。

### Oreno3D 视频与作者回退（2026-10-03 验证）

- 部分旧详情页的 `h1.video-h1` 存在但内容为空，视频来源和作者链接仍在。旧解析器因标题为空直接报错，导致两类链接一起丢失；现在以来源 ID 代替空标题继续解析，缺少详情页标记的响应仍视为异常。
- 视频来源从正文 `.video-figure a` 与 `a.video-watch-btn2` 提取，校验 Iwara 域名及 `/video/{id}` 路径，不扫描作者评论或侧栏链接。可交叉参照 [LoveIwara 详情解析器](https://github.com/FoxSensei001/LoveIwara/blob/master/lib/app/services/oreno3d_html_parser.dart) 的播放入口选择器。
- Oreno3D 作者 ID、名称与 URL 随链接解析结果传到界面，不依赖 Iwara 元数据请求成功。仅 ID 响应和失败响应不得清除已有的 `_iwara_metadata_loaded` 状态。
- 自动补全时，原视频没有可用 Iwara 作者资料，则请求 Oreno3D 作者页，检查最多 8 条作品，用其中可访问视频的 `user` 建立账号映射；同批同作者复用一次回退查询，使用原有并发上限。手动作者操作也可走该回退，并优先尝试已知的原视频 ID。
- 新旧列表第 1、1000、9500 页抽查的 14 条详情均能得到视频与作者链接。第 9600 页发现空标题记录；其中 [117917](https://oreno3d.com/movies/117917)、[120989](https://oreno3d.com/movies/120989)、[8350](https://oreno3d.com/movies/8350) 修复后均成功取得来源链接，并通过其他作品取得真实 Iwara 作者 ID。117917 的 Iwara 原视频实测返回 JSON `403 / errors.privateVideo`，不能把它断言为已删除；删除后的 `404` 分支以回归测试验证。
- 若所有候选作品均不可用，保留 Oreno3D 作者页及错误原因；不以显示名称猜测账号。视频来源链接存在也不代表原视频仍可访问或下载。

### 多语言标签候选

- 标签搜索输入框支持英文、简体中文和日文候选提示；输入逗号分隔的下一个词时会继续匹配候选，查询结果仍由当前数据源决定，Oreno3D 模式直接走上述原生 `keyword` 搜索。
- 点击候选后会保留输入焦点和分隔符，可继续编辑多个 Oreno3D 搜索关键词。
- 标签词典默认使用项目已有的 `data/iwara_tags.json` 离线回退；点击“更新标签”后，会将 LoveIwara 的 MIT 授权词典缓存到 `data/tag_translations/`，启动时不会强制联网。
- 完整缓存文件为 `data/tag_translations/loveiwara_iwara_tags_localized.json`；项目生成的备用索引为 `data/iwara_tags.json`。
- 随包源文件为 `app/data/tag_translations/loveiwara_iwara_tags_localized.json`，Nuitka 与 GitHub Actions 会显式将其加入编译产物。
- 随包的 Oreno3D/Iwara 标签映射为 `app/data/oreno3d_iwara_map.json`，Nuitka 与 GitHub Actions 会显式将其加入编译产物。
- 首次运行且可执行文件旁的 `data/tag_translations/` 缺少缓存时，程序会自动展开内置词典；已有用户缓存不会被覆盖。开发机 `data/` 中的其他运行时文件仍需自行保留。
- 第三方来源及许可证见 [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)。

## 7. 筛选

全局筛选开关在解析阶段生效。

### 支持筛选项
- 最小点赞
- 最小播放
- 发布日期区间
- 包含标签（正筛）
- 排除标签（反筛）

### 标签匹配规则
- 大小写不敏感
- 支持分隔符：
  - 逗号、中文逗号、空格、`;`、`|`
- 归一化对比字段：
  - `id`、`type`、`slug`、`name`、`title`

## 8. UI 支持的输入 URL 类型

- 单视频：
  - `https://www.iwara.tv/video/{id}`
- 用户主页：
  - `https://www.iwara.tv/profile/{name}` 或 `/user/{name}`
- 播放列表：
  - `https://www.iwara.tv/playlist/{id}`
- 搜索 URL：
  - `https://api.iwara.tv/videos?...`
  - `https://www.iwara.tv/videos?...`

## 9. 标签抓取脚本（为后续一键标签筛选做准备）

- 脚本路径：
  - `app/core/crawl_iwara_tags.py`
- 作用：
  - 直接调用 `/tags` 接口，按 `A-Z` 和 `0-9` 抓取标签
  - 导出三语可扩展的数据到 JSON + Markdown
- 接口模式：
  - `https://apiq.iwara.tv/tags?filter={A-Z0-9}&page={n}`
- 默认输出：
  - `data/iwara_tags.json`
  - `docs/iwara_tags.md`
- 翻译字段：
  - 每个 tag 都生成 `name_en`、`name_zh`、`name_ja`
  - 默认回退为原始 tag 文本
  - 可通过 `--translation-map path/to/map.json` 覆盖
- 示例：
  - `pixi run python app/core/crawl_iwara_tags.py`

## 10. NFO 生成与 Emby 兼容

下载规则启用 `NFO` 行为后，程序会在视频文件旁生成同名 `.nfo` 文件。文件是 UTF-8 编码的 `<movie>` XML，可被 Emby、Jellyfin 和 Kodi 作为本地元数据读取。

### 输出字段

- 标题：`title`、`originaltitle`、`sorttitle`
- Iwara 标识：`video_id`、`id`、`uniqueid type="iwara"`、`iwaraid`
- 来源：`source`、`source_url`、`slug`、`quality`
- 作者：`author`，同时写入 `director` 和 `studio`
- 日期：`premiered`、`releasedate`、`year`、`published_at`
- 时长：分钟级 `runtime`、秒级 `duration`，以及 `fileinfo/streamdetails/video/durationinseconds`
- 评分与统计：`rating`、`mpaa`、`likes`、`views`、`comments`，并保留 `iwara_likes`、`iwara_views`、`iwara_comments` 别名
- 描述和标签：`plot`、`outline`、重复写入的 `genre`/`tag`，以及可选的 `tags_json`
- 封面：仅当本地封面已经存在时写入 `thumb`，内容为与视频同目录的文件名

标准字段与 Iwara 专用字段会同时写入，便于媒体中心识别，也不会丢失 Iwara 原始信息。已有 NFO 不会在启动时自动改写；升级后需要重新下载、重新生成元数据，或使用外部工具重新生成，才会得到新增的标准字段。
