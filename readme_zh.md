# IwaraTool

![logo](./docs/iwaratool_logo.png)

[English](./readme.md) | [日本語](./readme_ja.md)


告别繁琐的命令行！拥有现代化 Fluent 风格界面的 Iwara 批量下载器，小白也能一键下载作者全视频

![demo](./docs/iwaratool_demo_v0.6.gif)

## 核心功能
- 支持 `X-Version` 签名计算。
- 画质自动回退：`Source -> 540 -> 360`。
- 状态机下载调度，减少 URL 过期问题。
- 任务队列持久化；安全退出时保留任务与临时文件，下次启动自动恢复。
- 本地去重 + SQLite 历史记录中心。
- 支持作者页、播放列表、搜索链接批量入队。
- 支持更好的 Iwara 搜索：支持 Oreno3D 在线视频搜索、Iwara 实时视频/作者/播放列表搜索，以及中日英 tag 候选。
- 本地订阅管理，支持区分 Iwara 账户订阅、本地作者和订阅列表，导入关注作者时会覆盖同名本地作者来源；支持刷新追踪、新增统计和列表/封面视图。
- 命名规则统一管理点赞、播放、日期、标签、标题关键词、命名模板和下载行为。
- 搜索下载上限可单独配置。
- 登录 token 缓存在 `data/config.ini`，提升启动速度。
- 中/英/日多语言支持。
- 支持运行时深色/浅色主题切换。
- 下载命名模板支持多占位符与目录层级控制。
- 可选 aria2 RPC、封面下载、`.nfo` 生成。
- 历史记录支持搜索、筛选、排序、打开文件、重命名和清理已移走记录。
- 表格字段、列宽和顺序可配置并持久化，分栏布局可响应窗口尺寸。
- 下载重试前自动清理对应临时缓存，减少坏缓存导致的重复失败。

## 快速开始
1. 从 [Releases](https://github.com/Moeary/IwaraTool/releases) 下载最新版本。
   - Linux 预编译包基于 GitHub 的 `ubuntu-latest` 构建，不支持 glibc 较老的系统；老版本发行版建议下载源码后在本机编译。
2. 打开程序；下载私有视频或导入关注作者时需要登录。
3. 在 `下载工作台` 粘贴链接，按需选择下载规则后提交。
4. 在 `订阅页` 跟踪账号流、作者或播放列表，并批量下载新增内容。

## 支持的 URL 类型
```text
https://www.iwara.tv/profile/username
https://www.iwara.tv/profile/username/videos
https://www.iwara.tv/playlist/xxxxxxxx
https://www.iwara.tv/video/xxxxxxxx
https://www.iwara.tv/videos?sort=date
https://www.iwara.tv/videos?tags=2d&sort=likes
https://api.iwara.tv/videos?tags=2d&sort=date
```

`sort` 支持：`date`、`trending`、`popularity`、`views`、`likes`。
`tags` 支持详见 [标签索引](./docs/iwara_tags.md)。

Oreno3D 标签桥接支持单标签直达 `/tags/{id}`；也可输入 `tag:<id>`、`origin:<id>`、`character:<id>` 或对应的 Oreno3D 实体 URL。结果仍会解析为正式的 Iwara 视频后再打开或加入队列。

## 本地数据与缓存

应用运行数据默认位于 `data/`，不同用途分开保存，搜索和订阅封面不会与下载规则生成的本地封面混用。

| 路径 | 用途 |
| --- | --- |
| `data/config.ini` | 登录 token、界面、并发和下载行为配置 |
| `data/tasks.json` | 可恢复的排队、运行中、失败和已中断任务状态 |
| `data/history.db` | 下载历史与已下载状态 |
| `data/subscriptions.db` | 订阅源、订阅视频和刷新状态 |
| `data/rules.json` | 命名、筛选、封面和 NFO 等下载规则 |
| `data/iwara_tags.json` | 项目生成的离线标签索引与三语字段 |
| `app/data/tag_translations/loveiwara_iwara_tags_localized.json` | 随程序打包的 LoveIwara MIT 标签翻译源文件 |
| `data/tag_translations/loveiwara_iwara_tags_localized.json` | 运行时标签翻译缓存，由“更新标签”刷新 |
| `data/img/search/` | 搜索页的 Oreno3D/Iwara 图片缓存 |
| `data/img/sub/` | 订阅页视频封面缓存，可复用历史中的 Iwara 封面 |
| `data/img/avatar/` | 订阅作者头像缓存；旧版 `avatar_*` 文件会在启动时迁移式复用 |

编译版会把 `app/data/` 下的 LoveIwara 词典带入程序，并在程序所在目录的 `data/tag_translations/` 缺少运行时缓存时首次自动展开；已有运行时缓存会保留。开发机 `data/` 中的其他本地状态不会自动编译进程序，更新程序时仍应保留旧 `data/` 目录。

## 页面展示

1. 下载工作台
![下载工作台](./docs/panel_view/iwaratool_download_panel.jpg)
2. 搜索页
![搜索页](./docs/panel_view/iwaratool_search_panel.jpg)
3. 订阅页
![订阅页](./docs/panel_view/iwaratool_subscription_panel.jpg)
4. 历史记录页
![历史记录页](./docs/panel_view/iwaratool_history_panel.jpg)
5. 规则页
![规则页](./docs/panel_view/iwaratool_rule_panel.jpg)
6. 设置页
![设置页](./docs/panel_view/iwaratool_settings_panel.jpg)

## 文档
- Wiki：<https://github.com/Moeary/IwaraTool/wiki>
- API 文档（ZH）：[docs/API_zh.md](./docs/API_zh.md)
- 标签索引：[docs/iwara_tags.md](./docs/iwara_tags.md)

## 运行与构建

项目依赖使用 [pixi](https://pixi.prefix.dev/latest/) 管理。

如果你是开发者想直接运行源代码/或者想构建应用：

```shell
pixi install
pixi run start  # 运行程序
pixi run build  # 构建应用
pixi run crawl  # 抓取标签数据（更新 docs/iwara_tags.md）
pixi run python -m unittest discover -s tests -p "test_*.py" -v
```

## 参与贡献

非常欢迎大家提 PR！
请将改动提交到 `dev` 分支，保持英文、简体中文和日文界面文本同步，并在提交前运行完整测试。涉及界面变化时，建议附上截图或短视频。

## 许可证

MIT License。

**下载本程序即视为同意遵守 MIT 许可证。详情请参阅 LICENSE 文件。**

1. 禁止将本项目用于任何违法、违规或违背公共秩序与善良风俗的用途；如因违规使用导致损失，责任由用户自行承担。
2. 项目提供的打包版本及脚本仅供个人学习与研究使用，未经许可不得用于商业再发行或转售。
3. 项目维护者保留依据法律法规或社区反馈随时更新、暂停或终止服务与支持的权利。

## 特别感谢

感谢[hare1039](https://github.com/hare1039)的[iwara-dl](https://github.com/hare1039/iwara-dl/tree/master)项目提供的宝贵参考和启发，尤其是在 X-Version 签名计算和下载链接解析方面的实现细节，对本项目的开发起到了重要的推动作用。
