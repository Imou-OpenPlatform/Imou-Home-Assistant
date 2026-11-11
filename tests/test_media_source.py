"""Tests for Imou media source identifiers."""

from custom_components.imou_life.media_source import _split


def test_split_identifier() -> None:
    assert _split(None) == []
    assert _split("entry:abc|cam:SN1:0|cloud") == [
        "entry:abc",
        "cam:SN1:0",
        "cloud",
    ]
