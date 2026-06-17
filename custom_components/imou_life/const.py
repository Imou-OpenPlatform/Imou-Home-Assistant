"""Constants."""

# Internal constants
DOMAIN = "imou_life"

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

# event push
EVENT_IMOU_EVENT = f"{DOMAIN}_event"
EVENT_IMOU_ALARM = f"{DOMAIN}_alarm"
CALLBACK_FLAG_ALARM = "alarm"
CALLBACK_FLAG_DEVICE_STATUS = "deviceStatus"
CALLBACK_FLAG_IOT = "iot"
CALLBACK_FLAG_NUMBERSTAT = "numberstat"
CALLBACK_FLAG_FACE_ANALYSIS = "faceAnalysis"
CALLBACK_FLAG_OPTIONS = (
    CALLBACK_FLAG_ALARM,
    CALLBACK_FLAG_DEVICE_STATUS,
    CALLBACK_FLAG_IOT,
    CALLBACK_FLAG_NUMBERSTAT,
    CALLBACK_FLAG_FACE_ANALYSIS,
)
DEFAULT_CALLBACK_FLAGS = [
    CALLBACK_FLAG_ALARM,
    CALLBACK_FLAG_DEVICE_STATUS,
    CALLBACK_FLAG_IOT,
]
DEFAULT_BASE_PUSH = "2"

# Alarm type -> Chinese description mapping for notifications
ALARM_TYPE_NAMES: dict[str, str] = {
    "videoMotion": "动态检测",
    "human": "人形检测",
    "alarmPIR": "人体红外",
    "smokeAlarm": "烟感报警",
    "gasAlarm": "燃气报警",
    "waterAlarm": "水浸报警",
    "soundLightAlarm": "声光报警",
    "alarmLocal": "本地报警",
    "Doorbell": "门铃事件",
    "magnetomerAlarm": "门磁报警",
    "urgencyAlarm": "紧急按钮报警",
    "sensorAbnormal": "防拆报警",
    "hoveringAlarm": "徘徊报警",
    "crossLineDetection": "拌线报警",
    "highTemAbnormal": "温度过高",
    "lowTemAbnormal": "温度过低",
    "highHumAbnormal": "湿度过高",
    "lowHumAbnormal": "湿度过低",
    "electricAbnormal": "电压异常",
    "fireAlarm": "火警报警",
    "earlyWarning": "早期预警",
    "alarmOccurs": "报警发生",
    "alarmEnd": "报警结束",
    "earlyWarningEnd": "早期预警结束",
    "online": "设备上线",
    "offline": "设备离线",
    "beOpenedDoor": "开门成功",
    "notOpenedDoor": "开门失败",
    "openDoorAbnormal": "恶意开门",
    "hijackAlarm": "劫持报警",
    "mobileDetect": "移动监测",
    "callBellEvent": "呼叫事件",
    "abAlarmSound": "异常音报警",
    "objectMoved": "物品搬移",
    "goodsMove": "物品搬移",
    "leftDetection": "物品遗留",
    "videoBlind": "视频遮挡",
    "wireLessDevLowPower": "低电量",
    "litElec": "锂电池低电量",
    "alkElec": "碱性电池低电量",
    "storageAbnormal": "SD卡异常",
    "storageEmpty": "无SD卡",
    "checkAlarmFail": "故障报警",
    "unknownAlarm": "未知告警",
}

# service
SERVICE_RESTART_DEVICE = "restart_device"
SERVICE_CONTROL_MOVE_PTZ = "control_move_ptz"
SERVICE_TURN_ON = "turn_on"
SERVICE_TURN_OFF = "turn_off"
SERVICE_SELECT = "select"


PLATFORMS = ["select", "sensor", "switch", "camera", "button", "binary_sensor", "text"]
