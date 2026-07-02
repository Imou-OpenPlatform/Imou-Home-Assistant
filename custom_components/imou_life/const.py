"""Constants."""

from pyimouapi.ha_device import ImouHaDevice

# Internal constants
DOMAIN = "imou_life"
UPDATE_TIMEOUT = 300


def imou_life_device_key(device: ImouHaDevice) -> str:
    """Stable device registry / unique_id prefix (legacy semantics)."""
    return f"{device.device_id}_{device.channel_id or device.product_id}"


# Configuration definitions
CONF_API_URL_SG = "openapi-sg.easy4ip.com"
CONF_API_URL_OR = "openapi-or.easy4ip.com"
CONF_API_URL_FK = "openapi-fk.easy4ip.com"
CONF_API_URL_HZ = "openapi.lechange.cn"

API_URL_OPTIONS = (
    CONF_API_URL_SG,
    CONF_API_URL_OR,
    CONF_API_URL_FK,
    CONF_API_URL_HZ,
)

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
PARAM_BASE_PUSH = "base_push"
PARAM_NOTIFY_SERVICES = "notify_services"
PARAM_MOTION_DETECT = "motion_detect"
PARAM_STATUS = "status"
PARAM_STORAGE_USED = "storage_used"
PARAM_HEADER_DETECT = "header_detect"
PARAM_CURRENT_OPTION = "current_option"
PARAM_OPTIONS = "options"
PARAM_RESTART_DEVICE = "restart_device"
PARAM_UPDATE_INTERVAL = "update_interval"
PARAM_DOWNLOAD_SNAP_WAIT_TIME = "download_snap_wait_time"
PARAM_LIVE_RESOLUTION = "live_resolution"
PARAM_LIVE_PROTOCOL = "live_protocol"
PARAM_ROTATION_DURATION = "rotation_duration"
PARAM_ENTITY_ID = "entity_id"
PARAM_PTZ = "ptz"
PARAM_OPTION = "option"
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
DEFAULT_BASE_PUSH = "2"

# service
SERVICE_RESTART_DEVICE = "restart_device"
SERVICE_CONTROL_MOVE_PTZ = "control_move_ptz"
SERVICE_TURN_ON = "turn_on"
SERVICE_TURN_OFF = "turn_off"
SERVICE_SELECT = "select"


PLATFORMS = ["select", "sensor", "switch", "camera", "button", "binary_sensor", "text"]
