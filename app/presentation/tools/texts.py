"""Tools page labels shared by both interfaces: ``tool_text(key)``.

Moved out of ``app/ui/tools`` verbatim; keys are slugs of the English text.
"""
from __future__ import annotations

from ...i18n import tr

TEXTS: dict[str, tuple[str, str, str]] = {
    'source_video': ('Source Video', '源视频', '元の動画'),
    'iwara_accepts_h_264_aac': ('Iwara accepts H.264 + AAC in MP4, at least 720p, up to 300 MB (600 MB for premium). Drop a video anywhere on this tool or browse for it.', 'Iwara 上传要求：H.264 + AAC 编码、MP4 封装，分辨率不低于 720p，大小不超过 300MB（会员 600MB）。可把视频拖到本工具任意位置，或点击浏览。', 'Iwara の条件：H.264 + AAC の MP4、720p 以上、300 MB まで（プレミアムは 600 MB）。動画をこのツールにドロップするか参照してください。'),
    'video_file': ('Video file', '视频文件', '動画ファイル'),
    'browse': ('Browse…', '浏览…', '参照…'),
    'compression': ('Compression', '压缩设置', '圧縮設定'),
    'size_limit': ('Size limit', '大小上限', 'サイズ上限'),
    'custom_limit': ('Custom limit', '自定义上限', 'カスタム上限'),
    'safety_margin_below_the_limit': ('Safety margin below the limit', '预留余量', '上限からの余裕'),
    'encoder': ('Encoder', '编码器', 'エンコーダー'),
    'x264_speed_slower_better_quality': ('x264 speed (slower = better quality)', 'x264 速度（越慢画质越好）', 'x264 速度（遅いほど高画質）'),
    'aac_audio_bitrate': ('AAC audio bitrate', 'AAC 音频码率', 'AAC 音声ビットレート'),
    'frame_rate': ('Frame rate', '帧率', 'フレームレート'),
    'upscaling_to_720p': ('Upscaling to 720p', '放大到 720p 的算法', '720p への拡大方式'),
    'lower_the_resolution_when_the': ('Lower the resolution when the size budget is too small (never below 720p)', '体积预算不够时自动降低分辨率（不低于 720p）', '容量が足りないときは解像度を下げる（720p 未満にはしない）'),
    're_encode_even_if_the': ('Re-encode even if the file already qualifies', '即使已符合要求也重新编码', '条件を満たしていても再エンコードする'),
    'output': ('Output', '输出', '出力'),
    'output_mp4': ('Output MP4', '输出 MP4 文件', '出力 MP4'),
    'save_as': ('Save As…', '另存为…', '名前を付けて保存…'),
    'start': ('Start', '开始压缩', '開始'),
    'cancel': ('Cancel', '取消', 'キャンセル'),
    'show_in_folder': ('Show in Folder', '打开所在文件夹', 'フォルダーを開く'),
    'preview_covers_of_the_result': ('Preview Covers of the Result', '计算成品的封面', '完成ファイルのカバーを確認'),
    'save_as_2': ('Save as', '另存为', '名前を付けて保存'),
    'cover_preview': ('Cover Preview', '封面预览', 'カバーのプレビュー'),
    'after_an_upload_iwara_offers': ("After an upload Iwara offers 16 screenshots as the cover, apparently taken at the middle of 16 equal slices (duration × (2i+1)/32), numbered here in the order of Iwara's picker (left to right, top to bottom). Check the final file before uploading so a good frame is on offer. This is a community finding, not confirmed by Iwara.", '上传后 Iwara 会给出 16 张截图供选择封面，推测取自把视频等分成 16 段后每段的正中间（时长 × (2i+1)/32）。编号与 Iwara 封面选择里的顺序一致（从左到右、从上到下）。上传前用最终成品算一遍，就能知道有没有合适的封面可选。该算法来自社区推测，未经 Iwara 官方确认。', 'アップロード後、Iwara はカバー候補として 16 枚のスクリーンショットを提示します。動画を 16 等分した各区間の中央（長さ × (2i+1)/32）と推定されています。番号は Iwara の選択画面の順（左上から）です。アップロード前に最終ファイルで確認できます（非公式の推定です）。'),
    'save_frame': ('Save Frame…', '保存此帧…', 'フレームを保存…'),
    'copy_time': ('Copy Time', '复制时间码', '時間をコピー'),
    'save_frame_2': ('Save frame', '保存此帧', 'フレームを保存'),
    'crop_like_a_card_3': ('Crop like a card (3:2)', '按卡片比例裁切（3:2）', 'カード比率で切り抜き（3:2）'),
    'tools': ('Tools', '工具', 'ツール'),
}


def tool_text(key: str) -> str:
    en, zh, ja = TEXTS[key]
    return tr(en, zh, ja)
