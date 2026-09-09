"""Multi-platform social layer: shared geo/keywords, per-platform parsers,
registry config, and pipeline/replay wiring. No network calls (fixtures only).

Run:  env/bin/python -m tests.test_social   (or python3 -m tests.test_social)
"""
from __future__ import annotations

import os

from fusion.ingest_social import (
    SocialPost,
    enabled_platforms,
    extract_keywords,
    geolocate,
    is_social_event,
    is_social_source,
    social_to_event,
)


def test_geolocate_coords():
    lat, lon, place = geolocate("sighting at 26.571, 56.251 near the strait")
    assert (lat, lon) == (26.571, 56.251), (lat, lon)
    assert place == "26.571,56.251"


def test_geolocate_gazetteer_longest_match():
    lat, lon, place = geolocate("Tanker seized in the Strait of Hormuz, near Hormuz island")
    assert place == "Strait Of Hormuz", place
    assert abs(lat - 26.57) < 1e-9 and abs(lon - 56.25) < 1e-9


def test_geolocate_none():
    assert geolocate("cute cat video, like and subscribe") == (None, None, None)


def test_keywords():
    assert extract_keywords("IRGC missile strike on tanker near Hormuz") == \
        ["tanker", "missile", "strike", "irgc"]


def test_legacy_telegram_dict_compat():
    """Old data/replay/*_telegram.json files have no `platform` field."""
    d = {"id": "tg:intelslava/123", "ts": "2026-08-18T10:00:00+00:00",
         "channel": "intelslava", "text": "tanker near Hormuz",
         "url": "https://t.me/intelslava/123", "lat": 26.57, "lon": 56.25,
         "place": "Hormuz", "keywords": ["tanker"], "views": 5000, "has_media": False}
    p = SocialPost.from_dict(d)
    assert p.platform == "telegram", p.platform
    assert p.to_dict()["platform"] == "telegram"


def test_social_to_event_domains():
    base = dict(ts="2026-08-18T10:00:00+00:00", text="missile strike near Hormuz",
                lat=26.57, lon=56.25, place="Hormuz", keywords=["missile", "strike"],
                views=3000, has_media=False)
    tg = social_to_event(SocialPost(id="tg:c/1", platform="telegram", channel="c",
                                    url="https://t.me/c/1", **base))
    assert tg.source_domain == "t.me/c" and tg.is_conflict and tg.root_code == "SOCIAL"
    rd = social_to_event(SocialPost(id="reddit:r/worldnews/a1", platform="reddit",
                                    channel="r/worldnews", url="https://www.reddit.com/r/worldnews/comments/a1/", **base))
    assert rd.source_domain == "reddit.com/r/worldnews", rd.source_domain
    assert rd.is_conflict and rd.goldstein < 0
    bs = social_to_event(SocialPost(id="bsky:alice/abc", platform="bluesky", channel="alice",
                                    url="https://bsky.app/profile/alice/post/abc", **base))
    assert bs.source_domain == "bsky.app"
    md = social_to_event(SocialPost(id="mastodon:alice/42", platform="mastodon", channel="alice",
                                    url="https://mastodon.social/@alice/42", **base))
    assert md.source_domain == "mastodon.social", md.source_domain
    # engagement proxy: views//1000 -> mentions
    assert tg.num_mentions == 3, tg.num_mentions
    quiet = social_to_event(SocialPost(id="tg:c/2", platform="telegram", channel="c",
                                       url="https://t.me/c/2", ts=base["ts"], text="fishing boats near Hormuz",
                                       lat=26.5, lon=56.2, place="Hormuz", keywords=[],
                                       views=None, has_media=False))
    assert not quiet.is_conflict and quiet.num_mentions == 1


def test_is_social_event():
    assert is_social_source("t.me/intelslava")
    assert is_social_source("reddit.com/r/worldnews")
    assert is_social_source("bsky.app")
    assert is_social_source("mastodon.social")
    assert not is_social_source("cnn.com")
    assert is_social_event({"root_code": "SOCIAL", "source_domain": "x.com", "url": ""})
    assert is_social_event({"source_domain": "reddit.com/r/worldnews", "url": ""})
    assert not is_social_event({"source_domain": "cnn.com", "url": "https://cnn.com/a",
                                "root_code": "18", "event_code": "181"})
    tg = social_to_event(SocialPost(id="tg:c/1", platform="telegram", channel="c",
                                    url="https://t.me/c/1", ts="2026-08-18T10:00:00+00:00",
                                    text="strike", lat=1.0, lon=1.0, place="Hormuz",
                                    keywords=["strike"], views=10, has_media=False))
    assert is_social_event(tg)


def test_reddit_parser():
    from fusion.ingest_reddit import parse_listing
    payload = {"data": {"after": "t3_xyz", "children": [
        {"data": {"id": "a1", "created_utc": 1787011200.0, "title": "Tanker attacked near Hormuz",
                  "selftext": "details here", "permalink": "/r/worldnews/comments/a1/x/",
                  "author": "osint_fan", "score": 2500, "num_comments": 300,
                  "post_hint": "self", "preview": None, "url_overridden_by_dest": ""}},
        {"data": {"id": "a2", "created_utc": 1787011300.0, "title": "Cute cats",
                  "selftext": "", "permalink": "/r/worldnews/comments/a2/y/",
                  "author": "x", "score": 5, "num_comments": 1}},
        {"data": {"id": "", "created_utc": None}},  # skipped
    ]}}
    posts, after = parse_listing("worldnews", payload)
    assert after == "t3_xyz" and len(posts) == 2
    hot = posts[0]
    assert hot.id == "reddit:r/worldnews/a1" and hot.platform == "reddit"
    assert hot.place == "Hormuz" and hot.lat is not None
    assert hot.views == 2800 and "tanker" in hot.keywords and "attack" in hot.keywords
    assert posts[1].lat is None  # kept but not correlatable


def test_bluesky_parser():
    from fusion.ingest_bluesky import parse_search
    payload = {"cursor": "next", "posts": [
        {"uri": "at://did:plc:abc/app.bsky.feed.post/rkey1",
         "author": {"handle": "osint.bsky.social"},
         "record": {"text": "Missile strike reported over the Strait of Hormuz",
                    "createdAt": "2026-08-18T10:00:00.000Z"},
         "likeCount": 40, "repostCount": 10, "indexedAt": "2026-08-18T10:01:00Z",
         "embed": {"$type": "app.bsky.embed.images#view"}},
        {"uri": "", "record": {}},  # skipped
    ]}
    posts, cursor = parse_search(payload)
    assert cursor == "next" and len(posts) == 1
    p = posts[0]
    assert p.id == "bsky:osint.bsky.social/rkey1" and p.platform == "bluesky"
    assert p.url == "https://bsky.app/profile/osint.bsky.social/post/rkey1"
    assert p.place == "Strait Of Hormuz" and p.views == 60 and p.has_media


def test_mastodon_parser():
    from fusion.ingest_mastodon import parse_statuses
    items = [{"id": "123", "created_at": "2026-08-18T10:00:00.000Z",
              "content": "<p>Drone attack near <b>Bandar Abbas</b></p>",
              "spoiler_text": "", "url": "https://mastodon.social/@alice/123",
              "account": {"acct": "alice"}, "favourites_count": 7,
              "reblogs_count": 3, "media_attachments": []}]
    posts = parse_statuses("mastodon.social", items)
    assert len(posts) == 1
    p = posts[0]
    assert p.id == "mastodon:alice/123" and p.platform == "mastodon"
    assert p.place == "Bandar Abbas" and p.views == 10
    assert "drone" in p.keywords and "attack" in p.keywords


def test_enabled_platforms():
    old = os.getenv("SOCIAL_PLATFORMS")
    try:
        os.environ.pop("SOCIAL_PLATFORMS", None)
        assert enabled_platforms() == ["telegram", "reddit", "bluesky", "mastodon"]
        assert enabled_platforms("reddit, bluesky") == ["reddit", "bluesky"]
        assert enabled_platforms("reddit,nope") == ["reddit"]  # unknown ignored
    finally:
        if old is None:
            os.environ.pop("SOCIAL_PLATFORMS", None)
        else:
            os.environ["SOCIAL_PLATFORMS"] = old


def test_pipeline_refresh_social_multi_platform():
    """Pipeline fans out across platforms, keeps geolocated ≤6h, reports aggregate + alias."""
    import sys
    import threading
    import types
    import fusion.pipeline as P
    from fusion.ingest_social import SocialPost

    now_iso = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    tg = SocialPost(id="tg:c/9", platform="telegram", ts=now_iso, channel="c",
                    text="tanker near Hormuz", url="https://t.me/c/9",
                    lat=26.57, lon=56.25, place="Hormuz", keywords=["tanker"], views=100)
    rd = SocialPost(id="reddit:r/worldnews/z9", platform="reddit", ts=now_iso,
                    channel="r/worldnews", text="strike near Hormuz",
                    url="https://www.reddit.com/r/worldnews/comments/z9/",
                    lat=26.57, lon=56.25, place="Hormuz", keywords=["strike"], views=50)
    stray = SocialPost(id="bsky:a/z8", platform="bluesky", ts=now_iso, channel="a",
                       text="hello world", url="https://bsky.app/profile/a/post/z8",
                       lat=None, lon=None, place=None, keywords=[], views=1)
    calls: list = []

    def fake_mod(posts, fail=False):
        m = types.ModuleType("fake_adapter")

        def fetch_latest(target):
            calls.append(target)
            if fail:
                raise RuntimeError("down")
            return list(posts)
        m.fetch_latest = fetch_latest
        return m

    fakes = {
        "fusion.ingest_telegram": fake_mod([tg]),
        "fusion.ingest_reddit": fake_mod([rd]),
        "fusion.ingest_bluesky": fake_mod([stray]),
        "fusion.ingest_mastodon": fake_mod([]),
    }
    saved = {k: sys.modules.get(k) for k in fakes}
    sys.modules.update(fakes)
    st = P.FusionState.__new__(P.FusionState)  # bypass make_store (no Neo4j probe)
    st.events, st.social, st.tracks = [], [], []
    st.event_ids, st.conflict_event_count = [], 0
    st.source_status = {"social": {"state": "starting"}, "telegram": {"state": "starting"},
                        "fusion": {"state": "starting"}}
    st.lock = threading.Lock()
    try:
        st.refresh_social(targets={"telegram": ["c"], "reddit": ["worldnews"],
                                   "bluesky": ["hormuz"], "mastodon": ["mastodon.social"]})
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    ids = {e.id for e in st.social}
    assert ids == {"tg:c/9", "reddit:r/worldnews/z9"}, ids  # stray not geolocated
    assert st.source_status["social"]["state"] == "ready", st.source_status["social"]
    assert st.source_status["social"]["count"] == 2
    assert st.source_status["telegram"]["count"] == 1  # backwards-compat alias
    assert st.source_status["social:telegram"]["count"] == 1
    assert st.source_status["social:reddit"]["count"] == 1


def test_pipeline_refresh_social_partial_on_failure():
    """One down platform -> aggregate partial, others still ingested."""
    import sys
    import threading
    import types
    import fusion.pipeline as P
    from fusion.ingest_social import SocialPost

    now_iso = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    tg = SocialPost(id="tg:c/9", platform="telegram", ts=now_iso, channel="c",
                    text="tanker near Hormuz", url="https://t.me/c/9",
                    lat=26.57, lon=56.25, place="Hormuz", keywords=["tanker"], views=100)

    def fake_mod(posts, fail=False):
        m = types.ModuleType("fake_adapter")

        def fetch_latest(target):
            if fail:
                raise RuntimeError("down")
            return list(posts)
        m.fetch_latest = fetch_latest
        return m

    fakes = {
        "fusion.ingest_telegram": fake_mod([tg]),
        "fusion.ingest_reddit": fake_mod([], fail=True),
        "fusion.ingest_bluesky": fake_mod([]),
        "fusion.ingest_mastodon": fake_mod([]),
    }
    saved = {k: sys.modules.get(k) for k in fakes}
    sys.modules.update(fakes)
    st = P.FusionState.__new__(P.FusionState)
    st.events, st.social, st.tracks = [], [], []
    st.event_ids, st.conflict_event_count = [], 0
    st.source_status = {"social": {"state": "starting"}, "telegram": {"state": "starting"},
                        "fusion": {"state": "starting"}}
    st.lock = threading.Lock()
    try:
        st.refresh_social(platforms=["telegram", "reddit"],
                          targets={"telegram": ["c"], "reddit": ["worldnews"]})
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    assert {e.id for e in st.social} == {"tg:c/9"}
    assert st.source_status["social"]["state"] == "partial", st.source_status["social"]


def test_replay_timeline_counts_social_all_platforms():
    """Replay scrubber counts every platform's posts as social (not GDELT events)."""
    from fusion.replay import ReplayState
    from fusion.ingest_gdelt import OsintEvent
    from fusion.ingest_social import social_to_event, SocialPost

    import threading
    rs = ReplayState.__new__(ReplayState)
    rs.t_min = 1787011200.0
    rs.t_max = 1787011200.0 + 86400 - 1  # single-day window (multi-day scenarios span wider)
    rs.lock = threading.Lock()
    rs.loaded = True
    gdelt = OsintEvent(id="gdelt:1", ts="2026-08-18T00:05:00+00:00", lat=26.5, lon=56.2,
                       place="Hormuz", country="IR", geo_type=4, actor1=None, actor2=None,
                       actor1_cc=None, actor2_cc=None, event_code="181", root_code="18",
                       root_label="Assault", quad_class=4, goldstein=-7.0, tone=-5.0,
                       num_mentions=5, num_sources=1, url="https://cnn.com/a",
                       source_domain="cnn.com", is_conflict=True, themes=[], persons=[], orgs=[])
    soc = social_to_event(SocialPost(id="reddit:r/worldnews/a1", platform="reddit",
                                     channel="r/worldnews", text="strike near Hormuz",
                                     url="https://www.reddit.com/x", ts="2026-08-18T00:06:00+00:00",
                                     lat=26.5, lon=56.2, place="Hormuz",
                                     keywords=["strike"], views=10))
    rs.events = [gdelt, soc]
    rs.tracks, rs.firms, rs.sar = {}, [], []
    tl = ReplayState.timeline(rs, step_min=15)
    b0 = tl["bins"][0]
    assert b0["events"] == 1 and b0["conflict"] == 1 and b0["social"] == 1, b0


if __name__ == "__main__":
    for name, fn in sorted({k: v for k, v in globals().items() if k.startswith("test_")}.items()):
        fn()
        print("ok", name)
    print("all social tests passed")
