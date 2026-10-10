"""解析评论里的时间轴，例如 "(1:14:40-2:14:40)"、"P2 14:40~20:00"。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_TIME = r"(?:(\d{1,2})[:：])?(\d{1,3})[:：](\d{1,2})"
_PATTERN = re.compile(
    r"(?:[Pp]\s*(\d{1,3})\s*)?"          # 可选分P，如 P2
    r"[(（\[【]?\s*"
    + _TIME +
    r"\s*(?:-|~|～|—|–|至|到)+\s*"
    + _TIME +
    r"\s*[)）\]】]?"
)


@dataclass(frozen=True)
class Clip:
    start: int            # 秒
    end: int              # 秒
    page: Optional[int]   # 分P，None 表示未指定

    @property
    def label(self) -> str:
        """标准化后的时间轴文本，如 1:14:40-2:14:40。"""
        return f"{format_seconds(self.start)}-{format_seconds(self.end)}"

    @property
    def duration(self) -> int:
        return self.end - self.start


def _to_seconds(h: Optional[str], m: str, s: str) -> Optional[int]:
    hours = int(h) if h else 0
    minutes, seconds = int(m), int(s)
    if seconds >= 60 or (h and minutes >= 60):
        return None
    return hours * 3600 + minutes * 60 + seconds


def format_seconds(total: int) -> str:
    h, rest = divmod(int(total), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_clip(text: str) -> Optional[Clip]:
    """返回文本中第一个合法的时间轴，没有则返回 None。"""
    if not text:
        return None
    for match in _PATTERN.finditer(text):
        page, h1, m1, s1, h2, m2, s2 = match.groups()
        start = _to_seconds(h1, m1, s1)
        end = _to_seconds(h2, m2, s2)
        if start is None or end is None or end <= start:
            continue
        return Clip(start=start, end=end, page=int(page) if page else None)
    return None
