DOMAIN = "updater"

CONF_SERVER_URL = "server_url"
CONF_CUSTOMER_ID = "customer_id"
CONF_API_TOKEN = "api_token"
CONF_DEVICE_NAME = "device_name"
CONF_CHANNEL = "channel"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_NAME = "Updater"
DEFAULT_CHANNEL = "stable"
DEFAULT_SCAN_INTERVAL = 300
LOCAL_VALUES_FILE = "updater.values.json"
INSTALLATION_STORE_KEY = f"{DOMAIN}_installation"
INTEGRATION_VERSION = "0.2.5"

PLATFORMS = ["sensor", "binary_sensor"]

STORE_VERSION = 1
STORE_KEY = f"{DOMAIN}_state"

DATA_API = "api"
DATA_COORDINATOR = "coordinator"
DATA_STATE = "state"
DATA_STORE = "store"
DATA_ENTRY = "entry"

ATTR_DESIRED_RELEASE = "desired_release"
ATTR_LAST_CHECKIN = "last_checkin"

# Lovelace resource URLs containing any of these substrings (case-insensitive) are also registered
# under root `frontend.extra_module_url` and removed from Lovelace YAML `resources:` so the module
# is not loaded twice (card-mod and similar). HACS only stores these in `.storage/lovelace_resources`;
# it does not append `frontend.extra_module_url` for you.
FRONTEND_MODULE_URL_SUBSTRINGS = (
    "lovelace-card-mod",
)

SERVICE_INSTALL_DESIRED_RELEASE = "install_desired_release"
SERVICE_RESTORE_LAST_BACKUP = "restore_last_backup"
