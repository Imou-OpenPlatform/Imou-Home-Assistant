"""Shared account-level AI Insight workbench helpers and base entity."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import translation
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyimouapi.ha_device import ImouHaDevice

from .const import CONF_API_URL_HZ, DOMAIN, PARAM_API_URL, imou_life_device_key
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .helpers import camera_channel_devices, resolve_ui_language, selector_option_label


def is_china_api_entry(config_entry: ImouConfigEntry) -> bool:
    host = str(config_entry.data.get(PARAM_API_URL) or "").strip().lower()
    return host == CONF_API_URL_HZ or host.endswith("lechange.cn")


async def async_trial_dialog_copy(
    hass: HomeAssistant, config_entry: ImouConfigEntry
) -> tuple[str, str]:
    """Return (title, body) for the AI Insight trial dialog."""
    language = resolve_ui_language(hass.config.language)
    await translation.async_get_translations(
        hass, language, "selector", integrations={DOMAIN}
    )
    region = "china" if is_china_api_entry(config_entry) else "intl"
    title = selector_option_label(
        hass,
        language,
        "insight_trial_dialog",
        f"{region}_title",
        "AI Insight trial",
    )
    message = selector_option_label(
        hass,
        language,
        "insight_trial_dialog",
        f"{region}_message",
        "",
    )
    return title, message


def device_option_label(device: ImouHaDevice) -> str:
    name = device.channel_name or device.device_name
    if device.channel_id not in (None, "0", 0):
        return f"{name} ({device.channel_id})"
    return name


def ai_insight_channel_devices(
    devices: list[ImouHaDevice],
) -> list[ImouHaDevice]:
    """Camera channels that can use insight briefing or search."""
    return [
        device
        for device in camera_channel_devices(devices)
        if device.insight_features_active
    ]


def camera_select_options(
    devices: list[ImouHaDevice],
) -> tuple[list[str], dict[str, str]]:
    """Return select labels and label → registry key."""
    labels: list[str] = []
    label_to_key: dict[str, str] = {}
    channel_devices = ai_insight_channel_devices(devices)
    for device in channel_devices:
        label = device_option_label(device)
        base = label
        suffix = 2
        while label in label_to_key and label_to_key[label] != imou_life_device_key(
            device
        ):
            label = f"{base} #{suffix}"
            suffix += 1
        labels.append(label)
        label_to_key[label] = imou_life_device_key(device)
    return labels, label_to_key


class ImouAccountEntity(CoordinatorEntity[ImouDataUpdateCoordinator]):
    """Base for entities tied to the integration account, not one device."""

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
        entity_type: str,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._entity_type = entity_type
        self._attr_has_entity_name = False
        self._attr_unique_id = f"{config_entry.entry_id}${entity_type}"
        self._attr_translation_key = entity_type
