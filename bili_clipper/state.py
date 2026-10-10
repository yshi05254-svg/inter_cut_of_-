"""记录每个任务已处理过的评论，避免重复投稿。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: Dict[str, Any] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError:
                self.data = {}

    def task(self, key: str) -> Dict[str, Any]:
        t = self.data.setdefault(key, {})
        t.setdefault("last_ctime", 0)
        t.setdefault("processed", {})
        t.setdefault("failures", {})
        return t

    def processed_ids(self, key: str) -> set:
        t = self.task(key)
        return {int(k) for k in t["processed"]} | {
            int(k) for k, n in t["failures"].items() if n.get("given_up")}

    def mark_done(self, key: str, rpid: int, ctime: int, info: Dict[str, Any]) -> None:
        t = self.task(key)
        t["processed"][str(rpid)] = info
        t["failures"].pop(str(rpid), None)
        t["last_ctime"] = max(int(t["last_ctime"]), int(ctime))
        self.save()

    def mark_failed(self, key: str, rpid: int, ctime: int, error: str, max_attempts: int) -> int:
        t = self.task(key)
        f = t["failures"].setdefault(str(rpid), {"count": 0})
        f["count"] += 1
        f["error"] = error[:500]
        if f["count"] >= max_attempts:
            f["given_up"] = True
            t["last_ctime"] = max(int(t["last_ctime"]), int(ctime))
        self.save()
        return f["count"]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
