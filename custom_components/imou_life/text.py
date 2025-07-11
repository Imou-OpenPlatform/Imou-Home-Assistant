import logging

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyimouapi.const import PARAM_STATE, PARAM_REF
from pyimouapi.exceptions import ImouException

from .const import DOMAIN, PARAM_OVERCHARGE_SWITCH, PARAM_COUNT_DOWN_SWITCH
from .entity import ImouEntity

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(
        hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    _LOGGER.info("ImouText.async_setup_entry")
    imou_coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for device in imou_coordinator.devices:
        for text_type, value in device.texts.items():
            text_entity = ImouText(imou_coordinator, entry, text_type, device)
            entities.append(text_entity)
    if len(entities) > 0:
        async_add_entities(entities)


class ImouText(ImouEntity, TextEntity):
    """imou Text."""

    @property
    def native_value(self):
        return self._device.texts[self._entity_type][PARAM_STATE]

    async def async_set_value(self, value: str) -> None:
        try:
            await self._coordinator.device_manager.async_set_text_value(
                self._device,
                self._entity_type,
                value,
            )
            self._device.texts[self._entity_type][PARAM_STATE] = value
        except ImouException as e:
            raise HomeAssistantError(e.message)  # noqa: B904

    @property
    def pattern(self):
        if PARAM_OVERCHARGE_SWITCH == self._entity_type:
            if self._device.texts[self._entity_type][PARAM_REF] == "128900":
                return "^(?:[5-9]|[1-9][0-9]{1,2}|[1-2][0-9]{3}|3[0-5][0-9]{2}|36[0-7][0-9]|3680)$"
            return "^(?:[5-9]|[1-9][0-9]{1,2}|[1-9][0-9]{3}|1[0-9]{3}|2[0-4][0-9]{2}|2500)$"
        elif PARAM_COUNT_DOWN_SWITCH == self._entity_type:
            return "^(?:0|[1-9]|[1-9][0-9]{1,2}|1[0-3][0-9]{2}|14[0-3][0-9]|1440)$"
        return None
