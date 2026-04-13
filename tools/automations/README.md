# Automations

Automation scripts are low-risk background helpers that can be run by cron.

Current scripts:

- `refresh_status.py`: refreshes a status file covering uncategorized raw inbox files and pending inferred intents
- `install_cron.sh`: installs a cron entry that refreshes that status file on a schedule

These scripts should prefer reporting and surfacing review needs over silently mutating durable state.
