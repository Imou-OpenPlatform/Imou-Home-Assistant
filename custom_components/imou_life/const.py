"""Constants."""

from homeassistant.const import Platform
from pyimouapi.ha_device import ImouHaDevice

# Internal constants
DOMAIN = "imou_life"
UPDATE_TIMEOUT = 300

# Listing the account costs a paged request plus one detail round trip per iot
# device, and every object it builds is dropped for devices we already know. It
# only exists to notice devices being added or removed, so it runs on its own
# slow clock instead of on every status poll.
DISCOVERY_INTERVAL = 600


def imou_life_device_key(device: ImouHaDevice) -> str:
    """Stable device registry / unique_id prefix (legacy semantics)."""
    key = imou_life_device_key_from_ids(
        device.device_id, device.channel_id, device.product_id
    )
    if key is None:
        return f"{device.device_id}_{device.channel_id or device.product_id}"
    return key


def imou_life_device_key_from_ids(
    device_id: str | None,
    channel_id: object | None,
    product_id: str | None,
) -> str | None:
    """Build the preferred device registry key from push / API ids.

    Same format as ``imou_life_device_key``: ``{device_id}_{channel_id|product_id}``.
    Uses ``is not None`` for channel so channel 0 is kept. Prefer
    ``imou_life_device_keys_from_ids`` when resolving against the registry —
    IoT pushes often include a monitor channel that is not the registry suffix.
    """
    keys = imou_life_device_keys_from_ids(device_id, channel_id, product_id)
    return keys[0] if keys else None


def imou_life_device_keys_from_ids(
    device_id: str | None,
    channel_id: object | None,
    product_id: str | None,
) -> list[str]:
    """Return candidate registry keys for an Imou device.

    Order:
    1. Channel-based key (IPC / multi-lens channel from the push)
    2. Product-based key (channel-less IoT accessory)
    3. Primary channel ``0`` when the push omitted channel_id — multi-lens
       devices are registered per channel (``did_0``, ``did_1``, …), not as
       ``did_pid``, so a missing channel still resolves to the main lens.
    """
    if not device_id:
        return []
    keys: list[str] = []
    if channel_id is not None:
        keys.append(f"{device_id}_{channel_id}")
    if product_id is not None and product_id != "":
        key = f"{device_id}_{product_id}"
        if key not in keys:
            keys.append(key)
    if channel_id is None:
        zero = f"{device_id}_0"
        if zero not in keys:
            keys.append(zero)
    return keys


# Configuration definitions
CONF_API_URL_SG = "openapi-sg.easy4ip.com"
CONF_API_URL_OR = "openapi-or.easy4ip.com"
CONF_API_URL_FK = "openapi-fk.easy4ip.com"
CONF_API_URL_HZ = "openapi.lechange.cn"

API_URL_REGIONS: dict[str, str] = {
    "sg": CONF_API_URL_SG,
    "eu": CONF_API_URL_OR,
    "na": CONF_API_URL_FK,
    "cn": CONF_API_URL_HZ,
}

_API_URL_REGION_BY_HOSTNAME = {
    hostname: region for region, hostname in API_URL_REGIONS.items()
}

DEFAULT_API_URL_REGION = "sg"


def api_url_from_region(region: str) -> str:
    """Map a config-flow region key to the stored API hostname."""
    return API_URL_REGIONS.get(region, CONF_API_URL_SG)


def api_url_region_from_value(value: str) -> str:
    """Return a region key for the login selector from stored or submitted value."""
    if value in API_URL_REGIONS:
        return value
    return _API_URL_REGION_BY_HOSTNAME.get(value, DEFAULT_API_URL_REGION)


CONF_HD = "HD"
CONF_SD = "SD"

CONF_HTTP = "http"
CONF_HTTPS = "https"


# parameters:
PARAM_API_URL = "api_url"
PARAM_APP_ID = "app_id"
PARAM_APP_SECRET = "app_secret"
PARAM_WEBHOOK_ID = "webhook_id"
PARAM_WEBHOOK_URL = "webhook_url"
PARAM_SELECTED_DEVICES = "selected_devices"
PARAM_ENABLE_EVENT_PUSH = "enable_event_push"
PARAM_EVENT_PUSH_TYPES = "event_push_types"
PARAM_NOTIFY_SERVICES = "notify_services"
PARAM_NOTIFY_ON_ALARM = "notify_on_alarm"
PARAM_LOCAL_EVENT_RECORD = "local_event_record"
PARAM_LOCAL_RECORD_PATH = "local_record_path"
PARAM_LOCAL_RECORD_DURATION = "local_record_duration"
DEFAULT_LOCAL_RECORD_DURATION = 60
# Always sync pushes to the Imou app as well as HA (setMessageCallback basePush).
BASE_PUSH_ALWAYS = "1"
PARAM_MOTION_DETECT = "motion_detect"
PARAM_MOTION = "motion"
MOTION_OFF_DELAY = 30
PARAM_STATUS = "status"
PARAM_STORAGE_USED = "storage_used"
PARAM_HEADER_DETECT = "header_detect"
PARAM_PET_DETECT = "pet_detect"
PARAM_FRAME_REVERSE = "frame_reverse"
PARAM_WIDE_DYNAMIC = "wide_dynamic"
PARAM_SMART_TRACK = "smart_track"
PARAM_PLAY_SOUND = "play_sound"
PARAM_LINKAGE_SIREN = "linkage_siren"
PARAM_LINKAGE_WHITE_LIGHT = "linkage_white_light"
PARAM_AB_ALARM_SOUND = "ab_alarm_sound"
PARAM_CLOSE_CAMERA = "close_camera"
PARAM_WHITE_LIGHT = "white_light"
PARAM_AUDIO_ENCODE_CONTROL = "audio_encode_control"
PARAM_LIGHT = "light"
PARAM_PLUG_SWITCH = "switch"
PARAM_NIGHT_VISION_MODE = "night_vision_mode"
PARAM_MODE = "mode"
PARAM_DEVICE_VOLUME = "device_volume"
PARAM_COLLECTION_POINT = "collection_point"
PARAM_CURRENT_OPTION = "current_option"
PARAM_OPTIONS = "options"
PARAM_RESTART_DEVICE = "restart_device"
PARAM_ENABLE_POLLING = "enable_polling"
PARAM_UPDATE_INTERVAL = "update_interval"
DEFAULT_UPDATE_INTERVAL = 300
PARAM_DOWNLOAD_SNAP_WAIT_TIME = "download_snap_wait_time"
PARAM_LIVE_RESOLUTION = "live_resolution"
PARAM_LIVE_PROTOCOL = "live_protocol"
PARAM_ROTATION_DURATION = "rotation_duration"
PARAM_PTZ = "ptz"
PARAM_COUNT_DOWN_SWITCH = "count_down_switch"
PARAM_OVERCHARGE_SWITCH = "overcharge_switch"

# event push — selector keys (hassfest: [a-z0-9-_]+) map to Imou API callbackFlag values
EVENT_PUSH_TYPE_ALARM = "alarm"
EVENT_PUSH_TYPE_DEVICE_STATUS = "device_status"
EVENT_PUSH_TYPE_IOT = "iot"
EVENT_PUSH_TYPE_NUMBERSTAT = "numberstat"
EVENT_PUSH_TYPE_FACE_ANALYSIS = "face_analysis"

EVENT_PUSH_TYPE_OPTIONS = (
    EVENT_PUSH_TYPE_ALARM,
    EVENT_PUSH_TYPE_DEVICE_STATUS,
    EVENT_PUSH_TYPE_IOT,
    EVENT_PUSH_TYPE_NUMBERSTAT,
    EVENT_PUSH_TYPE_FACE_ANALYSIS,
)

CALLBACK_FLAG_ALARM = "alarm"
CALLBACK_FLAG_DEVICE_STATUS = "deviceStatus"
CALLBACK_FLAG_IOT = "iot"
CALLBACK_FLAG_NUMBERSTAT = "numberstat"
CALLBACK_FLAG_FACE_ANALYSIS = "faceAnalysis"

EVENT_PUSH_TYPE_TO_CALLBACK_FLAG: dict[str, str] = {
    EVENT_PUSH_TYPE_ALARM: CALLBACK_FLAG_ALARM,
    EVENT_PUSH_TYPE_DEVICE_STATUS: CALLBACK_FLAG_DEVICE_STATUS,
    EVENT_PUSH_TYPE_IOT: CALLBACK_FLAG_IOT,
    EVENT_PUSH_TYPE_NUMBERSTAT: CALLBACK_FLAG_NUMBERSTAT,
    EVENT_PUSH_TYPE_FACE_ANALYSIS: CALLBACK_FLAG_FACE_ANALYSIS,
}

CALLBACK_FLAG_TO_EVENT_PUSH_TYPE: dict[str, str] = {
    v: k for k, v in EVENT_PUSH_TYPE_TO_CALLBACK_FLAG.items()
}

DEFAULT_EVENT_PUSH_TYPES = [
    EVENT_PUSH_TYPE_ALARM,
    EVENT_PUSH_TYPE_DEVICE_STATUS,
    EVENT_PUSH_TYPE_IOT,
]


def event_push_types_to_callback_flags(types: list[str]) -> list[str]:
    """Map config option values to Imou API callbackFlag strings."""
    flags: list[str] = []
    for value in types:
        if value in EVENT_PUSH_TYPE_TO_CALLBACK_FLAG:
            flags.append(EVENT_PUSH_TYPE_TO_CALLBACK_FLAG[value])
        else:
            # Legacy v1.2.10 options stored API flag strings directly
            flags.append(value)
    return flags


def callback_flags_to_event_push_types(flags: list[str]) -> list[str]:
    """Map stored values to hassfest-safe selector option keys."""
    types: list[str] = []
    for value in flags:
        if value in CALLBACK_FLAG_TO_EVENT_PUSH_TYPE:
            types.append(CALLBACK_FLAG_TO_EVENT_PUSH_TYPE[value])
        elif value in EVENT_PUSH_TYPE_TO_CALLBACK_FLAG:
            types.append(value)
        else:
            types.append(value)
    return types


EVENT_IMOU_EVENT = f"{DOMAIN}_event"
EVENT_IMOU_ALARM = f"{DOMAIN}_alarm"

# service
SERVICE_CONTROL_MOVE_PTZ = "control_move_ptz"


PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.ALARM_CONTROL_PANEL,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]
