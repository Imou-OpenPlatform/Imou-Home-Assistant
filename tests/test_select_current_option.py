"""Select entity keeps current_option inside options (avoids UI “unknown”)."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.imou_life.const import (
    PARAM_COLLECTION_POINT,
    PARAM_NIGHT_VISION_MODE,
)
from custom_components.imou_life.const import imou_life_device_key
from custom_components.imou_life.select import ImouSelect, SELECT_TYPES
from pyimouapi.const import PARAM_COLLECTION_POINT_PROMPT, PARAM_CURRENT_OPTION, PARAM_OPTIONS
from pyimouapi.ha_device import ImouHaDevice


def _select_entity(selects: dict, entity_type: str) -> ImouSelect:
    device = MagicMock(spec=ImouHaDevice)
    device.selects = selects
    device.device_id = "dev"
    device.channel_id = "0"
    key = imou_life_device_key(device)
    coordinator = MagicMock()
    coordinator.devices_by_key = {key: device}
    description = next(d for d in SELECT_TYPES if d.key == entity_type)
    entity = ImouSelect(coordinator, MagicMock(), description, device)
    entity._entity_type = entity_type
    entity._device_key = key
    return entity


def test_night_vision_maps_numeric_current_to_friendly() -> None:
    entity = _select_entity(
        {
            PARAM_NIGHT_VISION_MODE: {
                PARAM_OPTIONS: ["intelligent", "fullcolor", "infrared", "off"],
                PARAM_CURRENT_OPTION: "0",
            }
        },
        PARAM_NIGHT_VISION_MODE,
    )
    assert entity.current_option == "intelligent"


def test_collection_point_falls_back_to_prompt() -> None:
    entity = _select_entity(
        {
            PARAM_COLLECTION_POINT: {
                PARAM_OPTIONS: [PARAM_COLLECTION_POINT_PROMPT],
                PARAM_CURRENT_OPTION: "",
            }
        },
        PARAM_COLLECTION_POINT,
    )
    assert entity.current_option == PARAM_COLLECTION_POINT_PROMPT
