"""Unit tests for room.jinja macro logic (via mocked HA context)."""

from __future__ import annotations

from tests.ha_mock import HAMock, build_jinja_env, kitchen_fixture, render_macro


class TestNumericAggregations:
    def test_avg_excludes_canonical_ignored_and_unavailable(self, kitchen_env):
        assert render_macro(kitchen_env, "avg", "Kitchen", ["temperature", "°C"]) == "22.0"

    def test_highest(self, kitchen_env):
        assert render_macro(kitchen_env, "highest", "Kitchen", ["carbon_dioxide", "ppm"]) == "850.0"

    def test_lowest(self, kitchen_env):
        assert render_macro(kitchen_env, "lowest", "Kitchen", ["battery", "%"]) == "15.0"

    def test_total(self, kitchen_env):
        assert render_macro(kitchen_env, "total", "Kitchen", ["power", "W"]) == "150.5"

    def test_avg_empty_when_no_usable_sources(self):
        ha = HAMock()
        ha.add_entity(
            "sensor.empty_temp",
            "unavailable",
            area="Empty",
            attributes={"device_class": "temperature"},
        )
        env = build_jinja_env(ha)
        assert render_macro(env, "avg", "Empty", ["temperature", "°C"]) == ""


class TestAvailability:
    def test_available_numeric_by_dc_true(self, kitchen_env):
        assert (
            render_macro(kitchen_env, "available_numeric_by_dc", "Kitchen", ["temperature", "°C"])
            == "True"
        )

    def test_available_numeric_by_dc_false(self):
        ha = HAMock()
        ha.add_entity(
            "sensor.only_bad",
            "unavailable",
            area="Empty",
            attributes={"device_class": "temperature"},
        )
        env = build_jinja_env(ha)
        assert (
            render_macro(env, "available_numeric_by_dc", "Empty", ["temperature", "°C"]) == "False"
        )

    def test_available_lights(self, kitchen_env):
        assert render_macro(kitchen_env, "available_lights", "Kitchen", []) == "True"

    def test_available_switch_named(self, kitchen_env):
        assert (
            render_macro(kitchen_env, "available_switch_named", "Kitchen", ["vibration"]) == "True"
        )

    def test_available_locks(self, kitchen_env):
        assert render_macro(kitchen_env, "available_locks", "Kitchen", []) == "True"


class TestIgnoreCanonicalLabel:
    """One label excludes entities from every aggregate type that checks it."""

    def test_ignored_temperature_not_in_avg(self, kitchen_env):
        assert (
            render_macro(kitchen_env, "ignored_sensors_by_dc", "Kitchen", ["temperature"])
            == "Ignored Temp"
        )

    def test_ignored_light_not_in_lights_on(self, kitchen_env):
        assert render_macro(kitchen_env, "lights_on", "Kitchen", []) == "1"
        assert render_macro(kitchen_env, "ignored_lights_in_area", "Kitchen", []) == "Ignored Light"


class TestSourceLists:
    def test_all_sensors_by_dc(self, kitchen_env):
        result = render_macro(kitchen_env, "all_sensors_by_dc", "Kitchen", ["temperature"])
        # Includes non-ignored sources regardless of state (Bad Temp is unavailable but listed).
        assert result == "Temp A, Temp B, Bad Temp"

    def test_active_sensors_by_dc(self, kitchen_env):
        result = render_macro(kitchen_env, "active_sensors_by_dc", "Kitchen", ["temperature"])
        assert result == "Temp A, Temp B"

    def test_ignored_sensors_by_dc(self, kitchen_env):
        result = render_macro(kitchen_env, "ignored_sensors_by_dc", "Kitchen", ["temperature"])
        assert result == "Ignored Temp"

    def test_all_lights_in_area(self, kitchen_env):
        result = render_macro(kitchen_env, "all_lights_in_area", "Kitchen", [])
        assert result == "Ceiling, Lamp"

    def test_active_lights_in_area(self, kitchen_env):
        result = render_macro(kitchen_env, "active_lights_in_area", "Kitchen", [])
        assert result == "Ceiling"

    def test_ignored_lights_in_area(self, kitchen_env):
        result = render_macro(kitchen_env, "ignored_lights_in_area", "Kitchen", [])
        assert result == "Ignored Light"


class TestLightsAndBinary:
    def test_lights_on(self, kitchen_env):
        assert render_macro(kitchen_env, "lights_on", "Kitchen", []) == "1"

    def test_any_on_occupancy(self, kitchen_env):
        assert render_macro(kitchen_env, "any_on", "Kitchen", ["occupancy"]) == "True"

    def test_any_on_door_closed(self, kitchen_env):
        assert render_macro(kitchen_env, "any_on", "Kitchen", ["door"]) == "False"

    def test_active_binary_by_dc(self, kitchen_env):
        result = render_macro(kitchen_env, "active_binary_by_dc", "Kitchen", ["occupancy"])
        assert result == "Occupancy"


class TestSwitchesAndLocks:
    def test_any_switch_named(self, kitchen_env):
        assert render_macro(kitchen_env, "any_switch_named", "Kitchen", ["vibration"]) == "True"

    def test_any_switch_named_ignores_labeled(self):
        ha = kitchen_fixture()
        for eid in ha.entities:
            if "vibration" in eid and "ignored" not in eid:
                ha.entities[eid].state = "off"
        env = build_jinja_env(ha)
        assert render_macro(env, "any_switch_named", "Kitchen", ["vibration"]) == "False"

    def test_active_switches_named(self, kitchen_env):
        result = render_macro(kitchen_env, "active_switches_named", "Kitchen", ["vibration"])
        assert result == "Vibration Sensor"

    def test_ignored_switches_named(self, kitchen_env):
        result = render_macro(kitchen_env, "ignored_switches_named", "Kitchen", ["vibration"])
        assert result == "Ignored Vibration"

    def test_any_unlocked(self, kitchen_env):
        assert render_macro(kitchen_env, "any_unlocked", "Kitchen", []) == "True"

    def test_active_unlocked_locks(self, kitchen_env):
        result = render_macro(kitchen_env, "active_unlocked_locks", "Kitchen", [])
        assert result == "Back Door"


class TestCanonicalExclusion:
    """Generated canonical entities must never feed back into aggregates."""

    def test_canonical_temperature_not_in_average(self, kitchen_env):
        # Canonical reports 22.0 but only 20+24 sources exist -> avg 22.0 not 21.3
        avg = render_macro(kitchen_env, "avg", "Kitchen", ["temperature", "°C"])
        assert avg == "22.0"
        all_names = render_macro(kitchen_env, "all_sensors_by_dc", "Kitchen", ["temperature"])
        assert "canonical" not in all_names.lower()
