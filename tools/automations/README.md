# Automations

Automation scripts are low-risk background helpers that can be run by cron.

Current scripts:

- `refresh_status.py`: refreshes a status file covering uncategorized raw inbox files and pending inferred intents
- `install_cron.sh`: installs a cron entry that refreshes that status file on a schedule

Manual maintenance commands:

- `exocortex-hygiene check --write-report`: reports context hygiene across active state, queues, raw inbox, wiki-map freshness, and session artifact completeness
- `exocortex-hygiene apply --archive-surface-now`: archives and clears handled surface-now items
- `exocortex-hygiene apply --refresh-wiki-map`: refreshes `wiki-map.md` from managed wiki indexes
- `exocortex-hygiene apply --reprocess-sessions --reprocess-limit 10 --reprocess-timeout-seconds 120`: reprocesses missing session artifacts with a per-manifest timeout
- `exocortex-hygiene apply --ingest-raw --ingest-limit 10`: ingests raw inbox files into seed wiki source notes
- `exocortex-ingest --limit 10`: dry-runs raw inbox ingestion before moving files
- `exocortex-retrieve "<query>"`: searches managed markdown outside the active preload
- `exocortex-usage summary today`: summarizes private token and dollar usage
- `exocortex-health`: reports whether the intelligence loop is operational enough to trust
- `exocortex-review defer <needle>`: records a defer without removing the candidate from pending review
- `exocortex-review expire --days 30 --apply`: expires old pending candidates

These scripts should prefer reporting and surfacing review needs over silently mutating durable state.
