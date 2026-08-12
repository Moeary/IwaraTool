# IwaraTool API 说明（简体中文）

[English](./API.md) | [日本語](./API_ja.md)

本文档说明当前项目中已实现的 API 能力。

## 1. 认证

### 登录
- 接口：`POST https://api.iwara.tv/user/login`
- 请求体：
  - `email`：用户名或邮箱
  - `password`：账号密码
- 成功后：
  - 返回 `token`（Bearer Token）

### Token 用法
- 鉴权请求头：
  - `Authorization: Bearer <token>`
- Token 本地缓存到 `data/config.ini`：
  - `auth_token`
  - `auth_token_saved_at`
- 启动策略：
  - 优先使用本地 token（快速路径）
  - 无 token 时再回退账号密码登录

## 2. 视频信息与下载直链解析

### 获取视频元信息
- 接口：`GET https://api.iwara.tv/video/{video_id}`

### 解析可下载源
- 输入：视频元信息中的 `fileUrl`
- 追加 `X-Version` 请求头，计算基于：
  - `{filename}_{expires}_{salt}`
  - SHA1
- 内置画质回退：
  - `Source -> 540 -> 360`

## 3. 批量来源

### 按用户
- 先解析用户 id：
  - `GET https://api.iwara.tv/profile/{username}`
- 再拉取视频列表：
  - `GET https://api.iwara.tv/videos?user={user_id}&sort=date&page={n}`

### 按播放列表
- `GET https://api.iwara.tv/playlist/{playlist_id}?page={n}`

## 4. 搜索下载（新增）

程序已支持在下载输入框直接粘贴搜索 URL。

### 支持格式
- `https://api.iwara.tv/videos?...`
- `https://www.iwara.tv/videos?...`
- 示例：
  - `https://api.iwara.tv/videos?tags=2d&sort=date`
  - `https://www.iwara.tv/videos?tags=2d&sort=date`

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

## 5. Fluent 搜索页

主窗口侧栏中的“搜索”页是面向浏览和批量入队的 Fluent 搜索界面，和下载工作台里的“搜索 URL 入队”互不替代。数据源默认是 Oreno3D 在线搜索，也可以切换为 Iwara 实时 API。

### 搜索类型

- 视频：Oreno3D 模式直接请求 `GET https://oreno3d.com/search?keyword=...&sort=latest&page=1`；Iwara 模式调用 `GET https://api.iwara.tv/videos`。Oreno3D 只负责提供在线搜索候选，打开结果时最终进入 Iwara 页面。
- 作者：切换到 Iwara 实时 API 后按用户名请求作者主页，展示作者简介和视频数量。
- 标签：Oreno3D 模式把输入作为原站 `keyword` 直接提交，不转换成 Iwara 的 `tags=` 参数；候选词典只负责辅助输入。候选选中后会保留分隔符，可继续输入多个标签。
- 播放列表：输入播放列表 ID 或 `/playlist/{id}` 链接，展示其中的视频。

### 排序与下载规则

- 排序：最新、趋势、热度、喜欢数；这些选项对应 Oreno3D 原生搜索页的排序参数。
- 搜索页不再复制下载工作台的高级筛选字段；页面内的“下载规则”选择器直接复用现有规则。
- 选中规则后会立即应用项目已有的元数据筛选、命名模板、封面/NFO 和下载行为，并同步为默认规则。

### 图片缓存

- 视频封面与作者头像按需下载。
- 搜索页图片缓存目录：`data/img/search/`。
- 订阅页视频封面缓存目录：`data/img/sub/`；会优先复用历史记录中已有的 Iwara 封面并复制到此目录，不覆盖下载规则生成的封面。旧版本的 `data/img/sub_video/` 会在读取对应条目时迁移式复用。
- 作者、订阅流等列表接口若只返回 Iwara ID，刷新封面时会调用 `GET /video/{id}` 补齐 `file.id`、`fileUrl` 和 `thumbnail`，再按 `https://{file-host}/image/original/{file-id}/thumbnail-{index:02d}.jpg` 下载到上述目录；成功解析的地址会回写订阅数据库。
- 订阅作者头像缓存目录：`data/img/avatar/`；新文件以 `username` 开头。启动时会把旧版 `data/img/avatar_*` 文件复制迁移到新目录，并更新订阅源记录，旧文件不会被强制删除。
- 文件使用 URL 指纹命名，并通过临时文件原子替换，网络失败只保留占位图，不影响搜索结果。

### 刷新与缓存并发设置

- 设置页的“刷新与封面性能”卡片控制以下 UI 配置键：
  - `cover_download_workers_v1`：搜索页和订阅页封面获取并发数，范围为 `1–16`，默认 `6`；Oreno3D 缩略图与 Iwara API 补齐的封面都使用该并发池。
  - `subscription_refresh_workers_v1`：启用订阅源刷新并发数，范围为 `1–8`，默认 `3`；每个刷新任务使用独立的 API 会话，并复用当前 token 与代理。
  - `subscription_incremental_refresh_v1`：账户订阅增量刷新开关，默认开启。
- 增量刷新只对账户订阅流和从账户导入的作者生效：接口按最新排序分页，遇到本地已知的 Iwara 视频 ID 即停止；首次没有已知 ID 时仍会建立完整本地索引。播放列表和本地作者源保持原有完整拉取语义。
- 并发仅作用于网络请求；SQLite 写入仍由订阅存储层串行保护，避免多个刷新任务破坏本地数据。

### 结果视图

- 网格模式可手动指定每行列数，程序根据可用宽度自动计算卡片和缩略图尺寸。
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

### 多语言标签候选

- 标签搜索输入框支持英文、简体中文和日文候选提示；输入逗号分隔的下一个词时会继续匹配候选，查询结果仍由当前数据源决定，Oreno3D 模式直接走上述原生 `keyword` 搜索。
- 点击候选后会保留输入焦点和分隔符，可继续编辑多个 Oreno3D 搜索关键词。
- 标签词典默认使用项目已有的 `data/iwara_tags.json` 离线回退；点击“更新标签”后，会将 LoveIwara 的 MIT 授权词典缓存到 `data/tag_translations/`，启动时不会强制联网。
- 完整缓存文件为 `data/tag_translations/loveiwara_iwara_tags_localized.json`；项目生成的备用索引为 `data/iwara_tags.json`。
- 随包源文件为 `app/data/tag_translations/loveiwara_iwara_tags_localized.json`，Nuitka 与 GitHub Actions 会显式将其加入编译产物。
- 首次运行且可执行文件旁的 `data/tag_translations/` 缺少缓存时，程序会自动展开内置词典；已有用户缓存不会被覆盖。开发机 `data/` 中的其他运行时文件仍需自行保留。
- 第三方来源及许可证见 [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)。

## 6. 筛选

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

## 7. UI 支持的输入 URL 类型

- 单视频：
  - `https://www.iwara.tv/video/{id}`
- 用户主页：
  - `https://www.iwara.tv/profile/{name}` 或 `/user/{name}`
- 播放列表：
  - `https://www.iwara.tv/playlist/{id}`
- 搜索 URL：
  - `https://api.iwara.tv/videos?...`
  - `https://www.iwara.tv/videos?...`

## 8. 标签抓取脚本（为后续一键标签筛选做准备）

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

## 9. NFO 生成与 Emby 兼容

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
