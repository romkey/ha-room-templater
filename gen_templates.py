#!/usr/bin/env python3
"""
Generate per-room template sensors for Home Assistant.

Connects to your HA instance, discovers areas, and writes a YAML file of
template sensors you can include from configuration.yaml. Emits every
(area, measurement) pair so new devices work automatically; availability
templates mark entities unavailable when no usable non-ignored sources exist.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv
from slugify import slugify as _slugify_unicode

from inline_jinja import render_count_template, render_entity_template

NUMERIC_MACROS = frozenset({"avg", "lowest", "highest", "total"})
MACROS_WITH_CANONICAL_UNIT = NUMERIC_MACROS | frozenset(
    {"available_numeric_by_dc", "mismatched_unit_sensors_by_dc"}
)

# Each entry produces one template entity per area (when a source sensor
# exists). `macro` must be defined in room.jinja. `args` is everything
# after the area name in the macro call.
SENSORS: list[dict] = [
    # environmental
    {
        "kind": "sensor",
        "name": "Temperature",
        "macro": "avg",
        "args": ["temperature"],
        "device_class": "temperature",
        "unit": "°C",
    },
    {
        "kind": "sensor",
        "name": "Humidity",
        "macro": "avg",
        "args": ["humidity"],
        "device_class": "humidity",
        "unit": "%",
    },
    {
        "kind": "sensor",
        "name": "Illuminance",
        "macro": "avg",
        "args": ["illuminance"],
        "device_class": "illuminance",
        "unit": "lx",
    },
    {
        "kind": "sensor",
        "name": "Sound",
        "macro": "avg",
        "args": ["sound_pressure"],
        "device_class": "sound_pressure",
        "unit": "dB",
    },
    # gases
    {
        "kind": "sensor",
        "name": "CO2",
        "macro": "highest",
        "args": ["carbon_dioxide"],
        "device_class": "carbon_dioxide",
        "unit": "ppm",
    },
    {
        "kind": "sensor",
        "name": "CO",
        "macro": "highest",
        "args": ["carbon_monoxide"],
        "device_class": "carbon_monoxide",
        "unit": "ppm",
    },
    {
        "kind": "sensor",
        "name": "NO2",
        "macro": "highest",
        "args": ["nitrogen_dioxide"],
        "device_class": "nitrogen_dioxide",
        "unit": "µg/m³",
    },
    {
        "kind": "sensor",
        "name": "NO",
        "macro": "highest",
        "args": ["nitrogen_monoxide"],
        "device_class": "nitrogen_monoxide",
        "unit": "µg/m³",
    },
    {
        "kind": "sensor",
        "name": "N2O",
        "macro": "highest",
        "args": ["nitrous_oxide"],
        "device_class": "nitrous_oxide",
        "unit": "µg/m³",
    },
    {
        "kind": "sensor",
        "name": "Ozone",
        "macro": "highest",
        "args": ["ozone"],
        "device_class": "ozone",
        "unit": "µg/m³",
    },
    {
        "kind": "sensor",
        "name": "SO2",
        "macro": "highest",
        "args": ["sulphur_dioxide"],
        "device_class": "sulphur_dioxide",
        "unit": "µg/m³",
    },
    {
        "kind": "sensor",
        "name": "VOC",
        "macro": "highest",
        "args": ["volatile_organic_compounds_parts"],
        "device_class": "volatile_organic_compounds_parts",
        "unit": "ppb",
    },
    {
        "kind": "sensor",
        "name": "VOC Mass",
        "macro": "highest",
        "args": ["volatile_organic_compounds"],
        "device_class": "volatile_organic_compounds",
        "unit": "µg/m³",
    },
    # particulates
    {
        "kind": "sensor",
        "name": "PM1",
        "macro": "highest",
        "args": ["pm1"],
        "device_class": "pm1",
        "unit": "µg/m³",
    },
    {
        "kind": "sensor",
        "name": "PM2.5",
        "macro": "highest",
        "args": ["pm25"],
        "device_class": "pm25",
        "unit": "µg/m³",
    },
    {
        "kind": "sensor",
        "name": "PM4",
        "macro": "highest",
        "args": ["pm4"],
        "device_class": "pm4",
        "unit": "µg/m³",
    },
    {
        "kind": "sensor",
        "name": "PM10",
        "macro": "highest",
        "args": ["pm10"],
        "device_class": "pm10",
        "unit": "µg/m³",
    },
    {
        "kind": "sensor",
        "name": "AQI",
        "macro": "highest",
        "args": ["aqi"],
        "device_class": "aqi",
        "unit": None,
    },
    # power / energy
    {
        "kind": "sensor",
        "name": "Power",
        "macro": "total",
        "args": ["power"],
        "device_class": "power",
        "unit": "W",
    },
    {
        "kind": "sensor",
        "name": "Energy",
        "macro": "total",
        "args": ["energy"],
        "device_class": "energy",
        "unit": "kWh",
    },
    # diagnostic
    {
        "kind": "sensor",
        "name": "Min Battery",
        "macro": "lowest",
        "args": ["battery"],
        "device_class": "battery",
        "unit": "%",
    },
    {
        "kind": "sensor",
        "name": "Lights On",
        "macro": "lights_on",
        "args": [],
        "device_class": None,
        "unit": None,
    },
    # binary
    {
        "kind": "binary_sensor",
        "name": "Occupancy",
        "macro": "any_on",
        "args": ["occupancy"],
        "device_class": "occupancy",
        "unit": None,
    },
    {
        "kind": "binary_sensor",
        "name": "Door Open",
        "macro": "any_on",
        "args": ["door"],
        "device_class": "door",
        "unit": None,
    },
    {
        "kind": "binary_sensor",
        "name": "Moisture",
        "macro": "any_on",
        "args": ["moisture"],
        "device_class": "moisture",
        "unit": None,
    },
    {
        "kind": "binary_sensor",
        "name": "Vibration",
        "macro": "any_switch_named",
        "args": ["vibration"],
        "device_class": "vibration",
        "unit": None,
    },
    {
        "kind": "binary_sensor",
        "name": "Unlocked",
        "macro": "any_unlocked",
        "args": [],
        "device_class": "lock",
        "unit": None,
    },
]

# Companion text sensors: {main_macro: (all_sensors_macro, active_sensors_macro)}
SENSOR_LIST_MACROS: dict[str, tuple[str, str]] = {
    "avg": ("all_sensors_by_dc", "active_sensors_by_dc"),
    "highest": ("all_sensors_by_dc", "active_sensors_by_dc"),
    "lowest": ("all_sensors_by_dc", "active_sensors_by_dc"),
    "total": ("all_sensors_by_dc", "active_sensors_by_dc"),
    "lights_on": ("all_lights_in_area", "active_lights_in_area"),
    "any_on": ("all_binary_by_dc", "active_binary_by_dc"),
    "any_switch_named": ("all_switches_named", "active_switches_named"),
    "any_unlocked": ("all_locks_in_area", "active_unlocked_locks"),
}

SUFFIX_ALL_SENSORS = "_all_sensors"
SUFFIX_ACTIVE_SENSORS = "_active_sensors"
SUFFIX_IGNORED_SENSORS = "_ignored_sensors"
SUFFIX_MISMATCHED_UNIT_SENSORS = "_mismatched_unit_sensors"

# Attribute exposed on every companion entity holding the full friendly-name
# list. State for these entities is the count (HA caps state at 255 chars).
SOURCES_ATTRIBUTE = "sources"

SENSOR_IGNORED_MACROS: dict[str, str] = {
    "avg": "ignored_sensors_by_dc",
    "highest": "ignored_sensors_by_dc",
    "lowest": "ignored_sensors_by_dc",
    "total": "ignored_sensors_by_dc",
    "lights_on": "ignored_lights_in_area",
    "any_on": "ignored_binary_by_dc",
    "any_switch_named": "ignored_switches_named",
    "any_unlocked": "ignored_locks_in_area",
}

# Availability templates paired with state macros (see room.jinja).
#
# Companion list entities (`_all_sensors`, `_active_sensors`, `_ignored_sensors`,
# `_mismatched_unit_sensors`) intentionally use the broader `*_type` availability
# variant (any entity of that device_class / domain exists in the area) rather
# than `*_sources` (any non-ignored source). That way a room where every source
# is labeled `ignore_canonical` still publishes companion counts (and the
# dashboard still shows the Sources breakdown) instead of going unavailable.
# Main canonical entities continue to use the strict availability template so
# they go unavailable when no usable value can be computed.
AVAILABILITY_MACROS: dict[str, str] = {
    "avg": "available_numeric_by_dc",
    "highest": "available_numeric_by_dc",
    "lowest": "available_numeric_by_dc",
    "total": "available_numeric_by_dc",
    "all_sensors_by_dc": "available_type_by_dc",
    "active_sensors_by_dc": "available_type_by_dc",
    "ignored_sensors_by_dc": "available_type_by_dc",
    "mismatched_unit_sensors_by_dc": "available_type_by_dc",
    "lights_on": "available_lights",
    "all_lights_in_area": "available_lights_type",
    "active_lights_in_area": "available_lights_type",
    "ignored_lights_in_area": "available_lights_type",
    "any_on": "available_type_by_dc",
    "all_binary_by_dc": "available_type_by_dc",
    "active_binary_by_dc": "available_type_by_dc",
    "ignored_binary_by_dc": "available_type_by_dc",
    "any_switch_named": "available_switch_named",
    "all_switches_named": "available_switch_type",
    "active_switches_named": "available_switch_type",
    "ignored_switches_named": "available_switch_type",
    "any_unlocked": "available_locks",
    "all_locks_in_area": "available_locks_type",
    "active_unlocked_locks": "available_locks_type",
    "ignored_locks_in_area": "available_locks_type",
}


@dataclass(frozen=True)
class Config:
    ha_url: str
    ha_token: str
    exclude_areas: frozenset[str]
    output_path: Path
    dashboard_output_path: Path
    name_prefix: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.ha_token}"}


@dataclass(frozen=True)
class DashboardEntity:
    entity_id: str
    label: str
    friendly_name: str = ""  # the lookup key we asked /api/states for; empty = legacy


@dataclass(frozen=True)
class DashboardMeasurement:
    """One canonical measurement and its source-list companions for the dashboard."""

    section_label: str
    main: DashboardEntity
    all_sensors: DashboardEntity
    active_sensors: DashboardEntity
    ignored_sensors: DashboardEntity
    mismatched_unit_sensors: DashboardEntity | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate per-room Home Assistant template sensors from your areas and device classes."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        help="Output YAML path (overrides OUTPUT_PATH in .env)",
    )
    parser.add_argument(
        "--exclude-areas",
        metavar="NAMES",
        help="Comma-separated area names to skip (overrides EXCLUDE_AREAS in .env)",
    )
    parser.add_argument(
        "--dashboard-output",
        metavar="PATH",
        help="Dashboard YAML path (overrides DASHBOARD_OUTPUT_PATH in .env)",
    )
    parser.add_argument(
        "--name-prefix",
        metavar="PREFIX",
        help="Prefix for template friendly names (overrides NAME_PREFIX in .env)",
    )
    return parser.parse_args()


def load_config(args: argparse.Namespace) -> Config:
    load_dotenv()
    try:
        ha_url = os.environ["HA_URL"].rstrip("/")
        ha_token = os.environ["HA_TOKEN"]
    except KeyError as missing:
        sys.exit(
            f"Missing required environment variable: {missing}\n"
            "Copy .env.example to .env and set HA_URL and HA_TOKEN."
        )

    exclude_raw = (
        args.exclude_areas
        if args.exclude_areas is not None
        else os.environ.get("EXCLUDE_AREAS", "")
    )
    exclude_areas = frozenset(a.strip() for a in exclude_raw.split(",") if a.strip())

    output_raw = (
        args.output
        if args.output is not None
        else os.environ.get("OUTPUT_PATH", "rooms_generated.yaml")
    )
    dashboard_raw = (
        args.dashboard_output
        if args.dashboard_output is not None
        else os.environ.get("DASHBOARD_OUTPUT_PATH", "rooms_dashboard.yaml")
    )
    prefix_raw = (
        args.name_prefix
        if args.name_prefix is not None
        else os.environ.get("NAME_PREFIX", "canonical_")
    )
    return Config(
        ha_url=ha_url,
        ha_token=ha_token,
        exclude_areas=exclude_areas,
        output_path=Path(output_raw),
        dashboard_output_path=Path(dashboard_raw),
        name_prefix=prefix_raw,
    )


def jinja_str(s: str) -> str:
    """Safely quote a string for embedding inside a Jinja template."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def slug(s: str) -> str:
    """Slugify a string the same way Home Assistant does.

    HA generates a template entity's `entity_id` by running its `name` through
    `python-slugify` (via `homeassistant.util.slugify`). We must use the
    identical implementation, otherwise the dashboard references entities
    under names HA never assigned — notably, Unicode "smart" apostrophes
    (e.g. "John's Office", U+2019) are stripped entirely by python-slugify
    while a naive `c.isalnum() else "_"` translation would emit `john_s_office`
    instead of `johns_office`.
    """
    return _slugify_unicode(s, separator="_")


def template_name(room: str, name_prefix: str, sensor_name: str) -> str:
    """Friendly name for a generated template entity (measurement name lowercased)."""
    return f"{room} {name_prefix}{sensor_name.lower()}"


def template_unique_id(room: str, name_prefix: str, sensor_name: str) -> str:
    """unique_id for a generated template entity."""
    return f"{slug(room)}_{slug(f'{name_prefix}{sensor_name}')}"


def entity_id(
    kind: str,
    room: str,
    name_prefix: str,
    sensor_name: str,
    *,
    entity_id_map: dict[str, str] | None = None,
) -> str:
    """Entity id HA has assigned to the template entity, or a slug-based guess.

    If `entity_id_map` (friendly_name -> entity_id, produced by
    `fetch_entity_id_map`) contains an entry whose key matches the entity's
    name and whose value is in the expected `kind.*` domain, return that;
    HA's registry is the source of truth. Otherwise fall back to
    `{kind}.{slug(name)}` so the dashboard still emits something usable on
    the first run, before HA has registered the entities.
    """
    name = template_name(room, name_prefix, sensor_name)
    if entity_id_map is not None:
        existing = entity_id_map.get(name)
        if existing and existing.startswith(f"{kind}."):
            return existing
    return f"{kind}.{slug(name)}"


def render_template(config: Config, template: str) -> str:
    """POST a Jinja template to HA's /api/template and return the result."""
    try:
        response = requests.post(
            f"{config.ha_url}/api/template",
            headers=config.headers,
            json={"template": template},
            timeout=30,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = ""
        if exc.response is not None:
            detail = exc.response.text.strip()[:500]
        code = exc.response.status_code if exc.response else "unknown"
        sys.exit(f"Home Assistant API error ({code}): {detail}")
    except requests.RequestException as exc:
        sys.exit(f"Could not reach Home Assistant at {config.ha_url}: {exc}")
    return response.text


def fetch_areas(config: Config) -> list[str]:
    raw = render_template(config, "{{ areas() | map('area_name') | list | tojson }}")
    return json.loads(raw)


def fetch_area_ids(config: Config) -> dict[str, str]:
    """Return `{area_name: area_id}` for every area in HA.

    The dashboard generator uses this to build deep links to each area's
    Settings page (`/config/areas/area/<area_id>`). `area_id` is HA's
    internal handle and is **sticky** — once an area is created, renaming
    it does not change the id, so slugifying the current name is not a
    safe substitute. If the template renders something we can't parse
    (older HA build, surprise error string) we degrade gracefully: the
    dashboard still works, it just omits the settings shortcut.
    """
    template = (
        "[{% for a in areas() %}"
        '{"name": {{ area_name(a) | tojson }}, "id": {{ a | tojson }}}'
        "{% if not loop.last %}, {% endif %}"
        "{% endfor %}]"
    )
    raw = render_template(config, template)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        print(
            "Warning: unexpected response when fetching area ids; "
            "dashboard views will omit the Settings link to each area."
        )
        return {}
    return {entry["name"]: entry["id"] for entry in parsed}


def fetch_entity_id_map(config: Config) -> dict[str, str]:
    """Return `{friendly_name: entity_id}` for entities currently registered in HA.

    The dashboard generator uses this so it references the entity_ids HA has
    actually assigned, instead of guessing them from a name slug. Slug
    prediction is fragile: HA's entity registry pins entity_id to unique_id
    on first registration, so any later name change (or any difference
    between our slugify and HA's at the time of first registration) leaves
    the entity_id "stuck" — the dashboard would point at a sensor that
    doesn't exist. Looking up the entity_id by friendly name removes that
    class of bug entirely; on the first run, before HA has registered our
    entities, the lookup misses and we fall back to the slug prediction.
    """
    try:
        response = requests.get(
            f"{config.ha_url}/api/states", headers=config.headers, timeout=30
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(
            f"Warning: could not fetch /api/states ({exc}); "
            "dashboard will use slugified entity_ids."
        )
        return {}
    mapping: dict[str, str] = {}
    for state in response.json():
        friendly = state.get("attributes", {}).get("friendly_name")
        entity_id = state.get("entity_id")
        if friendly and entity_id:
            mapping[friendly] = entity_id
    return mapping


def sensor_macro_args(sensor: dict, macro: str) -> list[str]:
    """Macro call arguments: device_class args plus canonical unit when required.

    Sensors without a canonical unit (e.g. AQI) still need the parameter bound;
    pass an empty string so the placeholder gets substituted instead of leaving
    a literal `canonical_unit` in the rendered template.
    """
    args = list(sensor["args"])
    if macro in MACROS_WITH_CANONICAL_UNIT:
        args.append(sensor.get("unit") or "")
    return args


def render_template_call(macro: str, room: str, args: list[str]) -> str:
    """Inline room.jinja macro body into entity state (no {% from %} import)."""
    return render_entity_template(macro, room, args)


def make_template_entry(
    room: str,
    config: Config,
    measurement_name: str,
    macro: str,
    args: list[str],
    *,
    kind: str = "sensor",
    availability_args: list[str] | None = None,
    device_class: str | None = None,
    unit: str | None = None,
    state_template: str | None = None,
    attributes: dict[str, str] | None = None,
) -> dict:
    """Build one template entity dict.

    `state_template` overrides the default `render_entity_template(macro, ...)`
    output for the state field; used by companion list sensors that emit a
    count (so the entity state stays within HA's 255-char limit) instead of
    the full list of names.

    `default_entity_id` pins the entity_id HA assigns on first registration
    (HA's template schema accepts this key — see
    `homeassistant.components.template.schemas.make_template_entity_base_schema`).
    Without it HA derives the entity_id by slugifying `name`, which collapses
    Unicode "smart" apostrophes ("John's Office" -> `johns_office`) in ways
    we can't always predict from the YAML side. Setting it explicitly keeps
    the generated dashboard refs and the actual entities in sync. Note that
    once HA has registered an entity, its registry pins the entity_id to the
    unique_id and ignores subsequent changes here; clean up old registry
    entries if you rename.
    """
    availability_macro = AVAILABILITY_MACROS[macro]
    avail_args = availability_args if availability_args is not None else args
    uid = template_unique_id(room, config.name_prefix, measurement_name)
    entry: dict = {
        "name": template_name(room, config.name_prefix, measurement_name),
        "unique_id": uid,
        "default_entity_id": f"{kind}.{uid}",
        "state": (
            state_template
            if state_template is not None
            else render_template_call(macro, room, args)
        ),
        "availability": render_template_call(availability_macro, room, avail_args),
    }
    if attributes:
        entry["attributes"] = dict(attributes)
    if device_class is not None:
        entry["device_class"] = device_class
    if unit:
        entry["unit_of_measurement"] = unit
    return entry


def append_companion_sensors(
    out: dict[str, list[dict]],
    room: str,
    config: Config,
    sensor: dict,
) -> None:
    """Add source-list companion sensors for one canonical measurement.

    State is the count of matched entities (a small integer so it always fits
    inside Home Assistant's 255-char state cap, even in areas with dozens of
    sensors); the full friendly-name list is exposed as the `sources`
    attribute.
    """
    all_macro, active_macro = SENSOR_LIST_MACROS[sensor["macro"]]
    ignored_macro = SENSOR_IGNORED_MACROS[sensor["macro"]]
    base_name = sensor["name"]
    companions: list[tuple[str, str]] = [
        (SUFFIX_ALL_SENSORS, all_macro),
        (SUFFIX_ACTIVE_SENSORS, active_macro),
        (SUFFIX_IGNORED_SENSORS, ignored_macro),
    ]
    if sensor["macro"] in NUMERIC_MACROS:
        companions.append((SUFFIX_MISMATCHED_UNIT_SENSORS, "mismatched_unit_sensors_by_dc"))
    for suffix, macro in companions:
        macro_args = sensor_macro_args(sensor, macro)
        avail_args = sensor_macro_args(sensor, AVAILABILITY_MACROS[macro])
        out["sensor"].append(
            make_template_entry(
                room,
                config,
                f"{base_name}{suffix}",
                macro,
                macro_args,
                kind="sensor",
                availability_args=avail_args,
                state_template=render_count_template(macro, room, macro_args),
                attributes={
                    SOURCES_ATTRIBUTE: render_entity_template(macro, room, macro_args),
                },
            )
        )


def _available_condition(entity_id: str) -> list[dict]:
    return [{"entity": entity_id, "state_not": ["unavailable", "unknown"]}]


def dashboard_entity_row(ent: DashboardEntity) -> dict:
    """Entities card row: show only when the canonical sensor is not unavailable."""
    return {
        "type": "conditional",
        "conditions": _available_condition(ent.entity_id),
        "row": {
            "entity": ent.entity_id,
            "name": ent.label,
        },
    }


def dashboard_row_when_available(anchor_entity_id: str, row: dict) -> dict:
    """Wrap a section or entity row; visible only when `anchor_entity_id` is available."""
    return {
        "type": "conditional",
        "conditions": _available_condition(anchor_entity_id),
        "row": row,
    }


def _sources_attribute_row(ent: DashboardEntity) -> dict:
    """Entities-card row that displays the `sources` attribute (joined names)
    instead of the entity state (which is the count). Lets the dashboard show
    the actual source sensors for debugging while keeping the entity state
    within Home Assistant's 255-char limit.
    """
    return {
        "type": "attribute",
        "entity": ent.entity_id,
        "attribute": SOURCES_ATTRIBUTE,
        "name": ent.label,
    }


def area_settings_link_row(area_id: str) -> dict:
    """`weblink` row that jumps to this area's Settings page in HA.

    `new_tab: false` keeps the navigation in the current tab so the link
    behaves like internal navigation instead of spawning a separate browser
    tab. The URL pattern (`/config/areas/area/<area_id>`) is the deep link
    HA exposes from Settings → Areas and Zones; it survives area renames
    because `area_id` itself is stable once assigned.
    """
    return {
        "type": "weblink",
        "name": "Area settings",
        "icon": "mdi:cog",
        "url": f"/config/areas/area/{area_id}",
        "new_tab": False,
    }


def build_room_dashboard_entities(
    measurements: list[DashboardMeasurement],
    *,
    area_id: str | None = None,
) -> list[dict]:
    """One entities card: values, then a Sources block per measurement.

    The per-measurement Sources subsection is anchored on the companion
    `_all_sensors` entity (its availability is True whenever any source of
    that device_class exists in the area, even if every source is ignored).
    The canonical value row, on the other hand, stays anchored on the main
    so it disappears when there is nothing to display.

    Source rows use `type: attribute` to render the `sources` attribute
    (a comma-joined string of friendly names) — exposing the actual sensor
    list for debugging instead of just the count that lives in entity state.

    When `area_id` is supplied, a `weblink` row deep-linking to that area's
    Settings page is prepended so users can jump straight to area
    management without hunting through the sidebar.
    """
    rows: list[dict] = []
    if area_id:
        rows.append(area_settings_link_row(area_id))
    rows.extend(dashboard_entity_row(m.main) for m in measurements)
    rows.append({"type": "section", "label": "Sources"})
    for m in measurements:
        anchor_id = m.all_sensors.entity_id
        rows.append(
            dashboard_row_when_available(
                anchor_id,
                {"type": "section", "label": m.section_label},
            )
        )
        source_entities = [m.all_sensors, m.active_sensors, m.ignored_sensors]
        if m.mismatched_unit_sensors is not None:
            source_entities.append(m.mismatched_unit_sensors)
        for ent in source_entities:
            rows.append(
                dashboard_row_when_available(anchor_id, _sources_attribute_row(ent))
            )
    return rows


def build_dashboard(
    room_measurements: dict[str, list[DashboardMeasurement]],
    *,
    area_ids: dict[str, str] | None = None,
) -> dict:
    """Lovelace YAML: one view (tab) per room with values and source breakdown.

    When `area_ids` (`{room_name: area_id}`) is supplied, each view's
    entities card starts with a `weblink` row pointing at that area's
    Settings page. Rooms missing from the mapping silently get no link.
    """
    ids = area_ids or {}
    views = []
    for room, measurements in room_measurements.items():
        views.append(
            {
                "title": room,
                "path": slug(room),
                "cards": [
                    {
                        "type": "entities",
                        "title": room,
                        "entities": build_room_dashboard_entities(
                            measurements, area_id=ids.get(room)
                        ),
                    }
                ],
            }
        )
    return {"views": views}


def report_dashboard_resolution(
    expected: list[tuple[str, str]],
    entity_id_map: dict[str, str],
) -> int:
    """Print a clear report on dashboard entity_id resolution. Returns miss count.

    For every entity_id referenced by the dashboard we check whether the
    `friendly_name` we used to look it up matched something in
    Home Assistant's `/api/states`. Matches mean the row will work. Misses
    mean the dashboard fell back to a slug guess and may point at nothing.

    For each miss we print the closest friendly_name HA does know about,
    which makes near-misses (smart-vs-straight apostrophe, trailing space,
    capitalization drift) immediately obvious — so the user can fix the
    underlying naming issue rather than chase ghost entities.
    """
    known_eids = set(entity_id_map.values())
    matched: list[tuple[str, str]] = []
    missed: list[tuple[str, str]] = []
    for friendly, eid in expected:
        if eid in known_eids:
            matched.append((friendly, eid))
        else:
            missed.append((friendly, eid))
    total = len(expected)
    if total == 0:
        return 0
    if not missed:
        print(
            f"Dashboard: all {total} entity references verified against "
            "Home Assistant's registry."
        )
        return 0
    print(
        f"\nDashboard: {len(matched)} of {total} entity refs confirmed in HA; "
        f"{len(missed)} unresolved (using slug predictions that may not exist)."
    )
    friendly_pool = list(entity_id_map.keys())
    show = missed[:10]
    print("Unresolved references (closest HA friendly_name shown when available):")
    for friendly, eid in show:
        candidates = difflib.get_close_matches(friendly, friendly_pool, n=1, cutoff=0.6)
        if candidates:
            ha_friendly = candidates[0]
            ha_eid = entity_id_map[ha_friendly]
            print(f"  - want:  {friendly!r} -> {eid}")
            print(f"    have:  {ha_friendly!r} -> {ha_eid}")
        else:
            print(f"  - want:  {friendly!r} -> {eid}    (no close match in HA)")
    if len(missed) > 10:
        print(f"  ... and {len(missed) - 10} more")
    print(
        "\nFix one of:\n"
        "  1. If `want` and `have` differ only by punctuation/case, HA has the\n"
        "     entity under a slightly different friendly_name. Rename the area\n"
        "     in HA (Settings > Areas) so its name matches, or delete the\n"
        "     entity rows in Settings > Entities so they re-register with the\n"
        "     name from rooms_generated.yaml.\n"
        "  2. If there's no close match at all, the templates haven't been\n"
        "     deployed yet. Add `template: !include rooms_generated.yaml` to\n"
        "     configuration.yaml, reload template entities (or restart HA),\n"
        "     then re-run this script."
    )
    return len(missed)


def write_dashboard(
    config: Config,
    room_measurements: dict[str, list[DashboardMeasurement]],
    *,
    area_ids: dict[str, str] | None = None,
) -> int:
    """Write the Lovelace dashboard file; returns number of views written."""
    dashboard = build_dashboard(room_measurements, area_ids=area_ids)
    header = (
        "# Generated by ha-room-templater — regenerate with gen_templates.py\n"
        "# State-based templates: re-evaluate when referenced entities or labels change.\n"
        "# State/availability use inlined Jinja (not {% from 'room.jinja' %}).\n"
        "# Register in configuration.yaml (adjust filename if you changed DASHBOARD_OUTPUT_PATH):\n"
        "#   lovelace:\n"
        "#     mode: storage\n"
        "#     dashboards:\n"
        "#       room-summary:\n"
        "#         mode: yaml\n"
        "#         title: Rooms\n"
        "#         icon: mdi:floor-plan\n"
        f"#         filename: {config.dashboard_output_path.name}\n"
    )
    body = yaml.dump(
        dashboard,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=10_000,
    )
    config.dashboard_output_path.write_text(header + body, encoding="utf-8")
    return len(dashboard["views"])


def main() -> None:
    config = load_config(parse_args())
    all_areas = fetch_areas(config)
    rooms = [a for a in all_areas if a not in config.exclude_areas]
    if config.exclude_areas:
        print(f"Skipping areas: {sorted(config.exclude_areas)}")
    if config.name_prefix:
        print(f"Template name prefix: {config.name_prefix!r}")
    print(f"Processing {len(rooms)} area(s): {rooms}")

    entity_id_map = fetch_entity_id_map(config)
    print(
        f"Fetched {len(entity_id_map)} known entity_ids from Home Assistant "
        "for dashboard cross-reference."
    )

    area_ids = fetch_area_ids(config)
    if area_ids:
        print(
            f"Fetched {len(area_ids)} area id(s) for dashboard Settings links."
        )

    result: list[dict] = []
    room_measurements: dict[str, list[DashboardMeasurement]] = {}

    for room in rooms:
        room_out: dict[str, list[dict]] = {"sensor": [], "binary_sensor": []}
        for sensor in SENSORS:
            state_args = sensor_macro_args(sensor, sensor["macro"])
            avail_args = sensor_macro_args(sensor, AVAILABILITY_MACROS[sensor["macro"]])
            room_out[sensor["kind"]].append(
                make_template_entry(
                    room,
                    config,
                    sensor["name"],
                    sensor["macro"],
                    state_args,
                    kind=sensor["kind"],
                    availability_args=avail_args,
                    device_class=sensor["device_class"],
                    unit=sensor["unit"],
                )
            )
            append_companion_sensors(room_out, room, config, sensor)
            prefix = config.name_prefix
            base = sensor["name"]

            def _dash_entity(
                kind: str, label: str, suffix: str = ""
            ) -> DashboardEntity:
                sensor_name = f"{base}{suffix}"
                return DashboardEntity(
                    entity_id=entity_id(
                        kind, room, prefix, sensor_name, entity_id_map=entity_id_map
                    ),
                    label=label,
                    friendly_name=template_name(room, prefix, sensor_name),
                )

            mismatched = None
            if sensor["macro"] in NUMERIC_MACROS:
                mismatched = _dash_entity(
                    "sensor", "Mismatched units", SUFFIX_MISMATCHED_UNIT_SENSORS
                )
            room_measurements.setdefault(room, []).append(
                DashboardMeasurement(
                    section_label=base.lower(),
                    main=_dash_entity(sensor["kind"], f"{prefix}{base.lower()}"),
                    all_sensors=_dash_entity("sensor", "All", SUFFIX_ALL_SENSORS),
                    active_sensors=_dash_entity(
                        "sensor", "Active", SUFFIX_ACTIVE_SENSORS
                    ),
                    ignored_sensors=_dash_entity(
                        "sensor", "Ignored", SUFFIX_IGNORED_SENSORS
                    ),
                    mismatched_unit_sensors=mismatched,
                )
            )
        block: dict = {}
        for kind in ("sensor", "binary_sensor"):
            if room_out[kind]:
                block[kind] = room_out[kind]
        if block:
            result.append(block)
    config.output_path.write_text(
        yaml.dump(
            result,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=10_000,
        ),
        encoding="utf-8",
    )

    emitted = sum(
        len(block[kind])
        for block in result
        for kind in ("sensor", "binary_sensor")
        if kind in block
    )
    view_count = write_dashboard(config, room_measurements, area_ids=area_ids)
    pairs = len(rooms) * len(SENSORS)
    print(
        f"\nWrote {emitted} entities to {config.output_path} "
        f"({pairs} area × measurement pairs; numeric measurements emit 5 entities each)."
    )
    print(f"Wrote {view_count} dashboard tab(s) to {config.dashboard_output_path}.")

    expected: list[tuple[str, str]] = []
    for measurements in room_measurements.values():
        for m in measurements:
            for ent in (m.main, m.all_sensors, m.active_sensors, m.ignored_sensors):
                if ent.friendly_name:
                    expected.append((ent.friendly_name, ent.entity_id))
            if m.mismatched_unit_sensors is not None and m.mismatched_unit_sensors.friendly_name:
                expected.append(
                    (
                        m.mismatched_unit_sensors.friendly_name,
                        m.mismatched_unit_sensors.entity_id,
                    )
                )
    report_dashboard_resolution(expected, entity_id_map)


if __name__ == "__main__":
    main()
