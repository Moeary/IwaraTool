# 本地数据与缓存

[English](./data_en.md) | [日本語](./data_ja.md) | [返回 README](../../readme_zh.md)

应用运行数据默认位于 `data/`，不同用途分开保存，搜索和订阅封面不会与下载规则生成的本地封面混用。

| 路径 | 用途 |
| --- | --- |
| `data/config.ini` | 登录 token、界面、并发和下载行为配置 |
| `data/tasks.json` | 可恢复的排队、运行中、失败和已中断任务状态 |
| `data/history.db` | 下载历史、订阅源、订阅视频和刷新状态 |
| `data/rules.json` | 命名、筛选、封面和 NFO 等下载规则 |
| `data/logs/` | 滚动日志（`iwaratool.log`，最多 4 个 1 MB 文件） |
| `data/updates/` | 一键更新下载的新版本（校验通过后才会安装） |
| `data/x_version_salts.json` | 可选：覆盖 X-Version 盐值的 JSON 列表 |
| `data/backup_before_restore/` | 恢复备份时保留的旧文件 |
| `data/iwara_tags.json` | 项目生成的离线标签索引与三语字段 |
| `app/data/tag_translations/loveiwara_iwara_tags_localized.json` | 随程序打包的 LoveIwara MIT 标签翻译源文件 |
| `data/tag_translations/loveiwara_iwara_tags_localized.json` | 运行时标签翻译缓存，由“更新标签”刷新 |
| `data/img/search/` | 搜索页的 Oreno3D/Iwara 图片缓存 |
| `data/img/sub/` | 订阅页视频封面缓存，可复用历史中的 Iwara 封面 |
| `data/img/avatar/` | 订阅作者头像缓存；旧版 `avatar_*` 文件会在启动时迁移式复用 |

编译版会把 `app/data/` 下的 LoveIwara 词典带入程序，并在程序所在目录的 `data/tag_translations/` 缺少运行时缓存时首次自动展开；已有运行时缓存会保留。开发机 `data/` 中的其他本地状态不会自动编译进程序，更新程序时仍应保留旧 `data/` 目录。

当下载规则启用 NFO 生成时，NFO 会写在视频旁边，并使用相同的主文件名。新文件包含用于媒体中心导入的标准电影元数据字段，并保留 Iwara 专用的别名字段；已有的 NFO 文件不会被自动重写。

搜索页的 Oreno3D 结果保持在线获取，只缓存当前结果的图片。
