# IwaraTool

![](./docs/iwaratool_logo.png)

[English](./readme.md) | [日本語](./readme.ja.md)

告别繁琐的命令行！拥有现代化 Win11 风格界面的 Iwara 批量下载器，小白也能一键下载作者全视频



## 核心功能
- 支持 `X-Version` 签名计算。
- 画质自动回退：`Source -> 540 -> 360`。
- 状态机下载调度，减少 URL 过期问题。
- 本地去重 + SQLite 历史记录。
- 支持作者页、播放列表、搜索链接批量入队。
- 筛选：点赞、播放、日期区间、标签正筛/反筛。
- 搜索下载上限可单独配置。
- 登录 token 缓存在 `data/config.ini`，提升启动速度。
- 中/英/日实时切换，不需要重启。
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

## 文档
- Wiki：<https://github.com/Moeary/IwaraTool/wiki>
- API 文档（EN）：[docs/API.md](./docs/API.md)
- API 文档（ZH）：[docs/API.zh.md](./docs/API.zh.md)
- API 文档（JA）：[docs/API.ja.md](./docs/API.ja.md)
- 标签索引：[docs/iwara_tags.md](./docs/iwara_tags.md)

## 标签抓取
```bash
pixi run python app/core/crawl_iwara_tags.py
```

## 运行 / 构建
```bash
pixi run start
pixi run crawl
pixi run build
```

## 许可证
MIT License。
