"""Avatar storage on the local volume (served by Nginx under /uploads/)."""

import asyncio
import logging
import os
import re
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import Settings
from app.core.errors import PayloadTooLargeError, UnprocessableError
from app.core.images import InvalidImageError, process_avatar

logger = logging.getLogger(__name__)

_FILENAME_RE = re.compile(r"^\d+-[0-9a-f]{32}\.webp$")


def avatars_dir(settings: Settings) -> Path:
    return Path(settings.upload_dir) / "avatars"


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


async def store_avatar(file: UploadFile, user_id: int, settings: Settings) -> str:
    """Validate, normalise and store an uploaded avatar. Returns the public URL path."""
    data = bytearray()
    while chunk := await file.read(64 * 1024):
        data.extend(chunk)
        if len(data) > settings.avatar_max_bytes:
            raise PayloadTooLargeError(
                f"Avatar must be at most {settings.avatar_max_bytes} bytes", code="file_too_large"
            )
    if not data:
        raise UnprocessableError("Empty file", code="invalid_image")
    try:
        processed = await asyncio.to_thread(process_avatar, bytes(data), settings.avatar_size_px)
    except InvalidImageError as exc:
        raise UnprocessableError(str(exc), code="invalid_image") from exc

    filename = f"{user_id}-{uuid.uuid4().hex}.webp"
    await asyncio.to_thread(_write_atomic, avatars_dir(settings) / filename, processed)
    return f"{settings.uploads_url_prefix.rstrip('/')}/avatars/{filename}"


def delete_avatar_file(avatar_url: str, settings: Settings) -> None:
    """Best-effort removal of a previous avatar; refuses anything outside the avatars dir."""
    if not avatar_url:
        return
    filename = avatar_url.rsplit("/", 1)[-1]
    if not _FILENAME_RE.fullmatch(filename):
        return
    try:
        (avatars_dir(settings) / filename).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete old avatar %s", filename)
