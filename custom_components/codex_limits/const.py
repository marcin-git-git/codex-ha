DOMAIN = "codex_limits"
PLATFORMS = ["sensor"]

CONF_API_URL = "api_url"
CONF_LIMITS = "limits"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_API_KEY = "api_key"
CONF_HEADERS = "headers"

DEFAULT_SCAN_INTERVAL = 60
DEFAULT_API_URL = "http://localhost:11434/api/limits"

ATTR_LIMIT_NAME = "limit_name"
ATTR_LIMIT_TYPE = "limit_type"
ATTR_RESET_TIME = "reset_time"
ATTR_PREVIOUS_VALUE = "previous_value"
ATTR_IS_RESET = "is_reset"

LIMIT_TYPES = {
    "5h": {"name": "Limit 5-godzinny", "icon": "mdi:timer-sand", "max": 5},
    "5d": {"name": "Limit 5-dniowy", "icon": "mdi:calendar-clock", "max": 5},
}

SERVICE_CHECK_LIMITS = "check_limits"
