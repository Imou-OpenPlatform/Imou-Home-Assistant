"""Shared account-level AI Insight workbench helpers and base entity."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyimouapi.ha_device import ImouHaDevice

from .const import CONF_API_URL_HZ, DOMAIN, PARAM_API_URL, imou_life_device_key
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .helpers import camera_channel_devices


def is_china_api_entry(config_entry: ImouConfigEntry) -> bool:
    host = str(config_entry.data.get(PARAM_API_URL) or "").strip().lower()
    return host == CONF_API_URL_HZ or host.endswith("lechange.cn")


def trial_notification_copy(config_entry: ImouConfigEntry) -> tuple[str, str]:
    """Return (title, body) for the AI Insight trial persistent notification."""
    if is_china_api_entry(config_entry):
        return (
            "智见试用",
            "智见为您的摄像头提供智能搜索与每日简报。"
            "套餐需在乐橙云端或渠道人工开通。\n\n"
            "有问题请添加17799860785官方微信联系",
        )
    return (
        "AI Insight trial",
        "AI Insight adds smart search and daily safety briefings "
        "for your cameras. Plans are opened manually in the Imou "
        "cloud console or through your dealer.\n\n"
        "If you have any questions, please add official "
        "WhatsApp:+86 19106884129 Contact",
    )


def device_option_label(device: ImouHaDevice) -> str:
    name = device.channel_name or device.device_name
    if device.channel_id not in (None, "0", 0):
        return f"{name} ({device.channel_id})"
    return name


def camera_select_options(
    devices: list[ImouHaDevice],
) -> tuple[list[str], dict[str, str]]:
    """Return select labels and label → registry key."""
    labels: list[str] = []
    label_to_key: dict[str, str] = {}
    for device in camera_channel_devices(devices):
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
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{config_entry.entry_id}${entity_type}"
        self._attr_translation_key = entity_type
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=config_entry.title or "Imou Life",
            manufacturer="Imou",
        )
