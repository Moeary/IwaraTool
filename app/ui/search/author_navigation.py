"""Browse one author's works from a search result."""

from __future__ import annotations

from typing import Any

from ...core.search import SearchAuthor, SearchVideo
from ...i18n import tr
from .result_authors import _author_source_info, _author_subscription_target
from ..navigation import record_navigation
from ..worker_lifecycle import on_finished
from .author_workers import SearchAuthorProfileWorker


class SearchAuthorNavigationMixin:
    """Browse one author's works from a search result."""

    def _selected_author_subscription_target(
        self,
        values: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str, str, str] | None:
        """Return one author target when the current selection is unambiguous."""

        targets: dict[str, tuple[str, str, str, str]] = {}
        for value in values if values is not None else self._selected_data():
            if value.get("kind") not in {"video", "author"}:
                continue
            target = self._author_navigation_target(value.get("data"))
            if target:
                targets.setdefault(target[0].casefold(), target)
        return next(iter(targets.values())) if len(targets) == 1 else None

    @staticmethod
    def _author_navigation_target(
        value: SearchAuthor | SearchVideo | object,
    ) -> tuple[str, str, str, str] | None:
        """Use verified bridge metadata and distinguish card keys from user IDs."""

        raw = value.raw if isinstance(value, (SearchAuthor, SearchVideo)) and isinstance(value.raw, dict) else {}
        if isinstance(value, SearchVideo) and (
            value.source_kind == "oreno3d" or raw.get("oreno3d_url")
        ):
            mapped = raw.get("oreno_iwara_author")
            has_mapping = isinstance(mapped, dict) and bool(
                mapped.get("username") or mapped.get("slug") or mapped.get("profile_url")
            )
            if not has_mapping and raw.get("_iwara_metadata_loaded") is not True:
                return None
            if not has_mapping:
                user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
                author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
                if not any(
                    str(candidate.get(key) or "").strip()
                    for candidate in (user, author, raw)
                    for key in ("username", "slug", "handle", "author_username")
                ):
                    return None
        target = _author_subscription_target(value)
        if target and isinstance(value, SearchAuthor):
            raw_user = raw.get("user") if isinstance(raw.get("user"), dict) else raw
            real_id = str(raw_user.get("id") or raw_user.get("userId") or raw_user.get("user_id") or "").strip()
            if real_id:
                target = (target[0], target[1], real_id, target[3])
            elif target[2].casefold() == target[0].casefold():
                # normalize_author uses username as a stable card key when
                # a compact result lacks user.id; resolve the profile first.
                target = (target[0], target[1], "", target[3])
        return target

    def _clear_author_navigation(self):
        """Exit author browsing and invalidate pending navigation requests."""

        had_target = getattr(self, "_author_video_target", None) is not None
        self._author_video_target = None
        self._cancel_pending_author_navigation()
        if had_target:
            self._keyword_edit.setPlaceholderText(
                tr("Keywords or titles…", "输入关键词或标题…", "キーワードまたはタイトル…")
            )
            self._set_scope_hint("")

    def _cancel_pending_author_navigation(self):
        self._author_action_generation = getattr(self, "_author_action_generation", 0) + 1
        getattr(self, "_pending_author_actions", {}).clear()
        for worker in getattr(self, "_author_profile_workers", []):
            worker.requestInterruption()

    def _view_selected_author_works(self):
        self._request_selected_author_action("view")

    def _subscribe_selected_author_and_go(self):
        self._request_selected_author_action("subscribe_and_go")

    def _request_selected_author_action(self, action: str):
        values = self._selected_data()
        if len(values) != 1:
            self._show_warning(
                tr("Select one result for this author action", "请只选择一个结果后操作作者", "作者の操作には1件だけ選択してください")
            )
            return
        data = values[0].get("data")
        if not isinstance(data, (SearchAuthor, SearchVideo)):
            return
        self._cancel_pending_author_navigation()
        target = self._author_navigation_target(data)
        if target:
            source_url, source_origin = _author_source_info(data)
            self._run_author_action(target, action, source_url=source_url, source_origin=source_origin)
            return
        if not isinstance(data, SearchVideo):
            self._show_warning(tr("This result has no Iwara account", "当前结果没有 Iwara 账号信息", "この結果にはIwaraアカウント情報がありません"))
            return
        self._pending_author_actions[data.video_id] = action
        self._status_label.setText(
            tr("Resolving the Iwara author…", "正在解析 Iwara 作者…", "Iwara 作者を解析中…")
        )
        raw = data.raw if isinstance(data.raw, dict) else {}
        if data.source_kind == "oreno3d" or raw.get("oreno3d_url"):
            self._start_oreno_author_resolution(data)
        else:
            self._start_iwara_author_hydration(data)

    def _run_author_action(
        self,
        target: tuple[str, str, str, str],
        action: str,
        *,
        source_url: str = "",
        source_origin: str = "",
    ):
        if action == "subscribe_and_go":
            self._subscribe_to_author(
                target,
                source_url=source_url,
                source_origin=source_origin,
                navigate=True,
            )
        elif action == "view":
            if target[2]:
                self._show_author_works_target(target)
            else:
                self._start_author_profile_resolution(target)

    def _finish_pending_author_action(self, video: SearchVideo):
        action = getattr(self, "_pending_author_actions", {}).pop(video.video_id, "")
        if not action:
            return
        target = self._author_navigation_target(video)
        if target:
            source_url, source_origin = _author_source_info(video)
            self._run_author_action(target, action, source_url=source_url, source_origin=source_origin)
            return
        source_url, _source_origin = _author_source_info(video)
        message = tr(
            "Could not identify an Iwara account for this author.",
            "无法确定该作者的 Iwara 账号。",
            "この作者のIwaraアカウントを特定できません。",
        )
        if source_url:
            message += tr(
                " You can still use ‘Open author page in browser’ to browse the Oreno3D source.",
                " 仍可使用右键菜单“在浏览器打开作者页”浏览 Oreno3D 来源。",
                " 右クリックメニューの「作者ページをブラウザーで開く」からOreno3Dの元ページを閲覧できます。",
            )
        reason = str(video.raw.get("oreno3d_author_error") or "")
        if reason:
            message += f"\n{reason}"
        self._status_label.setText(message)
        self._show_warning(message)

    def _start_author_profile_resolution(self, target: tuple[str, str, str, str]):
        if any(
            worker.isRunning()
            and not worker.isInterruptionRequested()
            and worker.generation == self._generation
            and worker.action_generation == getattr(self, "_author_action_generation", 0)
            and worker.target[0].casefold() == target[0].casefold()
            for worker in self._author_profile_workers
        ):
            return
        worker = SearchAuthorProfileWorker(
            self._generation,
            getattr(self, "_author_action_generation", 0),
            target,
        )
        self._author_profile_workers.append(worker)
        worker.result_ready.connect(self._on_author_profile_result)
        on_finished(worker, self, self._cleanup_author_profile_worker)
        self._status_label.setText(
            tr(f"Resolving @{target[0]}’s Iwara user ID…", f"正在解析 @{target[0]} 的 Iwara 用户 ID…", f"@{target[0]} のIwaraユーザーIDを取得中…")
        )
        worker.start()

    def _on_author_profile_result(self, result: object):
        if not isinstance(result, dict) or int(result.get("generation", -1)) != self._generation:
            return
        if int(result.get("action_generation", -1)) != getattr(self, "_author_action_generation", 0):
            return
        target = self._author_navigation_target(result.get("author"))
        if target and target[2]:
            self._show_author_works_target(target)
            return
        message = str(result.get("error") or tr("Could not resolve the Iwara user ID", "无法解析 Iwara 用户 ID", "IwaraユーザーIDを取得できません"))
        self._status_label.setText(message)
        self._show_warning(message)

    def _cleanup_author_profile_worker(self, worker: SearchAuthorProfileWorker):
        if worker in self._author_profile_workers:
            self._author_profile_workers.remove(worker)
        worker.deleteLater()

    def _show_author_works_target(self, target: tuple[str, str, str, str], *, record: bool = True):
        """Start a user-scoped video search; Back returns to the search it replaced.

        ``record`` is False when the window has already recorded the session.
        """

        if record:
            record_navigation(self)
        self._auto_search_timer.stop()
        self._set_combo_data(self._source_combo, "iwara")
        self._set_combo_data(self._scope_combo, "videos")
        self._keyword_edit.clear()
        self._auto_search_timer.stop()
        self._enter_author_mode(target)
        self._start_search()

    def _enter_author_mode(self, target: tuple[str, str, str, str]):
        self._author_video_target = target
        self._keyword_edit.setPlaceholderText(
            tr(f"@{target[0]}’s works; type keywords for a new search…", f"@{target[0]} 的作品；输入关键词开始新搜索…", f"@{target[0]} の作品。キーワード入力で新しい検索…")
        )
        self._set_scope_hint(
            tr(
                f"Showing @{target[0]}’s works. Enter keywords or change the source or scope to return to general search.",
                f"正在查看 @{target[0]} 的作品。输入关键词或切换数据源、搜索类型可返回普通搜索。",
                f"@{target[0]} の作品を表示中。キーワード入力やソース・検索対象の変更で通常の検索に戻ります。",
            )
        )

    def _search_selected_author(self):
        self._view_selected_author_works()
