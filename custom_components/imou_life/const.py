"""Constants."""

from pyimouapi.ha_device import ImouHaDevice

# Internal constants
DOMAIN = "imou_life"


def imou_life_device_key(device: ImouHaDevice) -> str:
    """Return stable device key for registry identifiers and unique_id prefix."""
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
PARAM_MOTION_DETECT = "motion_detect"
PARAM_STATUS = "status"
PARAM_STORAGE_USED = "storage_used"
PARAM_HEADER_DETECT = "header_detect"
PARAM_CURRENT_OPTION = "current_option"
PARAM_OPTIONS = "options"
PARAM_RESTART_DEVICE = "restart_device"
PARAM_UPDATE_INTERVAL = "update_interval"
DEFAULT_UPDATE_INTERVAL = 120
PARAM_DOWNLOAD_SNAP_WAIT_TIME = "download_snap_wait_time"
PARAM_LIVE_RESOLUTION = "live_resolution"
PARAM_LIVE_PROTOCOL = "live_protocol"
PARAM_ROTATION_DURATION = "rotation_duration"
PARAM_ENTITY_ID = "entity_id"
PARAM_PTZ = "ptz"
PARAM_OPTION = "option"
PARAM_COUNT_DOWN_SWITCH = "count_down_switch"
PARAM_OVERCHARGE_SWITCH = "overcharge_switch"

# service
SERVICE_RESTART_DEVICE = "restart_device"
SERVICE_CONTROL_MOVE_PTZ = "control_move_ptz"
SERVICE_TURN_ON = "turn_on"
SERVICE_TURN_OFF = "turn_off"
SERVICE_SELECT = "select"


PLATFORMS = ["select", "sensor", "switch", "camera", "button", "binary_sensor", "text"]
