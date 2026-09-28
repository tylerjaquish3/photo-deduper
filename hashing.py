from datetime import datetime

import cv2
import imagehash
import numpy as np
from PIL import ExifTags, Image

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pillow_heif = None


class MissingHeicSupportError(RuntimeError):
    pass


def check_heic_support():
    if pillow_heif is None:
        raise MissingHeicSupportError(
            "HEIC support is not installed. Run: pip install pillow-heif"
        )


def compute_phash(path):
    with Image.open(path) as img:
        return imagehash.phash(img)


def compute_sharpness(path):
    with Image.open(path) as img:
        gray = np.array(img.convert("L"))
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


_DATE_TAG_ID = next(k for k, v in ExifTags.TAGS.items() if v == "DateTimeOriginal")
_MAKE_TAG_ID = next(k for k, v in ExifTags.TAGS.items() if v == "Make")
_MODEL_TAG_ID = next(k for k, v in ExifTags.TAGS.items() if v == "Model")

# Camera photos carry a Make/Model EXIF tag; OS screenshot tools never write
# one and almost always save as PNG, so that combination is a reliable
# content-based signal without needing image classification.
_SCREENSHOT_FORMATS = {"PNG", "BMP"}


def _detect_screenshot(img_format, exif):
    has_camera_exif = _MAKE_TAG_ID in exif or _MODEL_TAG_ID in exif
    return img_format in _SCREENSHOT_FORMATS and not has_camera_exif


def is_screenshot(path):
    with Image.open(path) as img:
        return _detect_screenshot(img.format, img.getexif())


def get_metadata(path, fallback_mtime):
    with Image.open(path) as img:
        width, height = img.size
        img_format = img.format
        date_taken = None
        exif = img.getexif()
        exif_ifd = exif.get_ifd(0x8769)
        if _DATE_TAG_ID in exif_ifd:
            try:
                date_taken = datetime.strptime(
                    str(exif_ifd[_DATE_TAG_ID]), "%Y:%m:%d %H:%M:%S"
                ).isoformat()
            except ValueError:
                date_taken = None

    if date_taken is None:
        date_taken = datetime.fromtimestamp(fallback_mtime).isoformat()

    return {
        "width": width,
        "height": height,
        "date_taken": date_taken,
        "is_screenshot": _detect_screenshot(img_format, exif),
    }
