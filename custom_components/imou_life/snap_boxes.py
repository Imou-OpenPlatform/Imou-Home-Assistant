"""Draw deviceSnapDetect boxes onto the snapshot."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from PIL import Image, ImageDraw
from pyimouapi.snap_detect import SnapDetectTarget

from .pic_thumbnail import public_media_url

_LOGGER = logging.getLogger(__name__)

_SNAP_SUBDIR = Path("imou_life") / "snap"
_MAX_SNAPS = 30
_BOX_COLORS = {
    "human": (255, 64, 64),
    "vehicle": (64, 160, 255),
    "animal": (80, 200, 120),
    "box": (255, 196, 0),
}
_LABELS = {
    "human": "person",
    "vehicle": "vehicle",
    "animal": "pet",
    "box": "object",
}


def detection_box(
    region: dict, img_width: int, img_height: int
) -> tuple[int, int, int, int] | None:
    """Return a pixel rectangle from a top-left ``x, y, width, height`` region.

    The origin is the top-left of the picture. Values in the 0-1 range are
    fractions of the picture; larger values are already pixels.
    """
    x = _number(region, "x")
    y = _number(region, "y")
    width = _number(region, "width")
    height = _number(region, "height")
    if x is None or y is None or width is None or height is None:
        return None
    if width <= 0 or height <= 0 or img_width < 2 or img_height < 2:
        return None
    if _is_fraction(x, y, width, height):
        x *= img_width
        y *= img_height
        width *= img_width
        height *= img_height
    left = max(0, min(round(x), img_width - 1))
    top = max(0, min(round(y), img_height - 1))
    right = max(left + 1, min(round(x + width), img_width - 1))
    bottom = max(top + 1, min(round(y + height), img_height - 1))
    return left, top, right, bottom


def annotate_snap_jpeg(
    jpeg: bytes,
    targets: tuple[SnapDetectTarget, ...] | list[SnapDetectTarget],
    names: dict[str, str] | None = None,
) -> bytes | None:
    """Return a jpeg with detection boxes, or None when nothing can be drawn."""
    try:
        image = Image.open(BytesIO(jpeg)).convert("RGB")
    except (OSError, ValueError):
        _LOGGER.debug("Snap detect picture is not an image", exc_info=True)
        return None
    draw = ImageDraw.Draw(image)
    img_width, img_height = image.size
    stroke = max(2, min(img_width, img_height) // 150)
    drawn = False
    for target in targets:
        box = detection_box(target.region, img_width, img_height)
        if box is None:
            continue
        color = _BOX_COLORS.get(target.target_type, (255, 220, 0))
        draw.rectangle(box, outline=color, width=stroke)
        label = (names or {}).get(target.target_type) or _LABELS.get(
            target.target_type, target.target_type
        )
        if target.confidence not in (None, ""):
            label = f"{label} {target.confidence}"
        text_y = box[1] - stroke - 4
        if text_y < 2:
            text_y = box[1] + stroke + 2
        draw.text((box[0] + stroke, text_y), label, fill=color)
        drawn = True
    if not drawn:
        return None
    output = BytesIO()
    image.save(output, format="JPEG", quality=85)
    return output.getvalue()


def write_annotated_snap(
    hass: HomeAssistant, device_id: str, channel_id: str | None, jpeg: bytes
) -> str | None:
    """Save an annotated jpeg under ``/local/`` and return that path."""
    www_dir = Path(hass.config.path("www"))
    snap_dir = www_dir / _SNAP_SUBDIR
    snap_dir.mkdir(parents=True, exist_ok=True)
    _prune_snaps(snap_dir)
    stamp = dt_util.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{_safe_token(device_id)}_{_safe_token(channel_id or '0')}_{stamp}.jpg"
    dest = (snap_dir / filename).resolve()
    if not dest.is_relative_to(snap_dir.resolve()):
        return None
    try:
        dest.write_bytes(jpeg)
    except OSError:
        _LOGGER.warning("Could not write snap detect picture %s", dest, exc_info=True)
        return None
    return f"/local/{_SNAP_SUBDIR.as_posix()}/{filename}"


def annotated_snap_url(hass: HomeAssistant, local_path: str) -> str:
    """Return a URL a phone can fetch for an annotated snapshot."""
    return public_media_url(hass, local_path)


def _is_fraction(x: float, y: float, width: float, height: float) -> bool:
    """True when every edge is a fraction of the picture, not a pixel count."""
    return max(x, y, width, height) <= 1


def _number(region: dict, key: str) -> float | None:
    if not isinstance(region, dict) or key not in region:
        return None
    raw = region[key]
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _safe_token(value: str) -> str:
    cleaned = "".join(char if char.isalnum() else "_" for char in value)
    return cleaned[:40] or "snap"


def _prune_snaps(snap_dir: Path) -> None:
    files = sorted(
        (path for path in snap_dir.glob("*.jpg") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
    )
    for path in files[:-_MAX_SNAPS]:
        path.unlink(missing_ok=True)
