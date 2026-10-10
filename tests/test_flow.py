"""用假的 HTTP 响应跑通 找评论 → 下载 → 分片上传 → 投稿 的完整流程。"""
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bili_clipper import runner as runner_mod  # noqa: E402
from bili_clipper.auth import AccountStore  # noqa: E402
from bili_clipper.config import load_config  # noqa: E402
from bili_clipper.runner import Runner  # noqa: E402


class FakeResp:
    def __init__(self, payload=None, status=200, content=b"", headers=None):
        self.payload, self.status_code = payload, status
        self.content, self.headers = content, headers or {}

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(self.status_code)


class FakeBili:
    def __init__(self):
        self.calls = []
        self.submitted = None
        self.chunks = []

    def __call__(self, session, method, url, params=None, data=None, json=None, **kw):
        self.calls.append((method, url))
        u = urlparse(url)
        q = {**{k: v[0] for k, v in parse_qs(u.query).items()}, **(params or {})}
        path = u.netloc + u.path
        if path.endswith("/x/web-interface/nav"):
            logged = session.cookies.get("SESSDATA") == "ok"
            return FakeResp({"code": 0 if logged else -101, "data": {
                "isLogin": logged, "mid": 5, "uname": "tester", "vipStatus": 1,
                "wbi_img": {"img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                            "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}})
        if path.endswith("/x/frontend/finger/spi"):
            return FakeResp({"code": 0, "data": {"b_3": "b3", "b_4": "b4"}})
        if path.endswith("/history/cursor"):
            return FakeResp({"code": 0, "data": {"cursor": {"max": 0}, "list": [
                {"title": "直播回放", "history": {"oid": 100, "bvid": "BV1aaaaaaaaa", "page": 1,
                                                 "business": "archive"}}]}})
        if path.endswith("/x/v2/reply/wbi/main"):
            assert "w_rid" in q
            return FakeResp({"code": 0, "data": {"cursor": {"is_end": True}, "replies": [
                {"rpid": 777, "mid": 5, "ctime": 1000, "content": {"message": "（1:14:40-2:14:40）"}},
                {"rpid": 666, "mid": 5, "ctime": 900, "content": {"message": "（0:10-0:20）"}},
            ]}})
        if path.endswith("/x/web-interface/view"):
            return FakeResp({"code": 0, "data": {
                "aid": 100, "bvid": "BV1aaaaaaaaa", "title": "直播回放", "tid": 17,
                "pic": "http://i0.hdslb.com/cover.jpg", "owner": {"name": "主播"},
                "pages": [{"page": 1, "cid": 1, "duration": 3 * 3600, "part": "P1"}]}})
        if path.endswith("member.bilibili.com/preupload"):
            assert q["upcdn"] == "bda2"
            return FakeResp({"OK": 1, "endpoint": "//upos-cs-upcdnbda2.bilivideo.com",
                             "upos_uri": "upos://ugcfx2lf/n123456.mp4", "auth": "AUTH",
                             "biz_id": 42, "chunk_size": 4})
        if "upos-cs-upcdnbda2" in path:
            assert kw["headers"]["X-Upos-Auth"] == "AUTH"
            if method == "POST" and "uploads" in u.query:
                return FakeResp({"upload_id": "UPID"})
            if method == "PUT":
                self.chunks.append((int(q["partNumber"]), data))
                return FakeResp({})
            if method == "POST":
                assert len(json["parts"]) == len(self.chunks)
                return FakeResp({"OK": 1})
        if path.endswith("cover.jpg"):
            return FakeResp(content=b"img", headers={"Content-Type": "image/jpeg"})
        if path.endswith("/x/vu/web/cover/up"):
            assert data["csrf"] == "jct"
            return FakeResp({"code": 0, "data": {"url": "http://cover/uploaded.jpg"}})
        if path.endswith("/x/geetest/pre/add"):
            return FakeResp({"code": 0})
        if path.endswith("/x/vu/web/add"):
            assert q["csrf"] == "jct"
            self.submitted = json
            return FakeResp({"code": 0, "data": {"aid": 1, "bvid": "BV1new"}})
        raise AssertionError(f"unexpected request {method} {url}")


def test_full_flow(tmp_path, monkeypatch):
    fake = FakeBili()
    monkeypatch.setattr(requests.Session, "request",
                        lambda self, method, url, **kw: fake(self, method, url, **kw))
    monkeypatch.setattr("time.sleep", lambda s: None)

    downloaded = {}

    def fake_download(bvid, page, clip, cookies, settings, work_dir):
        work_dir.mkdir(parents=True, exist_ok=True)
        p = work_dir / "clip.mp4"
        p.write_bytes(b"0123456789")  # 10 字节, chunk_size=4 → 3 个分片
        downloaded.update(bvid=bvid, page=page, clip=clip.label, sess=cookies.get("SESSDATA"))
        return p

    monkeypatch.setattr(runner_mod, "download_clip", fake_download)

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"任务列表": [{
        "投稿视频名": "", "tag": "切片", "投稿账号": "acc", "投稿账号密码": "",
    }]}, ensure_ascii=False), encoding="utf-8")
    config = load_config(cfg)
    AccountStore(config.settings.data_dir).save("acc", {"SESSDATA": "ok", "bili_jct": "jct"})

    Runner(config, interactive=False).run_once()

    assert downloaded == {"bvid": "BV1aaaaaaaaa", "page": 1, "clip": "1:14:40-2:14:40", "sess": "ok"}
    assert sorted(n for n, _ in fake.chunks) == [1, 2, 3]
    assert b"".join(d for _, d in sorted(fake.chunks)) == b"0123456789"
    sub = fake.submitted
    assert sub["title"] == "直播回放 1:14:40-2:14:40"
    assert sub["tid"] == 17 and sub["copyright"] == 2 and sub["tag"] == "切片"
    assert sub["source"] == "https://www.bilibili.com/video/BV1aaaaaaaaa"
    assert sub["cover"] == "http://cover/uploaded.jpg"
    assert sub["videos"][0]["filename"] == "n123456"

    state = json.loads((config.settings.data_dir / "state.json").read_text(encoding="utf-8"))
    assert state["acc->acc"]["last_ctime"] == 1000

    # 第二次运行：没有新评论，不应再次投稿
    fake.submitted = None
    Runner(config, interactive=False).run_once()
    assert fake.submitted is None
