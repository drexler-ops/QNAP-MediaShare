import random
from datetime import datetime, timedelta

from app.dedup.grouping import BKTree, Rec, compute_clusters
from app.scanner.livephoto import Candidate, match_pairs
from app.settings_store import DEFAULTS


def flip(h: str, bits: int, seed: int = 0) -> str:
    rnd = random.Random(seed)
    v = int(h, 16)
    for b in rnd.sample(range(64), bits):
        v ^= 1 << b
    return f"{v:016x}"


BASE = "a5a5a5a5f0f0f0f0"
T0 = datetime(2024, 7, 4, 12, 0, 0)


def test_bktree_matches_bruteforce():
    rnd = random.Random(1)
    hashes = [rnd.getrandbits(64) for _ in range(500)]
    tree = BKTree()
    for i, h in enumerate(hashes):
        tree.add(h, i)
    q = hashes[17] ^ 0b1011
    got = {k for k, _ in tree.query(q, 10)}
    want = {i for i, h in enumerate(hashes) if (h ^ q).bit_count() <= 10}
    assert got == want


def test_clusters_by_type():
    recs = [
        # exact copies (same sha) + a re-encoded JPG export + an unrelated photo
        Rec(1, "image", sha256="aaa", phash=BASE, width=4032, height=3024, exif_score=9, taken_at=T0),
        Rec(2, "image", sha256="aaa", phash=BASE, width=4032, height=3024, exif_score=9, taken_at=T0),
        Rec(3, "image", sha256="bbb", phash=flip(BASE, 3), width=1600, height=1200, taken_at=None),
        Rec(4, "image", sha256="ccc", phash="0123456789abcdef", width=100, height=100, taken_at=T0 + timedelta(days=1)),
        # a burst: 3 different shots seconds apart, moderately similar
        Rec(10, "image", sha256="b1", phash="ffff0000ffff0000", width=4032, height=3024, taken_at=T0 + timedelta(hours=1)),
        Rec(11, "image", sha256="b2", phash=flip("ffff0000ffff0000", 9, 1), width=4032, height=3024,
            taken_at=T0 + timedelta(hours=1, seconds=1)),
        Rec(12, "image", sha256="b3", phash=flip("ffff0000ffff0000", 10, 2), width=4032, height=3024,
            taken_at=T0 + timedelta(hours=1, seconds=3)),
        # videos: same clip re-encoded
        Rec(20, "video", sha256="v1", duration=10.0, video_sig=[BASE] * 10),
        Rec(21, "video", sha256="v2", duration=10.4, video_sig=[flip(BASE, 2, i) for i in range(10)]),
        Rec(22, "video", sha256="v3", duration=10.2, video_sig=["0f0f0f0f0f0f0f0f"] * 10),
    ]
    clusters = compute_clusters(recs, DEFAULTS)
    by_type = {}
    for c in clusters:
        by_type.setdefault(c.type, []).append(c)

    strong_photo = by_type["reencode"]
    assert len(strong_photo) == 1 and strong_photo[0].item_ids == [1, 2, 3]
    assert strong_photo[0].match_types == {1: "exact", 2: "exact", 3: "reencode"}
    assert [c.item_ids for c in by_type["burst"]] == [[10, 11, 12]]
    assert [c.item_ids for c in by_type["video"]] == [[20, 21]]
    assert "exact" not in by_type


def test_pure_exact_group_and_aspect_guard():
    recs = [
        Rec(1, "image", sha256="x", phash=BASE, width=100, height=100),
        Rec(2, "image", sha256="x", phash=BASE, width=100, height=100),
        # same pHash but very different aspect ratio (e.g. a crop): not a re-encode
        Rec(3, "image", sha256="y", phash=BASE, width=300, height=100),
    ]
    clusters = compute_clusters(recs, DEFAULTS)
    assert [(c.type, c.item_ids) for c in clusters] == [("exact", [1, 2])]


def test_nearly_identical_burst_shots_are_not_reencodes():
    """Two shots 1s apart with almost the same pHash must NOT be a (default-delete) re-encode group."""
    recs = [
        Rec(1, "image", sha256="a", phash=BASE, width=10, height=10, taken_at=T0),
        Rec(2, "image", sha256="b", phash=flip(BASE, 2), width=10, height=10, taken_at=T0 + timedelta(seconds=1)),
        # same capture time shifted by a timezone (export tools sometimes do this): still a re-encode
        Rec(3, "image", sha256="c", phash=flip(BASE, 3, 5), width=10, height=10, taken_at=T0 + timedelta(hours=-5)),
    ]
    clusters = compute_clusters(recs, DEFAULTS)
    assert sorted((c.type, c.item_ids) for c in clusters) == [("burst", [1, 2]), ("reencode", [1, 3])]


def test_burst_uses_one_copy_of_each_strong_cluster():
    recs = [
        Rec(1, "image", sha256="s", phash=BASE, width=10, height=10, exif_score=9, taken_at=T0),
        Rec(2, "image", sha256="s", phash=BASE, width=10, height=10, exif_score=0, taken_at=T0),
        Rec(3, "image", sha256="t", phash=flip(BASE, 10), width=10, height=10, taken_at=T0 + timedelta(seconds=2)),
    ]
    clusters = compute_clusters(recs, DEFAULTS)
    burst = [c for c in clusters if c.type == "burst"]
    assert [c.item_ids for c in burst] == [[1, 3]]


def test_live_photo_pairing():
    stills = [
        Candidate(1, "Jamie/2024/IMG_0001.HEIC", "CID-1"),
        Candidate(2, "Jamie/2024/IMG_0002.HEIC", None),
        Candidate(3, "Sam/IMG_0003.HEIC", "CID-3"),
    ]
    videos = [
        Candidate(10, "Jamie/2024/IMG_0001.MOV", "CID-1", 2.8),  # content identifier match
        Candidate(11, "Jamie/2024/IMG_0002.MOV", None, 3.0),  # stem fallback
        Candidate(12, "Sam/other/IMG_0003.MOV", "CID-3", 2.5),  # different folder: not paired
        Candidate(13, "Jamie/2024/IMG_0002.mov", None, 45.0),  # long video, already used anyway
    ]
    assert sorted(match_pairs(stills, videos)) == [(1, 10), (2, 11)]


def test_stem_fallback_ignores_long_videos():
    stills = [Candidate(1, "a/IMG_9.JPG", None)]
    videos = [Candidate(2, "a/IMG_9.MOV", None, 60.0)]
    assert match_pairs(stills, videos) == []
