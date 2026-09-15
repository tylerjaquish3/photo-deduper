from datetime import datetime

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
