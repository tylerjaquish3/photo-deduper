import grouping


def _record(path, phash_hex, width=100, height=100, file_size=1000, sharpness=10.0):
    return {
        "path": path,
        "phash": phash_hex,
        "width": width,
        "height": height,
        "file_size": file_size,
        "sharpness": sharpness,
        "date_taken": "2024-01-01",
    }


def test_hamming_distance():
    assert grouping.hamming_distance("ff00000000000000", "ff00000000000000") == 0
    assert grouping.hamming_distance("ff00000000000000", "0000000000000000") == 8
    assert grouping.hamming_distance("ffffffffffffffff", "0000000000000000") == 64


def test_group_photos_clusters_similar_hashes_and_ignores_singletons():
    records = [
        _record("/a.jpg", "0000000000000000"),
        _record("/b.jpg", "0000000000000001"),  # 1 bit different from a
        _record("/c.jpg", "ffffffffffffffff"),  # far from both
    ]

    groups = grouping.group_photos(records, threshold=8)

    assert len(groups) == 1
    assert {r["path"] for r in groups[0]} == {"/a.jpg", "/b.jpg"}


def test_group_photos_returns_empty_when_nothing_clusters():
    records = [
        _record("/a.jpg", "0000000000000000"),
        _record("/b.jpg", "ffffffffffffffff"),
    ]

    assert grouping.group_photos(records, threshold=8) == []


def test_group_photos_handles_empty_input():
    assert grouping.group_photos([], threshold=8) == []


def test_group_photos_merges_transitively_connected_hashes():
    # a<->b within threshold, b<->c within threshold, a<->c is not directly
    # within threshold but all three must end up in one group.
    records = [
        _record("/a.jpg", "0000000000000000"),
        _record("/b.jpg", "00000000000000ff"),  # 8 bits from a
        _record("/c.jpg", "000000000000ffff"),  # 8 bits from b, 16 from a
    ]

    groups = grouping.group_photos(records, threshold=8)

    assert len(groups) == 1
    assert {r["path"] for r in groups[0]} == {"/a.jpg", "/b.jpg", "/c.jpg"}


def test_rank_group_prefers_resolution_then_sharpness_then_file_size():
    group = [
        _record("/small.jpg", "0", width=100, height=100, sharpness=50, file_size=500),
        _record("/big.jpg", "0", width=800, height=600, sharpness=10, file_size=200000),
        _record("/sharper_same_res.jpg", "0", width=100, height=100, sharpness=90, file_size=600),
    ]

    ranked = grouping.rank_group(group)

    assert [r["path"] for r in ranked] == [
        "/big.jpg",
        "/sharper_same_res.jpg",
        "/small.jpg",
    ]


def test_rank_group_breaks_ties_deterministically_by_path():
    group = [
        _record("/z_photo.jpg", "0", width=100, height=100, sharpness=10, file_size=500),
        _record("/a_photo.jpg", "0", width=100, height=100, sharpness=10, file_size=500),
        _record("/m_photo.jpg", "0", width=100, height=100, sharpness=10, file_size=500),
    ]

    ranked = grouping.rank_group(group)

    assert [r["path"] for r in ranked] == ["/a_photo.jpg", "/m_photo.jpg", "/z_photo.jpg"]


def test_group_id_is_stable_regardless_of_order():
    group_a = [_record("/a.jpg", "0"), _record("/b.jpg", "0")]
    group_b = [_record("/b.jpg", "0"), _record("/a.jpg", "0")]

    assert grouping.group_id(group_a) == grouping.group_id(group_b)
