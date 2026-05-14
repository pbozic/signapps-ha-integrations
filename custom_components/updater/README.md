# SignApps Updater (Home Assistant)

Custom integration that connects Home Assistant to your **SignApps** (or compatible) deployment server: device registration, release checks, optional automatic installs, and optional Cloudflare tunnel credential delivery for the companion **SignApps Tunnel** add-on.

## Features

- Registers this Home Assistant instance and stores credentials locally after first setup.
- Polls the server for the **desired release**, reports version sensors, and sends periodic check-ins.
- Can **download and apply** a newer release when the server indicates one (with backup and restart flow).
- When the server has provisioned a tunnel, fetches tunnel credentials and writes **`/config/signapps_tunnel/credentials.json`** for the SignApps Tunnel add-on (no manual tunnel token paste in the add-on).

**Entities:** `sensor.installed_version`, `sensor.latest_version`, `sensor.last_check_in`, `binary_sensor.update_available`.

## Install

Use [HACS](https://hacs.xyz/) (custom repository) or copy this integration into **`custom_components/updater`** under your Home Assistant configuration directory, then restart Home Assistant.

## Configuration (UI)

Add the integration in **Settings → Devices & services → Add integration** and provide the values supplied by your administrator or hosting provider:

| Field | Description |
|--------|-------------|
| **Server URL** | Base URL of the deployment API (HTTPS recommended). |
| **Customer ID** | Identifies your account/tenant on the server. |
| **API token** | Customer-scoped token used for registration and updates. |
| **Device name** | Friendly label for this Home Assistant instance. |
| **Channel** | Release channel, e.g. `stable` or `beta`. |
| **Scan interval** | Seconds between polls. |

## Optional local overrides

If **`/config/updater.values.json`** exists, its keys override the UI-configured values at startup (same field names as above). Use only on trusted hosts; the file can contain secrets.

```json
{
  "server_url": "https://your-server.example",
  "customer_id": "YOUR_CUSTOMER_ID",
  "api_token": "YOUR_TOKEN",
  "device_name": "Home",
  "channel": "stable",
  "scan_interval": 300
}
```

## Services

- **`updater.install_desired_release`** — Creates a backup, downloads the current desired release package, deploys configured paths, and requests a Home Assistant restart. Normally runs automatically when a newer desired release is detected; can be triggered manually. Optional `entry_id` if you have multiple config entries.
- **`updater.restore_last_backup`** — Restores the last backup created by the updater install flow and requests a restart. Optional `entry_id`.

## Release package layout (advanced)

The server serves versioned **zip** artifacts. Typical content:

```text
packages/
dashboard/
www/
release-manifest.json   (optional; used only on first bootstrap)
```

Typical deploy mapping on the Home Assistant host:

- `packages/**` → `/config/packages/**`
- `dashboard/**` → `/config/dashboard/**`
- `www/**` → `/config/www/**`
- `release-manifest.json` → `/config/updater/state/release-manifest.json` (first install only)

Custom frontend assets often live under `www/` and are unpacked into matching paths under `/config/www/`.

## Dashboard preset model (advanced)

Dashboard YAML is composed from a **preset** plus an optional **per-customer** override; output is written under `/config/dashboard/generated/`. The server’s desired-release metadata selects preset and customer keys and the Lovelace dashboard slug.

**Customer override file** (strict schema; unknown keys are rejected):

- `title` — optional dashboard title override  
- `hide_views` — optional list of view `path` strings to remove from the preset  
- `view_overrides` — optional map keyed by view path: `title`, `cards`, `cards_append`  
- `views_append` — optional list of full view objects to append (`views` is a legacy alias)

The integration may inject a managed Lovelace block in **`configuration.yaml`** between `# BEGIN updater-managed-lovelace` and `# END updater-managed-lovelace`. If dashboard application fails, the previous generated file is kept and the install surfaces an error.

**Resources:** The block uses YAML-mode `lovelace:` with a top-level `resources:` list. The updater **merges** (de-duplicated by URL):

1. Entry scripts under `/config/www/ha-signapps-cards/**/*.js` (SignApps cards)  
2. URLs still present in **`/.storage/lovelace_resources`** (typical HACS registrations), preserving each entry’s **`type`** (`module` / `js` / `css`)  
3. URLs already listed in the **previous** managed block (so re-runs do not shrink the list)

HACS registers plugins in **`.storage/lovelace_resources`** only; it does **not** append **`frontend.extra_module_url`**. For URLs that must load as global frontend modules, the updater reads the same merged list: any URL whose path matches **`FRONTEND_MODULE_URL_SUBSTRINGS`** in **`const.py`** (default: **`lovelace-card-mod`** for [card-mod](https://github.com/thomasloven/lovelace-card-mod)) is merged into root **`frontend.extra_module_url`** in `configuration.yaml` and is **not** repeated under Lovelace **`resources:`**, so the module is not loaded twice. Add substrings for other HACS plugins that need the same behavior. Non-HACS URLs still require a manual **`frontend:`** block.

If HACS entries were already lost from `.storage` before the next updater run, restore them once from a backup or re-add via HACS; subsequent installs will keep them in `configuration.yaml`.

Restart Home Assistant after an install that changed `configuration.yaml`.

---

Structure follows Home Assistant’s integration guidelines: [Creating your first integration](https://developers.home-assistant.io/docs/creating_component_index/).
