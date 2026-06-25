"""Mock Home Assistant template context for local Jinja evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Entity:
    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)


class HAMock:
    """Minimal HA state registry for template tests."""

    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self.area_map: dict[str, list[str]] = {}
        self.labels: dict[str, set[str]] = {}

    def add_entity(
        self,
        entity_id: str,
        state: str,
        *,
        area: str | None = None,
        attributes: dict[str, Any] | None = None,
        labels: list[str] | None = None,
    ) -> Entity:
        ent = Entity(entity_id, state, attributes or {})
        self.entities[entity_id] = ent
        if area is not None:
            self.area_map.setdefault(area, []).append(entity_id)
        for label in labels or []:
            self.labels.setdefault(label, set()).add(entity_id)
        return ent

    def area_entities(self, area: str) -> list[str]:
        return list(self.area_map.get(area, []))

    def states(self, entity_id: str) -> str:
        return self.entities[entity_id].state

    def state_attr(self, entity_id: str, attr: str) -> Any:
        return self.entities[entity_id].attributes.get(attr)

    def label_entities(self, label: str) -> list[str]:
        return sorted(self.labels.get(label, ()))


def _float_default(value: Any, default: Any = None) -> Any:
    try:
        if value in (None, "", "unavailable", "unknown"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def build_jinja_env(ha: HAMock, *, load_room_jinja: bool = True) -> Environment:
    """Jinja environment with HA-like globals, filters, and tests."""
    loader = FileSystemLoader(str(PROJECT_ROOT)) if load_room_jinja else None
    env = Environment(
        loader=loader,
        autoescape=False,
        trim_blocks=False,
        lstrip_blocks=False,
    )

    env.globals["area_entities"] = ha.area_entities
    env.globals["states"] = ha.states
    env.globals["state_attr"] = ha.state_attr
    env.globals["label_entities"] = ha.label_entities
    env.filters["float"] = _float_default

    env.tests["is_state_attr"] = lambda eid, attr, val: ha.state_attr(eid, attr) == val
    env.tests["is_state"] = lambda eid, st: ha.states(eid) == st
    env.tests["search"] = lambda eid, pattern: re.search(pattern, eid) is not None
    env.tests["in"] = lambda eid, seq: eid in seq

    return env


def render_macro(env: Environment, macro: str, room: str, args: list[str]) -> str:
    """Render a room.jinja macro via {% from %} import."""
    arg_str = ", ".join(repr(a) for a in args)
    suffix = f", {arg_str}" if arg_str else ""
    src = f"{{% from 'room.jinja' import {macro} %}}{{{{ {macro}({room!r}{suffix}) }}}}"
    return env.from_string(src).render().strip()


def render_template_string(env: Environment, template: str) -> str:
    """Render an arbitrary template string (e.g. inlined generated state)."""
    return env.from_string(template).render().strip()


def kitchen_fixture() -> HAMock:
    """Standard Kitchen area with diverse entity types for macro tests."""
    ha = HAMock()
    ha.add_entity(
        "sensor.kitchen_temp_a",
        "20.0",
        area="Kitchen",
        attributes={"device_class": "temperature", "friendly_name": "Temp A"},
    )
    ha.add_entity(
        "sensor.kitchen_temp_b",
        "24.0",
        area="Kitchen",
        attributes={"device_class": "temperature", "friendly_name": "Temp B"},
    )
    ha.add_entity(
        "sensor.kitchen_temp_ignored",
        "99.0",
        area="Kitchen",
        attributes={"device_class": "temperature", "friendly_name": "Ignored Temp"},
        labels=["ignore_canonical"],
    )
    ha.add_entity(
        "sensor.kitchen_temp_bad",
        "unavailable",
        area="Kitchen",
        attributes={"device_class": "temperature", "friendly_name": "Bad Temp"},
    )
    ha.add_entity(
        "sensor.kitchen_canonical_temperature",
        "22.0",
        area="Kitchen",
        attributes={
            "device_class": "temperature",
            "friendly_name": "Kitchen canonical_temperature",
        },
    )
    ha.add_entity(
        "sensor.kitchen_co2_a",
        "400",
        area="Kitchen",
        attributes={"device_class": "carbon_dioxide", "friendly_name": "CO2 A"},
    )
    ha.add_entity(
        "sensor.kitchen_co2_b",
        "850",
        area="Kitchen",
        attributes={"device_class": "carbon_dioxide", "friendly_name": "CO2 B"},
    )
    ha.add_entity(
        "sensor.kitchen_power_a",
        "100",
        area="Kitchen",
        attributes={"device_class": "power", "friendly_name": "Plug A"},
    )
    ha.add_entity(
        "sensor.kitchen_power_b",
        "50.5",
        area="Kitchen",
        attributes={"device_class": "power", "friendly_name": "Plug B"},
    )
    ha.add_entity(
        "sensor.kitchen_battery_low",
        "15",
        area="Kitchen",
        attributes={"device_class": "battery", "friendly_name": "Remote Battery"},
    )
    ha.add_entity(
        "sensor.kitchen_battery_ok",
        "90",
        area="Kitchen",
        attributes={"device_class": "battery", "friendly_name": "Sensor Battery"},
    )
    ha.add_entity(
        "light.kitchen_ceiling",
        "on",
        area="Kitchen",
        attributes={"friendly_name": "Ceiling"},
    )
    ha.add_entity(
        "light.kitchen_lamp",
        "off",
        area="Kitchen",
        attributes={"friendly_name": "Lamp"},
    )
    ha.add_entity(
        "light.kitchen_ignored",
        "on",
        area="Kitchen",
        attributes={"friendly_name": "Ignored Light"},
        labels=["ignore_canonical"],
    )
    ha.add_entity(
        "binary_sensor.kitchen_occupancy",
        "on",
        area="Kitchen",
        attributes={"device_class": "occupancy", "friendly_name": "Occupancy"},
    )
    ha.add_entity(
        "binary_sensor.kitchen_door",
        "off",
        area="Kitchen",
        attributes={"device_class": "door", "friendly_name": "Pantry Door"},
    )
    ha.add_entity(
        "switch.kitchen_vibration",
        "on",
        area="Kitchen",
        attributes={"friendly_name": "Vibration Sensor"},
    )
    ha.add_entity(
        "switch.kitchen_vibration_ignored",
        "on",
        area="Kitchen",
        attributes={"friendly_name": "Ignored Vibration"},
        labels=["ignore_canonical"],
    )
    ha.add_entity(
        "lock.kitchen_door",
        "unlocked",
        area="Kitchen",
        attributes={"friendly_name": "Back Door"},
    )
    ha.add_entity(
        "lock.kitchen_garage",
        "locked",
        area="Kitchen",
        attributes={"friendly_name": "Garage Lock"},
    )
    return ha
