from datetime import datetime
import io
import struct

import pytest
from PIL import Image, ImageDraw

import hashing


def _make_test_image(variant="stripes", size=(400, 300)):
    img = Image.new("RGB", size, color=(240, 240, 240))
    draw = ImageDraw.Draw(img)
    if variant == "stripes":
        for x in range(0, size[0], 10):
            color = (x % 256, (x * 2) % 256, (x * 3) % 256)
            draw.line([(x, 0), (x, size[1])], fill=color, width=10)
        draw.ellipse([50, 50, 150, 150], fill=(255, 0, 0))
    else:
        for y in range(0, size[1], 20):
            for x in range(0, size[0], 20):
                if (x // 20 + y // 20) % 2 == 0:
                    draw.rectangle([x, y, x + 20, y + 20], fill=(0, 0, 0))
    return img


def _make_image_with_exif_date(path, date_str="2020:05:15 10:30:00"):
    date_bytes = (date_str + "\x00").encode("ascii")
    tiff = b"II*\x00" + struct.pack("<I", 8)
    ifd0 = struct.pack("<H", 1)
    ifd0 += struct.pack("<HHII", 0x8769, 4, 1, 26)
    ifd0 += struct.pack("<I", 0)
    sub_ifd = struct.pack("<H", 1)
    sub_ifd += struct.pack("<HHII", 0x9003, 2, len(date_bytes), 44)
    sub_ifd += struct.pack("<I", 0)
    tiff_full = tiff + ifd0 + sub_ifd + date_bytes
    app1_payload = b"Exif\x00\x00" + tiff_full
    app1_segment = b"\xff\xe1" + struct.pack(">H", len(app1_payload) + 2) + app1_payload

    base = _make_test_image("stripes")
    buf_io = io.BytesIO()
    base.save(buf_io, format="jpeg")
    raw = buf_io.getvalue()
    assert raw[0:2] == b"\xff\xd8"
    with open(path, "wb") as f:
        f.write(raw[0:2] + app1_segment + raw[2:])


@pytest.fixture
def sample_images(tmp_path):
    original = _make_test_image("stripes")
    original_path = tmp_path / "original.jpg"
    original.save(original_path, quality=95)

    resized_path = tmp_path / "resized.jpg"
    original.resize((200, 150)).save(resized_path, quality=95)

    recompressed_path = tmp_path / "recompressed.jpg"
    original.save(recompressed_path, quality=20)

    unrelated_path = tmp_path / "unrelated.jpg"
    _make_test_image("checkerboard").save(unrelated_path, quality=95)

    return {
        "original": original_path,
        "resized": resized_path,
        "recompressed": recompressed_path,
        "unrelated": unrelated_path,
    }


def test_resized_and_recompressed_copies_hash_close_to_original(sample_images):
    original_hash = hashing.compute_phash(sample_images["original"])
    resized_hash = hashing.compute_phash(sample_images["resized"])
    recompressed_hash = hashing.compute_phash(sample_images["recompressed"])
    unrelated_hash = hashing.compute_phash(sample_images["unrelated"])

    assert (original_hash - resized_hash) <= 8
    assert (original_hash - recompressed_hash) <= 8
    assert (original_hash - unrelated_hash) > 8


def test_compute_sharpness_returns_non_negative_float(sample_images):
    score = hashing.compute_sharpness(sample_images["original"])

    assert isinstance(score, float)
    assert score >= 0


def test_get_metadata_reads_dimensions(sample_images):
    metadata = hashing.get_metadata(sample_images["original"], fallback_mtime=1700000000)

    assert metadata["width"] == 400
    assert metadata["height"] == 300


def test_get_metadata_falls_back_to_mtime_when_no_exif_date(sample_images):
    metadata = hashing.get_metadata(sample_images["original"], fallback_mtime=1700000000)

    assert metadata["date_taken"]
    datetime.fromisoformat(metadata["date_taken"])  # must not raise


def test_check_heic_support_does_not_raise_when_installed():
    hashing.check_heic_support()


def test_get_metadata_reads_real_exif_date_taken(tmp_path):
    path = tmp_path / "with_exif.jpg"
    _make_image_with_exif_date(path, "2020:05:15 10:30:00")

    metadata = hashing.get_metadata(path, fallback_mtime=1700000000)

    assert metadata["date_taken"] == "2020-05-15T10:30:00"
