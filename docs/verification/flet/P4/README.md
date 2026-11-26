# P4 首页、详情与播放：尺寸验收截图

全部用离线虚构数据（`scripts/tools/demo_fixture.py`，不联网）生成：

```
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P4 --route /home --demo --wait 4 --prefix home-
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P4 --route /detail/video/demo004 --demo --wait 4 --prefix detail-
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P4 --route /detail/image/img003 --demo --wait 4 --only 1280x800,360x800 --prefix image-
IWARATOOL_DEMO_PLAY=<本地 mp4> pixi run -e flet start-flet --demo --route /detail/video/demo004 --shot <绝对路径>.png
IWARATOOL_DEMO_MENU=1 pixi run -e flet start-flet --demo --viewport 390x844 --route /search --shot <绝对路径>.png
```

| 文件 | 内容 |
| --- | --- |
| `home-<宽>x<高>.png` | 9.2 清单 11 种尺寸的首页：手机为横向货架（露出下一张卡片），宽屏为每栏 2 行网格；未登录时“我的订阅”显示登录提示 |
| `detail-<宽>x<高>.png` | 视频详情：封面（点按变为播放器）、标题与统计、操作行、作者卡、标签、简介、评论；内容宽 ≥ 1180 时相关作品在右侧栏（紧凑条目），否则在评论下方为网格 |
| `image-*.png` | 图片作品：整组图片竖排，按原图宽高比，竖图不超过窗口高度 |
| `player-1280x800.png` | 内嵌播放器（flet-video）播放本地副本 |
| `actions-sheet-dark-390x844.png` | 手机长按卡片的统一动作面板（`VideoActions`） |
| `*-dark-*.png` | 深色模式 |

检查结果：各尺寸无横向溢出；视频舞台高度不超过窗口高度的 70%，横屏手机与 2560 宽时标题和操作行在首屏可见；错误信息最多 4 行。

另以脚本核对：

- 播放：本地副本（`playable_video_target`）直接播放；远程视频经 `core/playback/stream_proxy.py` 回环代理播放。用本机模拟源站验证了“首个地址 403 → `refresh` 换新地址 → 继续播放”（flet-video 在 3 秒时位置 3.2 秒）。真实 Iwara 文件需联网核对。
- 右键菜单：`ContextMenu` 不在每张卡片上预建菜单，右键时在线程中查下载记录算出动作，重绘后用 `open(global_position=...)` 弹出（声明式控件是冻结的，不能直接改 `items`）。
- 全屏使用 flet-video 自带的自适应控制条（含全屏按钮）。
