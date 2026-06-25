"""Unit tests for inline_jinja macro expansion."""

from __future__ import annotations

import pytest

from inline_jinja import (
    expand_macro,
    load_macros,
    render_count_template,
    render_entity_template,
)
from tests.ha_mock import build_jinja_env, render_template_string  # noqa: F401


class TestLoadMacros:
    def test_loads_all_expected_macros(self):
        macros = load_macros()
        expected = {
            "avg",
            "highest",
            "lowest",
            "total",
            "lights_on",
            "any_on",
            "any_switch_named",
            "any_unlocked",
            "join_names",
            "available_numeric_by_dc",
            "convert_to_canonical",
            "mismatched_unit_sensors_by_dc",
            "all_sensors_by_dc",
            "active_sensors_by_dc",
            "ignored_sensors_by_dc",
        }
        assert expected.issubset(set(macros))

    def test_macro_params(self):
        macros = load_macros()
        assert macros["avg"][0] == ["area", "dc", "canonical_unit"]
        assert macros["available_numeric_by_dc"][0] == ["area", "dc", "canonical_unit"]
        assert macros["any_switch_named"][0] == ["area", "substr"]
        assert macros["lights_on"][0] == ["area"]


class TestExpandMacro:
    def test_binds_area_and_device_class(self):
        body = expand_macro("avg", "Kitchen", ["temperature", "°C"])
        assert "area_entities('Kitchen')" in body
        assert "'temperature'" in body
        assert "'°C'" in body
        assert "area" not in body.split("area_entities")[0]  # param replaced

    def test_no_import_from_room_jinja(self):
        body = render_entity_template("avg", "Kitchen", ["temperature"])
        assert "from 'room.jinja'" not in body
        assert "import avg" not in body

    def test_inlines_join_names(self):
        body = render_entity_template("all_sensors_by_dc", "Kitchen", ["temperature"])
        assert "join_names" not in body
        assert "state_attr(eid, 'friendly_name')" in body

    def test_state_based_template_has_no_hc_track(self):
        body = render_entity_template("avg", "Back Yard", ["humidity"])
        assert "_hc_track" not in body
        assert body.startswith("{%-")

    def test_escapes_quotes_in_area_name(self):
        body = render_entity_template("avg", "John's Office", ["temperature"])
        assert "area_entities('John\\'s Office')" in body

    def test_unknown_macro_raises(self):
        with pytest.raises(KeyError, match="Unknown macro"):
            expand_macro("not_a_macro", "Kitchen", [])


class TestMacroCoverage:
    """Every macro referenced by gen_templates must expand without join_names."""

    @pytest.mark.parametrize(
        "macro,args",
        [
            ("avg", ["temperature", "°C"]),
            ("highest", ["carbon_dioxide", "ppm"]),
            ("lowest", ["battery", "%"]),
            ("total", ["power", "W"]),
            ("lights_on", []),
            ("any_on", ["occupancy"]),
            ("any_switch_named", ["vibration"]),
            ("any_unlocked", []),
            ("all_sensors_by_dc", ["temperature"]),
            ("active_sensors_by_dc", ["temperature"]),
            ("ignored_sensors_by_dc", ["temperature"]),
            ("all_lights_in_area", []),
            ("active_lights_in_area", []),
            ("ignored_lights_in_area", []),
            ("all_binary_by_dc", ["occupancy"]),
            ("active_binary_by_dc", ["occupancy"]),
            ("ignored_binary_by_dc", ["occupancy"]),
            ("all_switches_named", ["vibration"]),
            ("active_switches_named", ["vibration"]),
            ("ignored_switches_named", ["vibration"]),
            ("all_locks_in_area", []),
            ("active_unlocked_locks", []),
            ("ignored_locks_in_area", []),
            ("available_numeric_by_dc", ["temperature", "°C"]),
            ("mismatched_unit_sensors_by_dc", ["temperature", "°C"]),
            ("available_sources_by_dc", ["temperature"]),
            ("available_type_by_dc", ["temperature"]),
            ("available_lights", []),
            ("available_lights_type", []),
            ("available_switch_named", ["vibration"]),
            ("available_switch_type", ["vibration"]),
            ("available_locks", []),
            ("available_locks_type", []),
        ],
    )
    def test_macro_expands(self, macro: str, args: list[str]):
        body = render_entity_template(macro, "Kitchen", args)
        assert body
        assert "join_names" not in body
        assert "{% from" not in body


class TestCountAndListRenderers:
    """`render_count_template` / `render_list_template` derive companion
    templates from each list macro without needing duplicate macros in
    room.jinja."""

    LIST_MACROS = [
        ("all_sensors_by_dc", ["temperature"]),
        ("active_sensors_by_dc", ["temperature"]),
        ("ignored_sensors_by_dc", ["temperature"]),
        ("mismatched_unit_sensors_by_dc", ["temperature", "°C"]),
        ("all_lights_in_area", []),
        ("active_lights_in_area", []),
        ("ignored_lights_in_area", []),
        ("all_binary_by_dc", ["occupancy"]),
        ("active_binary_by_dc", ["occupancy"]),
        ("ignored_binary_by_dc", ["occupancy"]),
        ("all_switches_named", ["vibration"]),
        ("active_switches_named", ["vibration"]),
        ("ignored_switches_named", ["vibration"]),
        ("all_locks_in_area", []),
        ("active_unlocked_locks", []),
        ("ignored_locks_in_area", []),
    ]

    @pytest.mark.parametrize("macro,args", LIST_MACROS)
    def test_count_template_has_no_join_names(self, macro, args):
        body = render_count_template(macro, "Kitchen", args)
        assert "join_names" not in body
        assert "| length" in body

    @pytest.mark.parametrize("macro,args", LIST_MACROS)
    def test_count_matches_join_names_length_against_kitchen(self, kitchen_env, macro, args):
        names_body = render_entity_template(macro, "Kitchen", args)
        count_body = render_count_template(macro, "Kitchen", args)

        names = render_template_string(kitchen_env, names_body)
        count = render_template_string(kitchen_env, count_body)

        assert count.isdigit()
        expected = [n for n in names.split(", ") if n]
        assert int(count) == len(expected)
