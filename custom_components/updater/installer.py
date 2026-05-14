from __future__ import annotations

from pathlib import Path
import shutil
import zipfile
import logging
import re
from typing import Any
import yaml

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import UpdaterApi

_LOGGER = logging.getLogger(__name__)
_MANAGED_LOVELACE_START = "# BEGIN updater-managed-lovelace"
_MANAGED_LOVELACE_END = "# END updater-managed-lovelace"


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy folder contents recursively from src into dst."""
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def _extract_zip(zip_path: Path, target_dir: Path) -> None:
    """Extract release zip into staging directory."""
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(target_dir)


def _copy_bootstrap_manifest_if_missing(staging_dir: Path, config_dir: Path) -> None:
    """Copy release manifest once; do not overwrite existing manifest."""
    src_manifest = staging_dir / "release-manifest.json"
    if not src_manifest.exists():
        return
    target_dir = config_dir / "updater" / "state"
    target_dir.mkdir(parents=True, exist_ok=True)
    dst_manifest = target_dir / "release-manifest.json"
    if dst_manifest.exists():
        return
    shutil.copy2(src_manifest, dst_manifest)


def _validate_dashboard_view(view: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(view, dict):
        raise RuntimeError(f"{label} view must be an object")
    path = view.get("path")
    title = view.get("title")
    cards = view.get("cards")
    if not isinstance(path, str) or not path.strip():
        raise RuntimeError(f"{label} view.path must be a non-empty string")
    if not isinstance(title, str) or not title.strip():
        raise RuntimeError(f"{label} view.title must be a non-empty string")
    if not isinstance(cards, list):
        raise RuntimeError(f"{label} view.cards must be a list")
    return dict(view)


def _load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _compose_dashboard_config(
    preset_path: Path,
    customer_path: Path | None,
) -> dict[str, Any]:
    preset_raw = _load_yaml(preset_path)
    if not isinstance(preset_raw, dict):
        raise RuntimeError("Preset dashboard must be a YAML object")
    preset_views = preset_raw.get("views")
    if not isinstance(preset_views, list) or not preset_views:
        raise RuntimeError("Preset dashboard must define non-empty views list")

    preset = dict(preset_raw)
    preset["views"] = [_validate_dashboard_view(view, label="Preset") for view in preset_views]

    customer_raw = _load_yaml(customer_path) if customer_path else None
    if customer_raw is None:
        return preset
    if not isinstance(customer_raw, dict):
        raise RuntimeError("Customer dashboard override must be a YAML object")

    allowed_keys = {"title", "hide_views", "views_append", "view_overrides", "views"}
    unknown_keys = set(customer_raw.keys()) - allowed_keys
    if unknown_keys:
        raise RuntimeError(f"Unsupported customer override keys: {', '.join(sorted(unknown_keys))}")

    merged = dict(preset)
    views: list[dict[str, Any]] = [dict(view) for view in preset["views"]]
    view_by_path = {view["path"]: view for view in views}

    title_override = customer_raw.get("title")
    if title_override is not None:
        if not isinstance(title_override, str) or not title_override.strip():
            raise RuntimeError("Customer title override must be a non-empty string")
        merged["title"] = title_override

    hide_views = customer_raw.get("hide_views", [])
    if hide_views:
        if not isinstance(hide_views, list) or not all(isinstance(v, str) for v in hide_views):
            raise RuntimeError("Customer hide_views must be a string list")
        hide_set = {v.strip() for v in hide_views if v.strip()}
        views = [view for view in views if view["path"] not in hide_set]
        view_by_path = {view["path"]: view for view in views}

    view_overrides = customer_raw.get("view_overrides", {})
    if view_overrides:
        if not isinstance(view_overrides, dict):
            raise RuntimeError("Customer view_overrides must be an object keyed by view path")
        for view_path, override in view_overrides.items():
            if not isinstance(view_path, str) or not view_path.strip():
                raise RuntimeError("Customer view_overrides keys must be non-empty strings")
            if not isinstance(override, dict):
                raise RuntimeError(f"Customer view_overrides[{view_path}] must be an object")
            target = view_by_path.get(view_path)
            if not target:
                continue
            if "title" in override:
                if not isinstance(override["title"], str) or not override["title"].strip():
                    raise RuntimeError(f"Customer view_overrides[{view_path}].title must be non-empty string")
                target["title"] = override["title"]
            if "cards" in override:
                if not isinstance(override["cards"], list):
                    raise RuntimeError(f"Customer view_overrides[{view_path}].cards must be a list")
                target["cards"] = override["cards"]
            if "cards_append" in override:
                if not isinstance(override["cards_append"], list):
                    raise RuntimeError(f"Customer view_overrides[{view_path}].cards_append must be a list")
                target["cards"] = list(target.get("cards", [])) + override["cards_append"]

    views_append = customer_raw.get("views_append")
    if views_append is None and isinstance(customer_raw.get("views"), list):
        # Backward-compatible fallback: treat `views` as appended customer views.
        views_append = customer_raw.get("views")
    if views_append:
        if not isinstance(views_append, list):
            raise RuntimeError("Customer views_append must be a list")
        for view in views_append:
            validated = _validate_dashboard_view(view, label="Customer")
            views.append(validated)

    if not views:
        raise RuntimeError("Composed dashboard has no views")

    merged["views"] = views
    return merged


def _safe_dashboard_slug(value: str) -> str:
    """Normalize to a Lovelace dashboards key; HA requires at least one '-' in the URL path."""
    slug = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
    slug = slug or "signapps_dashboard"
    slug = re.sub(r"-+", "-", slug.replace("_", "-")).strip("-")
    if "-" not in slug:
        slug = f"{slug}-signapps"
    return slug


def _collect_card_resource_urls(config_dir: Path) -> list[str]:
    """Collect custom card entry resources from /config/www/ha-signapps-cards."""
    cards_root = config_dir / "www" / "ha-signapps-cards"
    if not cards_root.exists():
        return []

    urls: list[str] = []
    for file_path in cards_root.rglob("*.js"):
        rel = file_path.relative_to(cards_root).as_posix()
        if rel.endswith(".map"):
            continue
        # Skip build internals; entry files import shared chunks automatically.
        if rel.startswith("shared/") or rel.startswith("assets/"):
            continue
        urls.append(f"/local/ha-signapps-cards/{rel}")

    return sorted(set(urls))


def _write_generated_dashboard(config_dir: Path, customer_key: str, composed: dict[str, Any]) -> Path:
    generated_dir = config_dir / "dashboard" / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    out_path = generated_dir / f"{customer_key}.yaml"
    temp_path = generated_dir / f".{customer_key}.yaml.tmp"
    with temp_path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(composed, file, sort_keys=False, allow_unicode=True)
    temp_path.replace(out_path)
    return out_path


def _ensure_lovelace_dashboard_wiring(
    config_dir: Path,
    *,
    dashboard_slug: str,
    dashboard_title: str,
    generated_filename: str,
    resources: list[str],
) -> None:
    # HA requires a hyphen in dashboard URL keys; normalize here so wiring stays valid
    # even if an older caller passed a legacy slug.
    dashboard_slug = _safe_dashboard_slug(dashboard_slug)
    config_file = config_dir / "configuration.yaml"
    existing = ""
    if config_file.exists():
        existing = config_file.read_text(encoding="utf-8")

    resources_block = ""
    if resources:
        resources_block = "  resources:\n" + "".join(
            f"    - url: {url}\n      type: module\n" for url in resources
        )

    managed_block = (
        f"{_MANAGED_LOVELACE_START}\n"
        "lovelace:\n"
        "  mode: yaml\n"
        f"{resources_block}"
        "  dashboards:\n"
        f"    {dashboard_slug}:\n"
        "      mode: yaml\n"
        f"      title: {dashboard_title}\n"
        "      icon: mdi:view-dashboard\n"
        "      show_in_sidebar: true\n"
        f"      filename: {generated_filename}\n"
        f"{_MANAGED_LOVELACE_END}\n"
    )

    if _MANAGED_LOVELACE_START in existing and _MANAGED_LOVELACE_END in existing:
        pattern = re.compile(
            rf"{re.escape(_MANAGED_LOVELACE_START)}.*?{re.escape(_MANAGED_LOVELACE_END)}\n?",
            flags=re.DOTALL,
        )
        updated = re.sub(pattern, managed_block, existing)
    else:
        updated = existing.rstrip() + "\n\n" + managed_block

    config_file.write_text(updated, encoding="utf-8")


async def _reload_lovelace_or_fallback(hass: HomeAssistant) -> None:
    if hass.services.has_service("lovelace", "reload_resources"):
        await hass.services.async_call("lovelace", "reload_resources", {}, blocking=True)
        return
    if hass.services.has_service("lovelace", "reload"):
        await hass.services.async_call("lovelace", "reload", {}, blocking=True)
        return
    # Fallback: existing install flow also requests full restart.
    _LOGGER.info("No Lovelace reload service found; relying on Home Assistant restart.")


async def _create_backup(hass: HomeAssistant, backup_name: str) -> str | None:
    """Create backup using available HA service(s), return backup id/slug if available."""
    if hass.services.has_service("backup", "create"):
        backup_result = await hass.services.async_call(
            "backup",
            "create",
            {"name": backup_name},
            blocking=True,
            return_response=True,
        )
        if isinstance(backup_result, dict):
            return backup_result.get("slug") or backup_result.get("backup_id") or backup_result.get("id")
        return None

    # Supervisor fallback (older installs)
    if hass.services.has_service("hassio", "backup_full"):
        await hass.services.async_call(
            "hassio",
            "backup_full",
            {"name": backup_name},
            blocking=True,
        )
        return None

    _LOGGER.warning("No backup service found (backup.create or hassio.backup_full). Continuing without backup.")
    return None


async def _restore_backup(hass: HomeAssistant, backup_id: str) -> None:
    """Restore backup using available HA service(s)."""
    if hass.services.has_service("backup", "restore"):
        await hass.services.async_call(
            "backup",
            "restore",
            {"slug": backup_id},
            blocking=True,
        )
        return

    # Supervisor fallback (older installs)
    if hass.services.has_service("hassio", "restore_full"):
        await hass.services.async_call(
            "hassio",
            "restore_full",
            {"slug": backup_id},
            blocking=True,
        )
        return

    raise RuntimeError("No backup restore service found (backup.restore or hassio.restore_full)")


async def install_desired_release(
    hass: HomeAssistant,
    *,
    api: UpdaterApi,
    coordinator: DataUpdateCoordinator[dict[str, Any]],
    state: dict[str, Any],
    store: Store[dict[str, Any]],
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Install the currently desired release using backup + staged deploy."""
    desired = (coordinator.data or {}).get("desired_release") or {}
    version = desired.get("version")
    artifact_url = desired.get("artifact_url")

    if not version or not artifact_url:
        raise RuntimeError("No desired release available")
    if state.get("installed_version") == version:
        return {"ok": True, "message": f"Already on version {version}"}

    backup_id = await _create_backup(hass, f"pre-updater-{entry.entry_id}-{version}")

    config_dir = Path(hass.config.path())
    updater_root = config_dir / "updater"
    releases_dir = updater_root / "releases"
    staging_dir = updater_root / "staging" / version
    zip_path = releases_dir / f"{version}.zip"
    releases_dir.mkdir(parents=True, exist_ok=True)

    await api.download_file(artifact_url=artifact_url, target_path=str(zip_path))
    await hass.async_add_executor_job(_extract_zip, zip_path, staging_dir)

    # Release package structure based on ha-plan.md:
    # - packages/ -> /config/packages
    # - dashboard/ -> /config/dashboard
    # - www/ -> /config/www
    await hass.async_add_executor_job(_copy_tree, staging_dir / "packages", config_dir / "packages")
    await hass.async_add_executor_job(_copy_tree, staging_dir / "dashboard", config_dir / "dashboard")
    await hass.async_add_executor_job(_copy_tree, staging_dir / "www", config_dir / "www")
    await hass.async_add_executor_job(_copy_bootstrap_manifest_if_missing, staging_dir, config_dir)

    # Dashboard wiring phase: preset base + customer override -> generated dashboard + Lovelace wiring
    preset_key = str(desired.get("preset_key") or "default")
    customer_key = str(desired.get("customer_key") or entry.data.get("customer_id") or "default")
    dashboard_slug = _safe_dashboard_slug(str(desired.get("dashboard_slug") or f"signapps_{customer_key}"))
    generated_filename = f"dashboard/generated/{customer_key}.yaml"
    try:
        preset_path = config_dir / "dashboard" / "presets" / f"{preset_key}.yaml"
        customer_path = config_dir / "dashboard" / "customers" / f"{customer_key}.yaml"
        composed = await hass.async_add_executor_job(_compose_dashboard_config, preset_path, customer_path)
        generated_path = await hass.async_add_executor_job(
            _write_generated_dashboard,
            config_dir,
            customer_key,
            composed,
        )
        dashboard_title = str(composed.get("title") or f"SignApps ({customer_key})")
        resources = await hass.async_add_executor_job(_collect_card_resource_urls, config_dir)
        await hass.async_add_executor_job(
            lambda: _ensure_lovelace_dashboard_wiring(
                config_dir,
                dashboard_slug=dashboard_slug,
                dashboard_title=dashboard_title,
                generated_filename=generated_filename,
                resources=resources,
            )
        )
        await _reload_lovelace_or_fallback(hass)
        _LOGGER.info("Dashboard wiring applied: generated=%s slug=%s", generated_path, dashboard_slug)
    except Exception as err:
        state["last_update_status"] = "dashboard_apply_failed"
        await store.async_save(state)
        raise RuntimeError(f"Dashboard wiring failed: {err}") from err

    state["pending_version"] = version
    state["last_backup_id"] = backup_id
    state["last_artifact_url"] = artifact_url
    state["last_update_status"] = "pending_restart"
    await store.async_save(state)

    await hass.services.async_call("homeassistant", "restart", {}, blocking=False)
    return {"ok": True, "message": f"Installed staged release {version}; restart requested", "backup_id": backup_id}


async def restore_last_backup(
    hass: HomeAssistant,
    *,
    state: dict[str, Any],
    store: Store[dict[str, Any]],
) -> dict[str, Any]:
    """Restore the most recent updater backup and request restart."""
    backup_id = state.get("last_backup_id")
    if not backup_id:
        raise RuntimeError("No stored backup id to restore")

    await _restore_backup(hass, backup_id)

    state["last_update_status"] = "rollback_requested"
    await store.async_save(state)
    await hass.services.async_call("homeassistant", "restart", {}, blocking=False)
    return {"ok": True, "message": f"Restore requested for backup {backup_id}"}
