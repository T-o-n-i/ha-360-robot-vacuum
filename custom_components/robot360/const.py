"""Constants for the 360 Robot integration."""

from datetime import timedelta

DOMAIN = "robot360"
MANUFACTURER = "Qihoo 360"

CONF_MID = "mid"
CONF_ANDROIDID = "androidid"
# Saved account login (q/t/qid) so HA does not need the password + captcha on every start.
CONF_TOKENS = "tokens"

UPDATE_INTERVAL = timedelta(seconds=60)
# Log in again before the app session (sid, roughly 12 h) runs out.
SESSION_MAX_AGE = timedelta(hours=11)
# Ask for fresh status this many seconds after a command.
STATUS_DELAY_AFTER_COMMAND = 3

# Consumable counters change slowly; ask for them once an hour.
CONSUMABLES_INTERVAL = timedelta(hours=1)
INFO_CONSUMABLES = "21015"

# infoTypes whose push payload is not a status object (map data etc.).
NON_STATUS_INFO_TYPES = {"20002", "21011", "21014"}
