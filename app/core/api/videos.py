"""Video and image endpoints: info, download sources, related posts and comments."""

from __future__ import annotations

from typing import Optional

from ...i18n import tr
from .auth_tokens import x_version_salts
from .endpoints import ALT_BASE_API, BASE_API
from .errors import _extract_api_message, _friendly_request_error, _try_response_json


class VideoApiMixin:
    """Video and image endpoints: info, download sources, related posts and comments."""

    def get_video_info(self, video_id: str) -> tuple[Optional[dict], str]:
        """Fetch video metadata.

        Returns (data_dict, error_message).
        """
        last_error = ""
        for root in (BASE_API, ALT_BASE_API):
            try:
                data = self._get_json(f"{root}/video/{video_id}")
                # If server returned HTML (bot protection) the first char is '<'
                if isinstance(data, str) and data.startswith("<"):
                    return None, tr(
                        "Received HTML response. Please enable proxy or Cloudflare bypass failed.",
                        "收到 HTML 响应，请启用代理或 Cloudflare 绕过失败",
                        "HTML レスポンスを受信しました。プロキシを有効化するか、Cloudflare 回避に失敗しています。",
                    )
                if not isinstance(data, dict):
                    return None, tr(
                        f"Unexpected API response type: {type(data).__name__}",
                        f"API 返回类型异常: {type(data).__name__}",
                        f"API 応答タイプが不正です: {type(data).__name__}",
                    )
                return data, ""
            except Exception as exc:
                last_error = str(exc)
                if "404" in last_error or "not found" in last_error.lower():
                    return None, tr(
                        "Video not found or not visible to the current account. It may have been deleted, hidden, private, or require another account.",
                        "作品不存在或当前账号不可见，可能已删除、隐藏、私有，或需要换有权限的账号登录。",
                        "動画が存在しないか現在のアカウントでは表示できません。削除・非表示・非公開、または別アカウントが必要な可能性があります。",
                    )
                continue
        return None, _friendly_request_error(last_error)

    def get_download_info(
        self,
        video_info: dict,
        preferred_quality: str = "Source",
        log_cb=None,
    ) -> tuple[Optional[str], Optional[str], str]:
        """Resolve the best available download URL from video metadata.

        Args:
            video_info: parsed video metadata dict from the Iwara API.
            preferred_quality: one of "Source", "540", "360".  Falls back
                to lower resolutions automatically.
            log_cb: optional callable(str) for verbose logging.

        Returns (download_url, quality_name, error_message).
        """
        def _log(msg: str):
            if log_cb:
                log_cb(msg)

        file_url: Optional[str] = video_info.get("fileUrl")
        if not file_url:
            return None, None, self._missing_file_url_error(video_info)

        _log(f"  fileUrl: {file_url[:80]}…")
        _log(
            tr(
                f"  Login state: {'logged in (Bearer token attached)' if self.token else 'not logged in (no token)'}",
                f"  登录态: {'已登录（携带 Token）' if self.token else '未登录（无 Token）'}",
                f"  ログイン状態: {'ログイン済み（Bearer トークン付き）' if self.token else '未ログイン（トークンなし）'}",
            )
        )

        sources, last_error = self._fetch_file_sources(file_url, _log)

        if sources is None:
            return None, None, tr(
                f"Failed to get file source list: {last_error or 'unknown error'}",
                f"获取文件列表失败: {last_error or '未知错误'}",
                f"ファイルソース一覧の取得に失敗: {last_error or '不明なエラー'}",
            )

        if not isinstance(sources, list) or not sources:
            return None, None, tr(
                f"Empty file source list. Raw response: {sources!r}",
                f"文件列表为空，原始响应: {sources!r}",
                f"ファイルソース一覧が空です。生レスポンス: {sources!r}",
            )

        return self._pick_download_quality(sources, preferred_quality, _log)

    @staticmethod
    def _missing_file_url_error(video_info: dict) -> str:
        """Why a video without ``fileUrl`` cannot be downloaded."""

        message = video_info.get("message", "")
        embed = video_info.get("embedUrl", "")
        if "youtube" in embed or "youtu.be" in embed:
            return tr(
                f"This video is a YouTube embed and cannot be downloaded ({embed})",
                f"该视频为 YouTube 嵌入，无法下载（{embed}）",
                f"この動画は YouTube 埋め込みのためダウンロードできません（{embed}）",
            )
        if message == "errors.privateVideo":
            return tr(
                "Private video. Please login and try again.",
                "私有视频，请先登录后重试",
                "非公開動画です。ログインして再試行してください。",
            )
        return tr(
            f"fileUrl is empty. API response keys: {list(video_info.keys())}",
            f"fileUrl 为空，API 回复键: {list(video_info.keys())}",
            f"fileUrl が空です。API レスポンスキー: {list(video_info.keys())}",
        )

    def _fetch_file_sources(self, file_url: str, _log) -> tuple[Optional[list[dict]], str]:
        """The file's source list, trying each X-Version salt; returns ``(sources, last error)``."""

        sources: Optional[list[dict]] = None
        last_error = ""
        for idx, salt in enumerate(x_version_salts(), start=1):
            x_version = self.compute_x_version(file_url, salt)
            _log(f"  X-Version[{idx}]: {x_version}")
            resp = None
            try:
                resp = self.scraper.get(
                    file_url,
                    headers={**self._auth_headers(), "X-Version": x_version},
                    timeout=30,
                )
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    continue
                ok, data, parse_error = _try_response_json(
                    resp,
                    action=tr("file source request", "文件源请求", "ファイルソースリクエスト"),
                )
                if not ok:
                    last_error = parse_error
                    continue
                if isinstance(data, list) and data:
                    sources = data
                    break
                last_error = tr(
                    f"response is empty or not a list: {data!r}",
                    f"响应为空或非列表: {data!r}",
                    f"応答が空、またはリストではありません: {data!r}",
                )
            except Exception as exc:
                last_error = str(exc)
            finally:
                if resp is not None:
                    resp.close()
        return sources, last_error

    def _pick_download_quality(self, sources: list[dict], preferred_quality: str, _log) -> tuple[Optional[str], Optional[str], str]:
        """The preferred quality, falling back to lower ones: ``(url, quality name, error)``."""

        # Log all available qualities with their URL prefixes
        available_names = [
            s.get("name", "<no name>") if isinstance(s, dict) else "<invalid source>"
            for s in sources
        ]
        _log(
            tr(
                f"  Available qualities: {available_names}",
                f"  可用画质列表: {available_names}",
                f"  利用可能な画質: {available_names}",
            )
        )

        # Case-insensitive sources map
        sources_map: dict[str, dict] = {
            s.get("name", "").lower(): s for s in sources if isinstance(s, dict)
        }

        # Build preference order based on selected quality
        # preferred "Source" → ["Source","540","360"]
        # preferred "540"   → ["540","360"]
        # preferred "360"   → ["360"]
        _all_qualities = ["Source", "540", "360"]
        try:
            start_idx = _all_qualities.index(preferred_quality)
        except ValueError:
            start_idx = 0
        quality_order = _all_qualities[start_idx:]

        for quality in quality_order:
            entry = sources_map.get(quality.lower())
            if entry:
                src = entry.get("src")
                raw = src.get("download", "") if isinstance(src, dict) else ""
                if raw:
                    dl_url = f"https:{raw}" if raw.startswith("//") else raw
                    # Use the original name from the entry (preserves casing)
                    actual_name = entry.get("name", quality)
                    _log(
                        tr(
                            f"  Selected quality: {actual_name}  URL prefix: {dl_url[:60]}...",
                            f"  选择画质: {actual_name}  URL 前缀: {dl_url[:60]}…",
                            f"  選択画質: {actual_name}  URL プレフィックス: {dl_url[:60]}...",
                        )
                    )
                    return dl_url, actual_name, ""
                else:
                    _log(
                        tr(
                            f"  Quality {quality} exists but src.download is empty, skipped",
                            f"  画质 {quality} 存在但 src.download 为空，跳过",
                            f"  画質 {quality} は存在しますが src.download が空のためスキップ",
                        )
                    )

        tried = ", ".join(quality_order)
        return None, None, tr(
            f"No usable quality found (tried: {tried}; server returned: {available_names})",
            f"找不到可用画质（尝试过: {tried}；服务器返回: {available_names}）",
            f"利用可能な画質が見つかりません（試行: {tried}、サーバー応答: {available_names}）",
        )

    def get_image_info(self, image_id: str) -> tuple[Optional[dict], str]:
        """Fetch one image post, including its ``files``."""

        image_id = str(image_id or "").strip()
        if not image_id:
            return None, tr("Missing image id", "缺少图片 ID", "画像IDがありません")
        try:
            data = self._get_json(f"{BASE_API}/image/{image_id}")
        except Exception as exc:
            return None, _friendly_request_error(str(exc))
        if not isinstance(data, dict) or not data.get("id"):
            return None, _extract_api_message(data) or tr(
                "Image not found or not visible to the current account.",
                "图片不存在或当前账号不可见。",
                "画像が存在しないか、現在のアカウントでは表示できません。",
            )
        return data, ""

    def get_related(self, kind: str, item_id: str, *, limit: int = 12) -> tuple[list[dict], str]:
        """Related videos (``kind="video"``) or images (``kind="image"``)."""

        kind = "image" if str(kind).strip().lower().startswith("image") else "video"
        try:
            data = self._get_json(
                f"{BASE_API}/{kind}/{str(item_id).strip()}/related",
                params={"limit": str(max(1, min(50, int(limit))))},
            )
        except Exception as exc:
            return [], _friendly_request_error(str(exc))
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            return [], ""
        return [item for item in results if isinstance(item, dict)], ""

    def get_comments(
        self,
        kind: str,
        item_id: str,
        *,
        page: int = 0,
        parent: str = "",
        limit: int = 20,
    ) -> tuple[list[dict], int | None, str]:
        """One page of comments, or the replies to ``parent``. Returns ``(rows, total, error)``."""

        kind = "image" if str(kind).strip().lower().startswith("image") else "video"
        params = {"page": str(max(0, int(page))), "limit": str(max(1, min(50, int(limit))))}
        if parent:
            params["parent"] = str(parent)
        try:
            data = self._get_json(f"{BASE_API}/{kind}/{str(item_id).strip()}/comments", params=params)
        except Exception as exc:
            return [], None, _friendly_request_error(str(exc))
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            return [], None, ""
        total = data.get("count")
        return (
            [row for row in data["results"] if isinstance(row, dict)],
            total if isinstance(total, int) and total >= 0 else None,
            "",
        )
