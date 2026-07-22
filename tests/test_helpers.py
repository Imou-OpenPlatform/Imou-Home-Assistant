"""Tests for Imou Life shared helpers and device key helpers."""

from custom_components.imou_life.const import imou_life_device_key_from_ids


def test_device_key_from_ids_prefers_channel_id() -> None:
    assert imou_life_device_key_from_ids("SN1", "0", "pid") == "SN1_0"
    assert imou_life_device_key_from_ids("SN1", 0, "pid") == "SN1_0"


def test_device_key_from_ids_uses_product_when_no_channel() -> None:
    assert imou_life_device_key_from_ids("SN1", None, "pidX") == "SN1_pidX"


def test_device_key_from_ids_incomplete_returns_none() -> None:
    assert imou_life_device_key_from_ids(None, "0", "pid") is None
    assert imou_life_device_key_from_ids("SN1", None, None) is None
