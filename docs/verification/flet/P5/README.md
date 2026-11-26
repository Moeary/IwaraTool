# P5 订阅与作者：尺寸验收截图

全部用离线虚构数据（`scripts/tools/demo_fixture.py`，不联网；`--demo` 下订阅、作者路由会先写入 6 个作者订阅并做一次虚构刷新）生成：

```
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P5 --route /subscriptions --demo --wait 6 --prefix subscriptions_
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P5 --route /subscriptions --demo --wait 6 --theme dark --only 1280x800,390x844 --prefix subscriptions_dark_
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P5 --route /subscriptions/1 --demo --wait 6 --only 360x800,768x1024 --prefix source_
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P5 --route /author/aoi_motion --demo --wait 7 --only 360x800,1280x800 --prefix author_
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P5 --route /author/aoi_motion --demo --wait 7 --theme dark --lang en --only 1440x900 --prefix author_dark_en_
```

| 文件 | 内容 |
| --- | --- |
| `subscriptions_<宽>x<高>.png` | 9.2 清单 11 种尺寸的订阅页：未达双栏宽度时为总览卡片（头像、名称、新增 / 停用标签、统计、刷新与更多菜单、最近作品货架）；`two_pane` 时左侧源列表（未下载数、新增角标）、右侧所选源的作品区 |
| `source_<宽>x<高>.png` | 单个订阅 `/subscriptions/<id>`：统计、作者状态（程序内订阅 / Iwara 关注）、标题搜索、状态筛选与排序、“下载当前”与“…”菜单、带状态徽章的作品网格 |
| `author_*.png` | 作者页 `/author/<用户名>`：资料卡与 `AuthorStatus`（已订阅时有“打开订阅”）、视频 / 图片标签、分页网格与“下载当前展示” |
| `*dark*.png` | 深色模式 |

检查结果：各尺寸无横向溢出；双栏时右侧网格按栏宽计算列数（`VideoGrid(width=...)`）；没有播放 / 点赞数的订阅作品只显示日期。

另以单元测试核对（`scripts/tests/flet_ui/test_flet_subscriptions.py`、`scripts/tests/core/test_subscription_presentation.py`）：源的搜索与排序、作品筛选、已下载不重复入队、只下载新增、选中后经 `stage_job` 进入待确认、删除与停用、添加链接的作者 / 播放列表识别、刷新进度与结果文字、删除确认摘要、作者资料解析。真实账号下的关注 / 取消关注需联网核对。
