"""Avatar image processing (Pillow only). Re-encoding strips EXIF and any payload tricks."""

import io

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_PIXELS = 25_000_000


class InvalidImageError(ValueError):
    pass


def process_avatar(data: bytes, size: int, max_pixels: int = MAX_PIXELS) -> bytes:
    """Validate, orient, square-crop, resize and re-encode an uploaded image as WEBP."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format not in ALLOWED_FORMATS:
                raise InvalidImageError("Only JPEG, PNG and WEBP images are supported")
            if img.width * img.height > max_pixels:
                raise InvalidImageError("Image dimensions are too large")
            img.load()
            oriented = ImageOps.exif_transpose(img)
            rgb = oriented.convert("RGB")
        fitted = ImageOps.fit(rgb, (size, size), method=Image.Resampling.LANCZOS)
        out = io.BytesIO()
        fitted.save(out, format="WEBP", quality=85)
        return out.getvalue()
    except InvalidImageError:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        ValueError,
        SyntaxError,
    ) as exc:
        raise InvalidImageError("Invalid or corrupted image") from exc
