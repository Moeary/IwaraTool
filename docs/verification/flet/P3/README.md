# P3 搜索与下载：尺寸验收截图

全部用离线虚构数据（`scripts/tools/demo_fixture.py`，不联网）生成：

```
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P3 --route /search --demo --wait 4 --prefix search-
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P3 --route /downloads --demo --wait 4 --prefix downloads-
```

`--demo` 时搜索页自动执行一次空关键词搜索（Iwara 最新视频），下载页加入 8 个任务并取消其中 1 个（`scripts/tools/flet_demo.py`）。

| 文件 | 内容 |
| --- | --- |
| `search-<宽>x<高>.png` | 9.2 清单 11 种尺寸的搜索结果网格（列数按 6.6） |
| `downloads-<宽>x<高>.png` | 同尺寸的下载页：链接栏、状态标签与计数、下载中的进度与速度、排队与已取消 |
| `*-dark-*.png` | 深色模式 1280×800 与 360×800 |

检查结果：各尺寸无横向溢出；手机为两列网格、宽屏 3～8 列；横屏手机（960×411）标签与结果摘要合为一行，页头收紧，网格保留一整行卡片；320 宽时任务行封面缩小为 56；提示条在底栏模式下浮在底栏之上。

另以脚本走通（虚构数据）：搜索 → 翻到第 2 页（手机滚到底自动加载、宽屏页码按钮）→ 多选 3 个 → 下载面板选规则 → 进入待确认（下载页徽标出现圆点、待确认卡片）→ 确认后下载、显示进度 → 取消 / 重试 / 恢复；筛选面板（点赞 ≥ 6000，按点赞排序）后结果摘要显示“筛选后 12 个”。Oreno3D 来源虚构数据未覆盖，需联网核对。
