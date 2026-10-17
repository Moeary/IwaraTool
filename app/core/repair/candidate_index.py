"""Index local history/subscription records so each scanned file is matched fast.

A scan used to compare every file with every local record: one compiled
regex per known ID and one ``SequenceMatcher`` per title. With thousands of
files and records that took minutes and held the GIL against the GUI thread.
The index answers the same questions from dictionaries and only fuzzy-scores
records that can still reach ``CANDIDATE_SCORE_FLOOR``; the decision rules
themselves are unchanged (see ``pick_repair_candidate``).
"""

from __future__ import annotations

import bisect
import math
import os
import re
import threading
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Any

from .matching import (
    CANDIDATE_SCORE_FLOOR,
    author_match_bonus,
    candidate_author_text,
    filename_search_text,
    normalize_match_text,
    pick_repair_candidate,
)

# ``(?<![A-Za-z0-9_-])ID(?![A-Za-z0-9_-])`` matches exactly when the ID is one
# whole run of these characters, so a dictionary lookup per run is equivalent.
_ID_RUN_RE = re.compile(r"[A-Za-z0-9_-]+")
# Runs like ``title_VideoID01`` (the default naming template) also expose
# their underscore/dash separated parts as ID candidates.
_ID_PART_RE = re.compile(r"[A-Za-z0-9]+")
_EPSILON = 1e-9


def _bigrams(text: str) -> list[str]:
    return [text[index:index + 2] for index in range(len(text) - 1)]


class RepairCandidateIndex:
    """Known video IDs and normalized titles/authors of local candidates."""

    def __init__(self, candidates: list[dict[str, Any]]):
        self._ids: dict[str, str] = {}
        # (candidate, normalized title) in candidate order; titleless ones
        # always score 0 and are left out.
        self._entries: list[tuple[dict[str, Any], str]] = []
        self._by_title: dict[str, list[int]] = defaultdict(list)
        self._by_author: dict[str, list[int]] = defaultdict(list)
        self._by_bigram: dict[str, list[int]] = defaultdict(list)
        self._author_bonus_cache: dict[str, dict[int, float]] = {}
        # Character counts per entry, filled on first use by ``select``.
        self._char_counts: dict[int, Counter[str]] = {}
        # Scoring is pure-Python CPU work: scan threads running it in
        # parallel finish no sooner, they only outvote the GUI thread for the
        # GIL. One file is scored at a time; network lookups stay parallel.
        self._select_lock = threading.Lock()
        seen: set[str] = set()
        for candidate in candidates:
            video_id = str(candidate.get("video_id", "") or candidate.get("id", "") or "").strip()
            if not video_id or video_id.casefold() in seen:
                continue
            seen.add(video_id.casefold())
            self._ids[video_id.casefold()] = video_id
            self._add_entry(candidate)
        by_length = sorted((len(title), index) for index, (_, title) in enumerate(self._entries))
        self._lengths = [length for length, _ in by_length]
        self._length_order = [index for _, index in by_length]

    def _add_entry(self, candidate: dict[str, Any]):
        title = normalize_match_text(candidate.get("title", ""))
        if not title:
            return
        index = len(self._entries)
        self._entries.append((candidate, title))
        self._by_title[title].append(index)
        author = candidate_author_text(candidate)
        if author:
            self._by_author[author].append(index)
        for gram in set(_bigrams(title)):
            self._by_bigram[gram].append(index)

    @property
    def known_ids(self) -> set[str]:
        return set(self._ids.values())

    def ids_in_name(self, stem: str) -> list[str]:
        """Known video IDs that appear as standalone tokens in a filename stem."""

        found: list[str] = []
        for run in _ID_RUN_RE.findall(str(stem or "")):
            parts = [run]
            if "_" in run or "-" in run:
                parts.extend(_ID_PART_RE.findall(run))
            for part in parts:
                video_id = self._ids.get(part.casefold())
                if video_id and video_id not in found:
                    found.append(video_id)
        return found

    def select(self, path: str) -> tuple[dict[str, Any] | None, bool]:
        """The local candidate a file's name clearly refers to: ``(candidate, ambiguous)``."""

        title = normalize_match_text(filename_search_text(path))
        if not title or not self._entries:
            return None, False
        with self._select_lock:
            return self._select_title(path, title)

    def _select_title(self, path: str, title: str) -> tuple[dict[str, Any] | None, bool]:
        bonuses = self._author_bonuses(normalize_match_text(os.path.basename(os.path.dirname(path))))
        indices = set(self._by_title.get(title, ()))
        indices.update(bonuses)
        indices.update(self._similar_titles(title, CANDIDATE_SCORE_FLOOR))

        matcher = SequenceMatcher(None)
        matcher.set_seq1(title)
        title_counts = Counter(title)
        ranked: list[tuple[float, dict[str, Any]]] = []
        for index in sorted(indices):
            candidate, candidate_title = self._entries[index]
            bonus = bonuses.get(index, 0.0)
            ratio = self._bounded_ratio(matcher, title_counts, index, CANDIDATE_SCORE_FLOOR - bonus)
            if ratio is None:
                continue
            score = ratio + bonus + (1.0 if title == candidate_title else 0.0)
            if score > 0:
                ranked.append((score, candidate))
        return pick_repair_candidate(ranked)

    def _bounded_ratio(
        self, matcher: SequenceMatcher, title_counts: Counter[str], index: int, needed: float,
    ) -> float | None:
        """``SequenceMatcher(None, title, other).ratio()``, or None once it cannot reach ``needed``.

        The length and shared-character bounds are ``real_quick_ratio`` and
        ``quick_ratio``, computed from cached counts instead of re-indexing
        every candidate title.
        """

        title = matcher.a
        other = self._entries[index][1]
        if title == other:
            return 1.0
        total = len(title) + len(other)
        if 2 * min(len(title), len(other)) / total < needed - _EPSILON:
            return None
        other_counts = self._char_counts.get(index)
        if other_counts is None:
            other_counts = self._char_counts[index] = Counter(other)
        shared = sum(min(count, other_counts[char]) for char, count in title_counts.items() if char in other_counts)
        if 2 * shared / total < needed - _EPSILON:
            return None
        matcher.set_seq2(other)
        return matcher.ratio()

    def _author_bonuses(self, author_hint: str) -> dict[int, float]:
        """Entry index -> author bonus for a folder name (cached; folders repeat)."""

        if not author_hint:
            return {}
        cached = self._author_bonus_cache.get(author_hint)
        if cached is None:
            cached = {}
            for author, indices in self._by_author.items():
                bonus = author_match_bonus(author_hint, author)
                if bonus:
                    cached.update(dict.fromkeys(indices, bonus))
            self._author_bonus_cache[author_hint] = cached
        return cached

    def _similar_titles(self, title: str, ratio: float) -> list[int]:
        """Entries whose title might reach ``ratio`` against ``title`` (a superset).

        A ratio ``r`` bounds the edit distance by ``k <= (la + lb) * (1 - r)``,
        and strings within ``k`` edits share at least ``max(la, lb) - 1 - 2k``
        bigrams (q-gram lemma). A title that must share ``T`` of the file's
        ``n`` bigrams contains one of any ``n - T + 1`` of them, so only the
        postings of the file's rarest bigrams need to be visited.
        """

        length = len(title)
        low = math.ceil(length * ratio / (2 - ratio) - _EPSILON)
        high = math.floor(length * (2 - ratio) / ratio + _EPSILON)
        start = bisect.bisect_left(self._lengths, low)
        stop = bisect.bisect_right(self._lengths, high)
        grams = _bigrams(title)
        shared = min(
            (
                max(length, other) - 1 - 2 * math.floor((length + other) * (1 - ratio) + _EPSILON)
                for other in range(max(low, 1), high + 1)
            ),
            default=0,
        )
        if shared <= 0 or not grams:
            return self._length_order[start:stop]

        counts: dict[str, int] = defaultdict(int)
        for gram in grams:
            counts[gram] += 1
        needed = len(grams) - shared + 1
        result: set[int] = set()
        for gram in sorted(counts, key=lambda value: len(self._by_bigram.get(value, ()))):
            result.update(self._by_bigram.get(gram, ()))
            needed -= counts[gram]
            if needed <= 0:
                break
        return [index for index in result if low <= len(self._entries[index][1]) <= high]
