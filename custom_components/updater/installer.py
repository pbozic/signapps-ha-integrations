from __future__ import annotations

from pathlib import Path
import json
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
from .const import FRONTEND_MODULE_URL_SUBSTRINGS

_LOGGER = logging.getLogger(__name__)
_MANAGED_LOVELACE_START = "# BEGIN updater-managed-lovelace"
_MANAGED_LOVELACE_END = "# END updater-managed-lovelace"
_MANAGED_PANEL_START = "# BEGIN updater-managed-panel"
_MANAGED_PANEL_END = "# END updater-managed-panel"


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

    allowed_keys = {"title", "hide_views", "views_append", "view_overrides", "views", "react"}
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
    merged.pop("react", None)
    return merged


def _validate_react_view(view: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(view, dict):
        raise RuntimeError(f"{label} react view must be an object")
    path = view.get("path")
    title = view.get("title")
    cards = view.get("cards")
    if not isinstance(path, str) or not path.strip():
        raise RuntimeError(f"{label} react view.path must be a non-empty string")
    if not isinstance(title, str) or not title.strip():
        raise RuntimeError(f"{label} react view.title must be a non-empty string")
    if not isinstance(cards, list):
        raise RuntimeError(f"{label} react view.cards must be a list")
    return dict(view)


def _compose_react_dashboard_config(
    preset_path: Path,
    customer_path: Path | None,
) -> dict[str, Any]:
    preset_raw = _load_yaml(preset_path)
    preset_react = preset_raw.get("react") if isinstance(preset_raw, dict) else None
    if not isinstance(preset_react, dict):
        preset_react = {}

    enabled = bool(preset_react.get("enabled", False))
    title = str(preset_react.get("title") or "SignApps")
    views: list[dict[str, Any]] = []
    if enabled:
        preset_views = preset_react.get("views")
        if isinstance(preset_views, list):
            views = [_validate_react_view(view, label="Preset react") for view in preset_views]

    customer_raw = _load_yaml(customer_path) if customer_path else None
    if not isinstance(customer_raw, dict):
        return {"enabled": enabled and bool(views), "title": title, "views": views}

    customer_react = customer_raw.get("react")
    if not isinstance(customer_react, dict):
        return {"enabled": enabled and bool(views), "title": title, "views": views}

    if customer_react.get("enabled") is False:
        return {"enabled": False, "title": title, "views": []}

    if customer_react.get("enabled") is True:
        enabled = True

    title_override = customer_react.get("title")
    if isinstance(title_override, str) and title_override.strip():
        title = title_override.strip()

    view_by_path = {view["path"]: view for view in views}

    hide_views = customer_react.get("hide_views", [])
    if hide_views:
        if not isinstance(hide_views, list) or not all(isinstance(v, str) for v in hide_views):
            raise RuntimeError("Customer react.hide_views must be a string list")
        hide_set = {v.strip() for v in hide_views if v.strip()}
        views = [view for view in views if view["path"] not in hide_set]
        view_by_path = {view["path"]: view for view in views}

    view_overrides = customer_react.get("view_overrides", {})
    if view_overrides:
        if not isinstance(view_overrides, dict):
            raise RuntimeError("Customer react.view_overrides must be an object keyed by view path")
        for view_path, override in view_overrides.items():
            if not isinstance(view_path, str) or not view_path.strip():
                raise RuntimeError("Customer react.view_overrides keys must be non-empty strings")
            if not isinstance(override, dict):
                raise RuntimeError(f"Customer react.view_overrides[{view_path}] must be an object")
            target = view_by_path.get(view_path)
            if not target:
                continue
            if "title" in override:
                if not isinstance(override["title"], str) or not override["title"].strip():
                    raise RuntimeError(
                        f"Customer react.view_overrides[{view_path}].title must be non-empty string"
                    )
                target["title"] = override["title"]
            if "cards" in override:
                if not isinstance(override["cards"], list):
                    raise RuntimeError(f"Customer react.view_overrides[{view_path}].cards must be a list")
                target["cards"] = override["cards"]
            if "cards_append" in override:
                if not isinstance(override["cards_append"], list):
                    raise RuntimeError(
                        f"Customer react.view_overrides[{view_path}].cards_append must be a list"
                    )
                target["cards"] = list(target.get("cards", [])) + override["cards_append"]

    views_append = customer_react.get("views_append")
    if views_append is None and isinstance(customer_react.get("views"), list):
        views_append = customer_react.get("views")
    if views_append:
        if not isinstance(views_append, list):
            raise RuntimeError("Customer react.views_append must be a list")
        for view in views_append:
            validated = _validate_react_view(view, label="Customer react")
            views.append(validated)
        enabled = True

    if views and customer_react.get("enabled") is not False:
        enabled = True

    return {"enabled": enabled and bool(views), "title": title, "views": views}


def _write_generated_react_dashboard(config_dir: Path, customer_key: str, composed: dict[str, Any]) -> Path:
    generated_dir = config_dir / "dashboard" / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    out_path = generated_dir / f"{customer_key}.react.json"
    temp_path = generated_dir / f".{customer_key}.react.json.tmp"
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(composed, file, indent=2, ensure_ascii=False)
        file.write("\n")
    temp_path.replace(out_path)
    return out_path


def _sync_react_config_to_www(config_dir: Path, composed: dict[str, Any]) -> Path:
    www_dir = config_dir / "www" / "signapps-dashboard"
    www_dir.mkdir(parents=True, exist_ok=True)
    out_path = www_dir / "config.json"
    temp_path = www_dir / ".config.json.tmp"
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(composed, file, indent=2, ensure_ascii=False)
        file.write("\n")
    temp_path.replace(out_path)
    return out_path


def _remove_signapps_panel_custom_yaml(config_dir: Path) -> bool:
    """Strip SignApps panel_custom from configuration.yaml (panel is registered by the updater integration)."""
    config_file = config_dir / "configuration.yaml"
    if not config_file.exists():
        return False

    existing = config_file.read_text(encoding="utf-8")
    updated = existing

    if _MANAGED_PANEL_START in updated and _MANAGED_PANEL_END in updated:
        managed_pattern = re.compile(
            rf"{re.escape(_MANAGED_PANEL_START)}.*?{re.escape(_MANAGED_PANEL_END)}\n?",
            flags=re.DOTALL,
        )
        updated = managed_pattern.sub("", updated)

    legacy_patterns = [
        # Dict style (invalid): panel_custom:\n  signapps-react:\n    title: ...
        r"panel_custom:\s*\n\s+signapps-react:\s*\n(?:[ \t]+.+\n)*",
        # Malformed list (invalid): panel_custom:\n  - signapps-react:\n    title: ...
        r"panel_custom:\s*\n\s+-\s+signapps-react:\s*\n(?:[ \t]+.+\n)*",
        # Valid list style we previously wrote — remove so YAML is not duplicated
        r"panel_custom:\s*\n\s+-\s+name:\s+signapps-react\s*\n(?:[ \t]+.+\n)*",
    ]
    for pattern in legacy_patterns:
        updated = re.sub(pattern, "", updated, flags=re.MULTILINE)

    updated = re.sub(r"\n{3,}", "\n\n", updated).rstrip() + "\n"
    if updated == existing:
        return False

    config_file.write_text(updated, encoding="utf-8")
    _LOGGER.info(
        "Removed panel_custom SignApps block from configuration.yaml "
        "(updater registers the panel at runtime)"
    )
    return True


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


def _load_lovelace_resources_from_storage(config_dir: Path) -> list[tuple[str, str]]:
    """URLs and types from UI storage (e.g. HACS). Preserved when switching to YAML Lovelace."""
    path = config_dir / ".storage" / "lovelace_resources"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, TypeError):
        return []

    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        data = raw if isinstance(raw, dict) else {}

    items = data.get("items")
    if not isinstance(items, list):
        return []

    out: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        rtype = item.get("type")
        if not isinstance(rtype, str) or rtype not in ("module", "js", "css"):
            rtype = "module"
        out.append((url.strip(), rtype))
    return out


def _parse_resource_entries_from_managed_block(text: str) -> list[tuple[str, str]]:
    """Extract url + type from a previous updater-managed lovelace block."""
    if _MANAGED_LOVELACE_START not in text or _MANAGED_LOVELACE_END not in text:
        return []
    try:
        start = text.index(_MANAGED_LOVELACE_START)
        end = text.index(_MANAGED_LOVELACE_END)
    except ValueError:
        return []
    chunk = text[start:end]
    entries: list[tuple[str, str]] = []
    lines = chunk.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*-\s*url:\s*(.+)\s*$", line)
        if not m:
            continue
        url = m.group(1).strip().strip("\"'")
        if not url:
            continue
        rtype = "module"
        if i + 1 < len(lines):
            tm = re.match(r"^\s*type:\s*(\S+)", lines[i + 1])
            if tm:
                candidate = tm.group(1).strip().strip("\"'")
                if candidate in ("module", "js", "css"):
                    rtype = candidate
        entries.append((url, rtype))
    return entries


def _merge_lovelace_resource_urls(
    config_dir: Path,
    *,
    existing_configuration_text: str,
    signapps_urls: list[str],
) -> list[tuple[str, str]]:
    """Union SignApps, HACS/UI storage, and prior managed block. Per-url type prefers storage."""
    by_url: dict[str, str] = {}
    for url in signapps_urls:
        u = url.strip()
        if u:
            by_url.setdefault(u, "module")
    for url, rtype in _parse_resource_entries_from_managed_block(existing_configuration_text):
        by_url.setdefault(url, rtype)
    for url, rtype in _load_lovelace_resources_from_storage(config_dir):
        by_url[url] = rtype
    return sorted(by_url.items(), key=lambda x: x[0])


def _url_prefers_frontend_module(url: str, rtype: str) -> bool:
    """True if this resource should load via frontend.extra_module_url (not Lovelace resources)."""
    if rtype == "css":
        return False
    low = url.lower()
    return any(s in low for s in FRONTEND_MODULE_URL_SUBSTRINGS)


def _partition_lovelace_and_frontend_module_urls(
    merged: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[str]]:
    """Split merged resources: frontend-module URLs vs Lovelace-only (avoid double-loading)."""
    lovelace: list[tuple[str, str]] = []
    frontend: list[str] = []
    seen_fe: set[str] = set()
    for url, rtype in merged:
        if _url_prefers_frontend_module(url, rtype):
            if url not in seen_fe:
                seen_fe.add(url)
                frontend.append(url)
        else:
            lovelace.append((url, rtype))
    return lovelace, frontend


def _is_root_level_yaml_line(line: str) -> bool:
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#"):
        return False
    return not line.startswith(" ") and not line.startswith("\t")


def _parse_yaml_list_item_scalar(line: str) -> str | None:
    m = re.match(r"^\s*-\s+(.+?)\s*$", line)
    if not m:
        return None
    val = m.group(1).strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1]
    return val or None


def _merge_frontend_extra_module_urls(config_text: str, urls_to_add: list[str]) -> str:
    """Merge URLs into root `frontend.extra_module_url` without touching other keys (line-based).

    HACS only registers plugins in ``.storage/lovelace_resources``; it does not write
    ``frontend.extra_module_url``. We mirror matching URLs here so card-mod-style modules
    load globally like a manual HA config.
    """
    if not urls_to_add:
        return config_text

    newline = "\r\n" if "\r\n" in config_text else "\n"
    lines = config_text.splitlines()

    def join_body(body: list[str]) -> str:
        if not body:
            return ""
        out = newline.join(body)
        if config_text.endswith(newline) or (config_text and config_text[-1] in "\n\r"):
            return out + newline
        return out

    try:
        fe_idx: int | None = None
        for i, line in enumerate(lines):
            if _is_root_level_yaml_line(line) and re.match(r"^frontend:\s*", line.strip()):
                fe_idx = i
                break

        to_merge = list(urls_to_add)
        if fe_idx is None:
            tail = [
                "",
                "# SignApps updater: frontend modules (e.g. card-mod) merged from Lovelace resources.",
                "frontend:",
                "  extra_module_url:",
            ]
            for u in to_merge:
                tail.append(f"    - {u}")
            return join_body(lines + tail)

        block_end = len(lines)
        for j in range(fe_idx + 1, len(lines)):
            if _is_root_level_yaml_line(lines[j]):
                block_end = j
                break

        em_line: int | None = None
        em_indent = 0
        for j in range(fe_idx + 1, block_end):
            m = re.match(r"^(\s*)extra_module_url:\s*$", lines[j])
            if m:
                em_line = j
                em_indent = len(m.group(1))
                break

        if em_line is None:
            insert: list[str] = [
                "  extra_module_url:",
            ]
            for u in to_merge:
                insert.append(f"    - {u}")
            new_lines = lines[: fe_idx + 1] + insert + lines[fe_idx + 1 :]
            return join_body(new_lines)

        existing: list[str] = []
        k = em_line + 1
        while k < block_end:
            raw = lines[k]
            if not raw.strip() or raw.lstrip().startswith("#"):
                k += 1
                continue
            indent = len(raw) - len(raw.lstrip())
            if indent <= em_indent:
                break
            item = _parse_yaml_list_item_scalar(raw)
            if item is not None:
                existing.append(item)
            k += 1

        merged_list: list[str] = []
        seen: set[str] = set()
        for u in existing + to_merge:
            u = u.strip()
            if not u or u in seen:
                continue
            seen.add(u)
            merged_list.append(u)

        pad = " " * (em_indent + 2)
        new_block_lines = [lines[em_line]]
        for u in merged_list:
            new_block_lines.append(f"{pad}- {u}")

        new_lines = lines[:em_line] + new_block_lines + lines[k:]
        return join_body(new_lines)
    except Exception as err:
        _LOGGER.warning("Could not merge frontend.extra_module_url: %s", err)
        return config_text


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

    merged_resources = _merge_lovelace_resource_urls(
        config_dir,
        existing_configuration_text=existing,
        signapps_urls=resources,
    )
    lovelace_resources, frontend_module_urls = _partition_lovelace_and_frontend_module_urls(
        merged_resources
    )

    resources_block = ""
    if lovelace_resources:
        resources_block = "  resources:\n" + "".join(
            f"    - url: {url}\n      type: {rtype}\n" for url, rtype in lovelace_resources
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

    if frontend_module_urls:
        updated = _merge_frontend_extra_module_urls(updated, frontend_module_urls)
        _LOGGER.info(
            "Merged %d URL(s) into frontend.extra_module_url (not duplicated in Lovelace resources)",
            len(frontend_module_urls),
        )

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

        react_composed = await hass.async_add_executor_job(
            _compose_react_dashboard_config,
            preset_path,
            customer_path,
        )
        react_generated_path = await hass.async_add_executor_job(
            _write_generated_react_dashboard,
            config_dir,
            customer_key,
            react_composed,
        )
        await hass.async_add_executor_job(_sync_react_config_to_www, config_dir, react_composed)
        react_enabled = bool(react_composed.get("enabled"))
        await hass.async_add_executor_job(_remove_signapps_panel_custom_yaml, config_dir)
        if react_enabled:
            from .panel import async_setup_signapps_panel

            await async_setup_signapps_panel(hass)
        _LOGGER.info(
            "Dashboard wiring applied: generated=%s slug=%s react=%s enabled=%s",
            generated_path,
            dashboard_slug,
            react_generated_path,
            react_enabled,
        )
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
