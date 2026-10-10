import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bili_clipper.client import mixin_key, sign_wbi  # noqa: E402
from bili_clipper.config import load_config  # noqa: E402
from bili_clipper.downloader import build_command  # noqa: E402
from bili_clipper.finder import Candidate, find_clip_comments  # noqa: E402
from bili_clipper.runner import render_title  # noqa: E402
from bili_clipper.state import State  # noqa: E402
from bili_clipper.timeline import Clip, parse_clip  # noqa: E402
from bili_clipper.uploader import clean_tags  # noqa: E402


@pytest.mark.parametrize("text,start,end,page", [
    ("（1:14:40-2:14:40）", 4480, 8080, None),
    ("(1:14:40-2:14:40)", 4480, 8080, None),
    ("1:14:40-2:14:40", 4480, 8080, None),
    ("求切片 14:40~20:05 谢谢", 880, 1205, None),
    ("P2 1：02：03 到 1：05：00", 3723, 3900, 2),
    ("【0:10—0:30】", 10, 30, None),
    ("1:59:00 - 2:01:30", 7140, 7290, None),
])
def test_parse_clip(text, start, end, page):
    assert parse_clip(text) == Clip(start, end, page)


@pytest.mark.parametrize("text", ["", "好看", "2:00-1:00", "1:61:00-2:00:00", "10:70-11:00"])
def test_parse_clip_invalid(text):
    assert parse_clip(text) is None


def test_clip_label():
    assert parse_clip("1:14:40-2:14:40").label == "1:14:40-2:14:40"
    assert parse_clip("4:05-14:40").label == "4:05-14:40"


def test_wbi_sign_known_vector():
    # bilibili-API-collect 文档中的示例
    key = mixin_key("7cd084941338484aae1ad9425b84077c", "4932caff0ff746eab6f01bf08b70ac45")
    assert key == "ea1db124af3c7062474693fa704f4ff8"
    signed = sign_wbi({"foo": "114", "bar": "514", "zab": 1919810}, key, wts=1702204169)
    assert signed["w_rid"] == "8f6f2b5b3d485fe1886cec6a0be8c5d4"


def test_render_title():
    vals = {"原标题": "原视频", "时间轴": "1:14:40-2:14:40"}
    assert render_title("", vals) == "原视频 1:14:40-2:14:40"
    assert render_title("【切片】{原标题}", vals) == "【切片】原视频"
    long = render_title("", {"原标题": "长" * 100, "时间轴": "1:14:40-2:14:40"})
    assert len(long) <= 80 and long.endswith("… 1:14:40-2:14:40")


def test_clean_tags():
    assert clean_tags([]) == ["切片"]
    assert clean_tags(["a", "a", "b,c", "x" * 30]) == ["a", "bc", "x" * 20]


def test_load_config_chinese_keys(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps([{
        "投稿视频名": "", "tag": "切片，直播", "投稿账号": "u1", "投稿账号密码": "p1",
    }], ensure_ascii=False), encoding="utf-8")
    c = load_config(cfg)
    t = c.tasks[0]
    assert t.tags == ["切片", "直播"]
    assert t.watch_account == "u1" and t.watch_password == "p1"
    assert c.settings.data_dir == tmp_path / "data"


def test_example_config_loads():
    c = load_config(Path(__file__).resolve().parent.parent / "config.example.json")
    assert len(c.tasks) == 2 and c.tasks[1].enabled is False


class FakeClient:
    def __init__(self, replies):
        self.replies = replies

    def iter_replies(self, aid, max_pages):
        yield from self.replies.get(aid, [])


def _reply(rpid, mid, ctime, msg):
    return {"rpid": rpid, "mid": mid, "ctime": ctime, "content": {"message": msg}}


def test_find_latest_comment():
    client = FakeClient({
        1: [_reply(11, 99, 300, "1:00-2:00"), _reply(12, 5, 290, "0:10-0:20"),
            _reply(13, 5, 100, "0:01-0:02")],
        2: [_reply(21, 5, 400, "不是时间轴"), _reply(22, 5, 350, "(1:14:40-2:14:40)")],
    })
    cands = [Candidate(1, "BV1", "a"), Candidate(2, "BV2", "b")]
    found = find_clip_comments(client, 5, cands, since_ctime=0, reply_pages=3, processed=set())
    assert [c.rpid for c in found] == [22, 12, 13]
    found = find_clip_comments(client, 5, cands, since_ctime=290, reply_pages=3, processed=set())
    assert [c.rpid for c in found] == [22]
    found = find_clip_comments(client, 5, cands, since_ctime=0, reply_pages=3, processed={22})
    assert found[0].rpid == 12


def test_state(tmp_path):
    s = State(tmp_path / "state.json")
    s.mark_failed("k", 1, 100, "err", max_attempts=2)
    assert s.processed_ids("k") == set()
    s.mark_failed("k", 1, 100, "err", max_attempts=2)
    assert s.processed_ids("k") == {1}
    s.mark_done("k", 2, 200, {"x": 1})
    s2 = State(tmp_path / "state.json")
    assert s2.processed_ids("k") == {1, 2} and s2.task("k")["last_ctime"] == 200


def test_build_command(tmp_path):
    from bili_clipper.config import GlobalSettings
    s = GlobalSettings(precise_cut=True, ffmpeg="/usr/bin/ffmpeg")
    cmd = build_command("https://x", Clip(4480, 8080, None), tmp_path / "out",
                        tmp_path / "c.txt", tmp_path / "r", s)
    assert "*4480-8080" in cmd and "--force-keyframes-at-cuts" in cmd
    assert cmd[cmd.index("--ffmpeg-location") + 1] == "/usr/bin/ffmpeg"
