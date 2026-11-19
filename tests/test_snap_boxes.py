"""Detection boxes use the picture's top-left as the origin."""

from __future__ import annotations

from io import BytesIO

from custom_components.imou_life.snap_boxes import annotate_snap_jpeg, detection_box
from PIL import Image
from pyimouapi.snap_detect import SnapDetectTarget


def _jpeg(width: int = 100, height: int = 80) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def test_box_is_top_left_plus_size() -> None:
    """x, y is the top-left corner; width and height extend right and down."""
    assert detection_box({"x": 10, "y": 20, "width": 30, "height": 15}, 100, 80) == (
        10,
        20,
        40,
        35,
    )


def test_annotate_paints_the_box_edge() -> None:
    """A returned region is drawn on the snapshot."""
    target = SnapDetectTarget(
        "human", "0.9", {"x": 10, "y": 20, "width": 30, "height": 15}
    )
    annotated = annotate_snap_jpeg(_jpeg(), (target,))
    assert annotated is not None
    image = Image.open(BytesIO(annotated))
    edge = [image.getpixel((x, 20)) for x in range(10, 40)]
    assert any(red > green + 30 and red > blue + 30 for red, green, blue in edge)
    outside = image.getpixel((90, 70))
    assert all(channel > 240 for channel in outside)


def test_missing_origin_is_not_drawn() -> None:
    """A region without y is not guessed into a box."""
    target = SnapDetectTarget("human", None, {"x": 10, "width": 20, "height": 30})
    assert annotate_snap_jpeg(_jpeg(), (target,)) is None
