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


def test_fractional_region_uses_the_picture_size() -> None:
    """A 0-1 region is a fraction of the picture, not a one-pixel box."""
    assert detection_box(
        {"x": 0.53, "y": 0.466, "width": 0.101, "height": 0.359},
        1000,
        1000,
    ) == (530, 466, 631, 825)


def test_annotate_paints_a_fractional_person_box() -> None:
    """A fractional person region is drawn on the person, not the corner."""
    target = SnapDetectTarget(
        "human",
        95,
        {"x": 0.5, "y": 0.25, "width": 0.2, "height": 0.5},
    )
    annotated = annotate_snap_jpeg(_jpeg(200, 100), (target,))
    assert annotated is not None
    image = Image.open(BytesIO(annotated))
    edge = [image.getpixel((x, 25)) for x in range(100, 140)]
    assert any(red > green + 30 and red > blue + 30 for red, green, blue in edge)
    corner = image.getpixel((2, 2))
    assert all(channel > 240 for channel in corner)


def test_missing_origin_is_not_drawn() -> None:
    """A region without y is not guessed into a box."""
    target = SnapDetectTarget("human", None, {"x": 10, "width": 20, "height": 30})
    assert annotate_snap_jpeg(_jpeg(), (target,)) is None
