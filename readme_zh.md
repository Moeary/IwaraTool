# IwaraTool

![logo](./docs/iwaratool_logo.png)

[English](./readme.md) | [日本語](./readme_ja.md)


告别繁琐的命令行！拥有现代化 Fluent 风格界面的 Iwara 批量下载器，小白也能一键下载作者全视频

![demo](./docs/iwaratool_demo.gif)


## 核心功能
- 支持 `X-Version` 签名计算。
- 画质自动回退：`Source -> 540 -> 360`。
- 状态机下载调度，减少 URL 过期问题。
- 本地去重 + SQLite 历史记录。
- 支持作者页、播放列表、搜索链接批量入队。
- 筛选：点赞、播放、日期区间、标签正筛/反筛。
- 搜索下载上限可单独配置。
- 登录 token 缓存在 `data/config.ini`，提升启动速度。
- 中/英/日多语言支持。
- 下载命名模板支持多占位符与目录层级控制。
- 可选 aria2 RPC、封面下载、`.nfo` 生成。

## 快速开始
1. 从 [Releases](https://github.com/Moeary/IwaraTool/releases) 下载最新版本。
2. 打开程序后先登录。
3. 在 `新建下载` 粘贴链接并开始排队。

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

## 文档
- Wiki：<https://github.com/Moeary/IwaraTool/wiki>
- API 文档（ZH）：[docs/API_zh.md](./docs/API_zh.md)
- 标签索引：[docs/iwara_tags.md](./docs/iwara_tags.md)

## 运行与构建

项目依赖使用 [pixi](https://pixi.prefix.dev/latest/) 管理。

如果你是开发者想直接运行源代码/或者想构建应用：

```shell
pixi run start //运行程序
pixi run build //构建应用
pixi run crawl //抓取标签数据（更新 docs/iwara_tags.md）
```

## 参与贡献

非常欢迎大家提 PR！
目前程序的 i18n（多语言）暂时还不够完善，深色/浅色模式切换也还没做。这些虽然都不影响核心的下载功能，但如果你有时间并且愿意帮忙完善它的话，直接提 PR 就行(提PR请规范提交更新merge到dev分支,并且尝试提交前通过GitHub Action的CI/CD)，感谢！

## 许可证

MIT License。

**下载本程序即视为同意遵守 MIT 许可证。详情请参阅 LICENSE 文件。**

1. 禁止将本项目用于任何违法、违规或违背公共秩序与善良风俗的用途；如因违规使用导致损失，责任由用户自行承担。
2. 项目提供的打包版本及脚本仅供个人学习与研究使用，未经许可不得用于商业再发行或转售。
3. 项目维护者保留依据法律法规或社区反馈随时更新、暂停或终止服务与支持的权利。

## 特别感谢

感谢[hare1039](https://github.com/hare1039)的[iwara-dl](https://github.com/hare1039/iwara-dl/tree/master)项目提供的宝贵参考和启发，尤其是在 X-Version 签名计算和下载链接解析方面的实现细节，对本项目的开发起到了重要的推动作用。



