# P6 资料库、规则、设置：尺寸验收截图

全部用离线虚构数据（`--demo`，不联网，临时数据目录）生成：

```
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P6 --route /library --demo --wait 6 --only 360x800,1280x800,1920x1080 --prefix library_
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P6 --route /rules --demo --wait 5 --only 360x800,768x1024,1280x800,1920x1080 --prefix rules_
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P6 --route /rules/__builtin_default__ --demo --wait 5 --theme dark --only 390x844 --prefix rule_dark_
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P6 --route /settings --demo --wait 5 --only 360x800,768x1024,1280x800,1920x1080 --prefix settings_
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P6 --route /settings/downloads --demo --wait 5 --theme dark --lang en --only 1440x900,390x844 --prefix settings_downloads_dark_en_
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P6 --route /settings/shortcuts --demo --wait 5 --only 1440x900 --prefix settings_shortcuts_
pixi run -e flet python scripts/tools/flet_viewports.py --out docs/verification/flet/P6 --route /settings/home/rows --demo --wait 5 --only 390x844,1280x800 --prefix home_rows_
```

| 文件 | 内容 |
| --- | --- |
| `library_*.png` | 资料库：手机列表行、宽屏表格（按宽度隐藏列，标题列不少于 240）、≥1180 时右侧记录面板；范围标签、作者筛选、搜索字段与状态筛选 |
| `rules_*.png` | 下载规则：宽屏列表-详情（● 默认、类型 · 摘要、“用于：”页面）、可折叠“各页面使用的规则”；手机为列表，点开 `/rules/<id>` |
| `rule_dark_390x844.png` | 规则编辑器（深色）：醒目的示例文件名、完整模板、预设 / 插入字段 / 检验、筛选、标题筛选、下载行为、底部操作条 |
| `settings_*.png` | 设置：宽屏分类列表 + 卡片（内容宽 ≤720）；未达双栏宽度时为分类列表，点开 `/settings/<分类>` |
| `settings_downloads_dark_en_*.png` | 下载分类（深色、英文）：目录（浏览 / 打开 / 清理临时文件）、画质、行为、并发与自动重试、限速与分时、aria2（含远端与连接测试） |
| `settings_shortcuts_1440x900.png` | 键盘快捷键：按区域分组，点击后按新组合键（Esc 取消、Backspace 清除），冲突时提示 |
| `home_rows_*.png` | 自定义首页 `/settings/home/rows`：勾选显示、上移 / 下移、编辑 / 删除、添加栏目（同页编辑表单）、恢复默认，保存后首页重载一次 |

检查结果：各尺寸无横向溢出；所有修改即时保存（文本与数字在失焦或回车时保存，并显示夹取后的实际值），没有“保存全部设置”。

单元测试：`scripts/tests/core/test_settings_presentation.py`（数值解析与夹取、搜索记录裁剪、并发与模式、清理 / aria2 / 标签词典报告、分类与别名、首页栏目编辑规则、内置栏目改动换 id、快捷键原生显示）、`scripts/tests/flet_ui/test_flet_rules.py`、`test_flet_library.py`。登录、代理生效、aria2 连接、备份恢复、ffmpeg 检测需在真实环境核对。
