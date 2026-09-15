import hashlib

import numpy as np

_POPCOUNT_TABLE = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def hamming_distance(hex_a, hex_b):
    return bin(int(hex_a, 16) ^ int(hex_b, 16)).count("1")


def _popcount_uint64(arr):
    byte_view = arr.view(np.uint8).reshape(arr.shape + (8,))
    return _POPCOUNT_TABLE[byte_view].sum(axis=-1)


class _UnionFind:
    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a, b):
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[root_a] = root_b


def group_photos(records, threshold=8, batch_size=500):
    if not records:
        return []

    paths = [r["path"] for r in records]
    hashes = np.array([int(r["phash"], 16) for r in records], dtype=np.uint64)
    n = len(hashes)

    uf = _UnionFind(paths)
    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        batch = hashes[start:end]
        distances = _popcount_uint64(np.bitwise_xor(batch[:, None], hashes[None, :]))
        rows, cols = np.where(distances <= threshold)
        for row, col in zip(rows, cols):
            i, j = start + int(row), int(col)
            if i < j:
                uf.union(paths[i], paths[j])

    clusters = {}
    for record in records:
        root = uf.find(record["path"])
        clusters.setdefault(root, []).append(record)

    return [group for group in clusters.values() if len(group) > 1]


def rank_group(group):
    return sorted(
        group,
        key=lambda r: (-(r["width"] * r["height"]), -r["sharpness"], -r["file_size"], r["path"]),
    )


def group_id(group):
    paths = sorted(r["path"] for r in group)
    return hashlib.sha256("|".join(paths).encode()).hexdigest()[:16]
