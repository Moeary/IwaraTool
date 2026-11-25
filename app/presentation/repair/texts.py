"""Repair Center labels shared by both interfaces: ``repair_text(key)``.

Moved out of ``app/ui/repair`` verbatim; keys are slugs of the English text.
"""
from __future__ import annotations

from ...i18n import tr

TEXTS: dict[str, tuple[str, str, str]] = {
    'choose_a_folder_to_begin': ('Choose a folder to begin', '请选择一个文件夹开始', 'フォルダーを選択して開始'),
    'repair_selected_folder': ('Repair Selected Folder', '开始修复', '修復を開始'),
    'stop': ('Stop', '停止', '停止'),
    'drop_a_folder_here_or': ('Drop a folder here or browse it. Subfolders are scanned too.', '可将文件夹拖入此处或浏览选择；也会扫描子文件夹。', 'フォルダーをドロップまたは参照できます。サブフォルダーも検索します。'),
    'drop_a_local_folder_or': ('Drop a local folder or enter its path…', '拖入文件夹或输入路径…', 'ローカルフォルダーをドロップまたは入力…'),
    'browse': ('Browse', '浏览', '参照'),
    'leave_blank_to_organize_inside': ('Leave blank to organize inside the source folder…', '留空则在输入文件夹内整理…', '空欄なら入力フォルダー内に整理…'),
    'scan': ('Scan', '扫描', '検索'),
    'in_place_mode_uses_only': ('In-place mode uses only the filename. Organize mode also applies directory segments such as {author}/.', '原地模式只应用文件名段；整理模式还会应用 {author}/ 等目录段。', '現在の場所ではファイル名のみを適用します。整理モードでは {author}/ などのディレクトリ部分も適用します。'),
    'custom': ('Custom', '自定义', 'カスタム'),
    'rename_existing_videos': ('Rename existing videos', '重命名已有视频', '既存動画を名前変更'),
    'allow_video_download_when_needed': ('Allow video download when needed', '必要时允许下载视频', '必要時に動画ダウンロードを許可'),
    'existing_local_videos_are_always': ('Existing local videos are always kept; this repair page never replaces them.', '已有本地视频始终保留；修复页不会替换它们。', '既存のローカル動画は保持され、修復ページで置き換えることはありません。'),
    'download_cover_screenshot': ('Download cover screenshot', '下载封面截图', 'サムネイルを取得'),
    'write_nfo_metadata': ('Write NFO metadata', '写入 NFO 元数据', 'NFO メタデータを書き込む'),
    'add_to_download_history': ('Add to download history', '加入下载历史', 'ダウンロード履歴に追加'),
    'clear_log': ('Clear log', '清空日志', 'ログをクリア'),
    'repair_logs_will_appear_here': ('Repair logs will appear here…', '修复日志将显示在此…', '修復ログはここに表示されます…'),
    'no_folder_scanned': ('No folder scanned', '尚未扫描文件夹', '未検索'),
    'repair_center': ('Repair Center', '修复中心', '修復センター'),
    'source_folder': ('Source Folder', '视频文件夹', '動画フォルダー'),
    'mode': ('Mode', '模式', 'モード'),
    'rename_in_place': ('Rename in place', '原地重命名', '現在の場所で名前変更'),
    'organize_into_output_folder': ('Organize into output folder', '整理到输出目录', '出力フォルダーに整理'),
    'output': ('Output', '输出', '出力'),
    'repair_rule': ('Repair Rule', '修复规则', '修復ルール'),
    'rule': ('Rule', '规则', 'ルール'),
    'preset': ('Preset', '预设', 'プリセット'),
    'name': ('Name', '命名', '名前'),
    'repair_options': ('Repair Options', '修复选项', '修復オプション'),
    'repair_log': ('Repair Log', '修复日志', '修復ログ'),
    'repair_list': ('Repair List', '修复列表', '修復リスト'),
    'state': ('State', '状态', '状態'),
    'original_path': ('Original Path', '原路径', '元のパス'),
    'title': ('Title', '标题', 'タイトル'),
    'date': ('Date', '日期', '日付'),
    'target_path': ('Target Path', '目标路径', '変更先パス'),
    'detail': ('Detail', '详情', '詳細'),
    'choose_source_folder': ('Choose source folder', '选择视频文件夹', '動画フォルダーを選択'),
    'choose_output_folder': ('Choose output folder', '选择输出文件夹', '出力フォルダーを選択'),
}


def repair_text(key: str) -> str:
    en, zh, ja = TEXTS[key]
    return tr(en, zh, ja)
