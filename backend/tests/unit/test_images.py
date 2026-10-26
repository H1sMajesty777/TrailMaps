import io

import pytest
from PIL import Image

from app.core.images import InvalidImageError, process_avatar


def _image_bytes(fmt: str, size=(300, 200), mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, (200, 30, 30)).save(buf, fmt)
    return buf.getvalue()


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_supported_formats_become_square_webp(fmt):
    out = process_avatar(_image_bytes(fmt), 128)
    result = Image.open(io.BytesIO(out))
    assert result.format == "WEBP"
    assert result.size == (128, 128)
    assert result.mode == "RGB"


def test_rgba_png_is_flattened_to_rgb():
    out = process_avatar(_image_bytes("PNG", mode="RGBA"), 64)
    assert Image.open(io.BytesIO(out)).mode == "RGB"


@pytest.mark.parametrize("payload", [b"", b"not an image", b"\x89PNG\r\n\x1a\n" + b"x" * 20])
def test_garbage_is_rejected(payload):
    with pytest.raises(InvalidImageError):
        process_avatar(payload, 64)


def test_unsupported_format_is_rejected():
    with pytest.raises(InvalidImageError, match="JPEG, PNG and WEBP"):
        process_avatar(_image_bytes("GIF"), 64)


def test_huge_dimensions_are_rejected_before_decoding():
    with pytest.raises(InvalidImageError, match="too large"):
        process_avatar(_image_bytes("PNG"), 64, max_pixels=100)
