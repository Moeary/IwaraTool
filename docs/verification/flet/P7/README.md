# P7 修复中心与工具：尺寸验收截图

修复中心用离线虚构数据（`--demo`，临时数据目录里放 11 个命名混乱的文件，`IWARATOOL_DEMO_STEP` 推进到对应步骤）；工具页用一段本机生成的测试片段（复制进临时数据目录，截图里不出现本机路径）：

```
ffmpeg -f lavfi -i testsrc2=size=640x360:rate=30:duration=12 -f lavfi -i sine=frequency=440:duration=12 \
       -c:v libvpx-vp9 -b:v 600k -c:a libopus tmp/flet_p7/media/sample.webm

V="pixi run -e flet python scripts/tools/flet_viewports.py --demo --out docs/verification/flet/P7"
$V --route /repair --wait 5 --only 1280x800 --prefix repair_folder_
IWARATOOL_DEMO_STEP=options $V --route /repair --wait 5 --only 360x800,1280x800 --prefix repair_options_
IWARATOOL_DEMO_STEP=preview $V --route /repair --wait 7 --only 360x800,960x411,1280x800,1920x1080 --prefix repair_preview_
IWARATOOL_DEMO_STEP=done $V --route /repair --wait 8 --theme dark --lang en --only 1440x900 --prefix repair_done_dark_en_
$V --route /tools --wait 4 --only 360x800 --prefix tools_list_
IWARATOOL_DEMO_VIDEO=tmp/flet_p7/media/sample.webm $V --route /tools/compress --wait 7 --only 360x800 --prefix tools_compress_
IWARATOOL_DEMO_VIDEO=tmp/flet_p7/media/sample.webm IWARATOOL_DEMO_STEP=run $V --route /tools --wait 30 --only 1440x900 --prefix tools_compress_done_
IWARATOOL_DEMO_VIDEO=tmp/flet_p7/media/sample.webm IWARATOOL_DEMO_TOOL=covers $V --route /tools --wait 9 --theme dark --lang ja --only 1280x800,1920x1080 --prefix tools_covers_dark_ja_
IWARATOOL_DEMO_VIDEO=tmp/flet_p7/media/sample.webm IWARATOOL_DEMO_TOOL=covers $V --route /tools/covers --wait 9 --only 320x640 --prefix tools_covers_
```

| 文件 | 内容 |
| --- | --- |
| `repair_folder_1280x800.png` | ① 视频文件夹：路径 + 浏览、原地重命名 / 整理到输出目录（分段按钮）、模式说明；整理模式才显示输出目录 |
| `repair_options_*.png` | ② 规则与选项：规则下拉、预设菜单、命名模板与示例文件名（无效时标红）、五个选项 |
| `repair_preview_*.png` | ③ 扫描与预览：计数标签、每行状态徽章 + ID · 标题 · 日期、原路径、→ 目标路径、说明（冲突 / 未找到标色）、✎ 按 Iwara ID 或链接重新查询；可折叠日志；底部“调整选项 / 扫描 / 开始修复”。宽屏横向步骤条，窄屏只标当前步骤，手机徽章与 ✎ 在首行 |
| `repair_done_dark_en_1440x900.png` | ⑤ 结果（深色、英文）：重命名 / NFO / 封面 / 跳过 / 失败数字卡片 + 最终列表，“重新开始” |
| `tools_list_360x800.png` | 工具列表（窄屏），点开 `/tools/<工具>` |
| `tools_compress_*.png` | 上传压缩：源视频与媒体信息、判断卡片（需要处理的原因、输出规格与预计大小、备注）、输出 + 开始 + 进度与剩余时间 + 完成后“打开所在文件夹 / 计算成品的封面”、压缩设置（标签与控件同行，宽 ≤720） |
| `tools_covers_*.png` | 封面预览：16 张候选（4 × 4，太窄时 2 列）、序号与时间码、3:2 裁切开关、选中项大图 + 保存此帧 / 复制时间码；方向键移动选中项 |

检查结果：9.2 全部尺寸下两页均无横向溢出、无异常（`tmp/flet_p7/sweep` 的全尺寸扫描）；修复流程从扫描到修复结束、压缩从探测到成品（H.264 + AAC、1280×720）都实际跑通。

与旧界面结果一致：两边的扫描 / 查询 / 预览 / 修复都走 `core/repair/runs.py`，选项都由 `presentation/repair/page.RepairForm.options()` 生成；压缩都用 `presentation/tools/compress.CompressForm.options()` 和 `core/tools/compress.Compressor`，同一样本得到同一计划与输出。

单元测试：`scripts/tests/core/test_repair_runs.py`（批量回调、停止与取消令牌、表单选项与保存、行状态与汇总）、`test_tools_presentation.py`（压缩选项保存、编码器随 ffmpeg、判断卡片、输出路径、进度文字、裁切、网格列数与方向键）、`scripts/tests/flet_ui/test_flet_repair.py`、`test_flet_tools.py`（假 ffmpeg 驱动探测、压缩、封面截图与选择）。真实视频库上的修复、硬件编码器需在真实环境核对。
