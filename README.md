# Updater Home Assistant Integration

This integration connects Home Assistant to the deployment server in this repository.

## What it does

- Registers the current Home Assistant instance as a device for a specific customer.
- Stores the issued `device_id` + device token locally after first registration.
- Periodically checks `/api/devices/{device_id}/desired-release`.
- Sends `/api/devices/{device_id}/checkin` each poll.
- Automatically installs a newer desired release when detected.
- After each poll, fetches **`GET /api/devices/{device_id}/tunnel-credentials`** (device bearer) when the backend has provisioned a Cloudflare tunnel, and writes **`/config/signapps_tunnel/credentials.json`** for the **SignApps Tunnel** add-on (see `addons/signapps-tunnel/README.md` and `ha-cloudflare-plan.md` §19).
- Exposes entities:
  - `sensor.installed_version`
  - `sensor.latest_version`
  - `sensor.last_check_in`
  - `binary_sensor.update_available`

## Configuration

Add the integration through the Home Assistant UI and provide:

- `server_url` (example: `http://192.168.1.10:3001`)
- `customer_id` (customer id from backend)
- `api_token` (customer-scoped API token)
  - Generate/rotate per customer via: `POST /api/customers/{customer_id}/token/rotate` (admin JWT)
- `device_name`
- `channel` (`stable` or `beta`)
- `scan_interval` in seconds

### Local values file on HA

You can override these values from a file on Home Assistant itself:

- `/config/updater.values.json`

Example:

```json
{
  "server_url": "http://192.168.1.10:3001",
  "customer_id": "cmabcd1234",
  "api_token": "customer-token-here",
  "device_name": "HA Main",
  "channel": "stable",
  "scan_interval": 300
}
```

If present, this file overrides the UI-configured values at startup.

## Folder placement

This repo stores the source in:

`integrations/updater`

For Home Assistant loading, copy it to:

`<config>/custom_components/updater`

**Platform release zips** (from `npm run release:prepare`) only ship `packages/`, `dashboard/`, and `www/`. They do **not** replace this Python integration. Whenever you change code under `integrations/updater/` (for example `installer.py`), copy the whole folder to `custom_components/updater` again and restart Home Assistant, or the instance will keep running the old logic.

The structure is based on the Home Assistant integration creation docs:
[Creating your first integration](https://developers.home-assistant.io/docs/creating_component_index/).

## Runtime flow

1. User configures integration in Home Assistant with:
   - server URL
   - customer ID
   - customer API token
2. Integration loads stored `device_id` + device token from HA storage.
3. If missing, integration registers the device:
   - `POST /api/devices` with customer token
   - stores returned `device_id` + device token
4. On each poll interval:
   - `GET /api/devices/{device_id}/desired-release`
   - compare desired version vs installed version
   - `POST /api/devices/{device_id}/checkin` with status
   - if desired version is newer: install release + compose/apply dashboard wiring
5. Coordinator updates entities:
   - installed version
   - latest version
   - update available
   - last check-in timestamp

## Release zip structure

Release artifacts are expected to be zip files with this structure (aligned with `ha-plan.md`):

```text
packages/
dashboard/
www/
release-manifest.json (optional, bootstrap-only)
```

Deploy mapping:

- `packages/**` -> `/config/packages/**`
- `dashboard/**` -> `/config/dashboard/**`
- `www/**` -> `/config/www/**`
- `release-manifest.json` -> `/config/updater/state/release-manifest.json` (first install only, never overwritten)

For custom cards, release assets should live under:

- `www/ha-signapps-cards/**` -> `/config/www/ha-signapps-cards/**`

## Update and rollback services

This integration registers two Home Assistant services:

- `updater.install_desired_release`
  - creates HA backup
  - downloads + extracts release zip
  - deploys mapped directories
  - requests HA restart
  - can be called manually, but normal operation auto-installs when a newer desired release appears
- `updater.restore_last_backup`
  - restores the last updater-created backup
  - requests HA restart

Both services accept optional `entry_id` to target a specific updater config entry.

## Dashboard preset + customer override model

Dashboard wiring is YAML-managed and applied by updater during install.

Source files copied by release:

- `/config/dashboard/presets/<preset_key>.yaml` (base preset)
- `/config/dashboard/customers/<customer_key>.yaml` (customer override, optional)

Generated output:

- `/config/dashboard/generated/<customer_key>.yaml`

Desired release metadata should include:

- `preset_key` (default: `default`)
- `customer_key` (usually backend customer id)
- `dashboard_slug` (Lovelace dashboard key; must contain `-` in the final value — the updater enforces this for Home Assistant)

### Customer override schema (strict)

Allowed keys in customer file:

- `title`: optional string, overrides dashboard title
- `hide_views`: optional list of view `path` strings to remove from preset
- `view_overrides`: optional object keyed by view path with:
  - `title`: optional string
  - `cards`: optional list (replace cards)
  - `cards_append`: optional list (append cards)
- `views_append`: optional list of full view objects to append
- `views`: legacy alias for `views_append`

Unknown keys are rejected.

### Lovelace wiring

Updater writes a managed Lovelace block in `/config/configuration.yaml` between markers:

- `# BEGIN updater-managed-lovelace`
- `# END updater-managed-lovelace`

If reload services are available, updater calls Lovelace reload; otherwise it relies on the restart already requested by install flow.

### Failure behavior

If dashboard compose/wiring fails:

- previously generated dashboard file is preserved
- updater sets `last_update_status = dashboard_apply_failed`
- install call fails with a detailed error
