"""Unit conversion for canonical numeric sensors."""

from __future__ import annotations

import pytest

from tests.ha_mock import HAMock, build_jinja_env, render_macro, render_template_string


class TestConvertToCanonical:
    @pytest.mark.parametrize(
        "value,source,canonical,expected",
        [
            (77, "°F", "°C", 25.0),
            (25, "°C", "°F", 77.0),
            (77, "°F", "°F", 77),
            (22, "", "°C", 22),
            (0.1, "kW", "W", 100.0),
            (500, "mW", "W", 0.5),
            (1500, "Wh", "kWh", 1.5),
            (2, "MWh", "kWh", 2000.0),
            (1, "ppm", "ppb", 1000.0),
            (2, "mg/m³", "µg/m³", 2000.0),
        ],
    )
    def test_supported_pairs(self, value, source, canonical, expected):
        ha = HAMock()
        env = build_jinja_env(ha)
        tpl = (
            "{% from 'room.jinja' import convert_to_canonical %}"
            "{{ convert_to_canonical(" + str(value) + ", '" + source + "', '" + canonical + "') }}"
        )
        result = render_template_string(env, tpl)
        assert float(result) == pytest.approx(expected)

    def test_unknown_pair_returns_empty(self):
        ha = HAMock()
        env = build_jinja_env(ha)
        tpl = (
            "{% from 'room.jinja' import convert_to_canonical %}"
            "{{ convert_to_canonical(10, 'ppb', 'µg/m³') }}"
        )
        result = render_template_string(env, tpl)
        assert result in ("", "None")


class TestTemperatureFahrenheit:
    def test_single_f_source_averages_to_celsius(self):
        ha = HAMock()
        ha.add_entity(
            "sensor.room_temp_f",
            "77",
            area="Room",
            attributes={
                "device_class": "temperature",
                "unit_of_measurement": "°F",
                "friendly_name": "Thermostat",
            },
        )
        env = build_jinja_env(ha)
        assert render_macro(env, "avg", "Room", ["temperature", "°C"]) == "25.0"

    def test_mixed_f_and_c_sources(self):
        ha = HAMock()
        ha.add_entity(
            "sensor.room_temp_f",
            "77",
            area="Room",
            attributes={"device_class": "temperature", "unit_of_measurement": "°F"},
        )
        ha.add_entity(
            "sensor.room_temp_c",
            "25",
            area="Room",
            attributes={"device_class": "temperature", "unit_of_measurement": "°C"},
        )
        env = build_jinja_env(ha)
        assert render_macro(env, "avg", "Room", ["temperature", "°C"]) == "25.0"


class TestPowerUnitConversion:
    def test_kw_and_w_combined(self):
        ha = HAMock()
        ha.add_entity(
            "sensor.plug_w",
            "50",
            area="Room",
            attributes={"device_class": "power", "unit_of_measurement": "W"},
        )
        ha.add_entity(
            "sensor.plug_kw",
            "0.1",
            area="Room",
            attributes={"device_class": "power", "unit_of_measurement": "kW"},
        )
        env = build_jinja_env(ha)
        assert render_macro(env, "total", "Room", ["power", "W"]) == "150.0"


class TestMismatchedUnits:
    def test_lists_unconvertible_sources(self):
        ha = HAMock()
        ha.add_entity(
            "sensor.gas_ppb",
            "50",
            area="Room",
            attributes={
                "device_class": "nitrogen_dioxide",
                "unit_of_measurement": "ppb",
                "friendly_name": "NO2 ppb",
            },
        )
        ha.add_entity(
            "sensor.gas_ug",
            "30",
            area="Room",
            attributes={
                "device_class": "nitrogen_dioxide",
                "unit_of_measurement": "µg/m³",
                "friendly_name": "NO2 mass",
            },
        )
        env = build_jinja_env(ha)
        result = render_macro(
            env, "mismatched_unit_sensors_by_dc", "Room", ["nitrogen_dioxide", "µg/m³"]
        )
        assert result == "NO2 ppb"

    def test_highest_uses_only_convertible(self):
        ha = HAMock()
        ha.add_entity(
            "sensor.gas_ppb",
            "500",
            area="Room",
            attributes={
                "device_class": "nitrogen_dioxide",
                "unit_of_measurement": "ppb",
            },
        )
        ha.add_entity(
            "sensor.gas_ug",
            "30",
            area="Room",
            attributes={
                "device_class": "nitrogen_dioxide",
                "unit_of_measurement": "µg/m³",
            },
        )
        env = build_jinja_env(ha)
        assert render_macro(env, "highest", "Room", ["nitrogen_dioxide", "µg/m³"]) == "30.0"

    def test_all_unconvertible_unavailable(self):
        ha = HAMock()
        ha.add_entity(
            "sensor.gas_ppb",
            "50",
            area="Room",
            attributes={
                "device_class": "nitrogen_dioxide",
                "unit_of_measurement": "ppb",
                "friendly_name": "NO2 ppb",
            },
        )
        env = build_jinja_env(ha)
        assert (
            render_macro(env, "available_numeric_by_dc", "Room", ["nitrogen_dioxide", "µg/m³"])
            == "False"
        )
        assert render_macro(env, "avg", "Room", ["nitrogen_dioxide", "µg/m³"]) == ""
        assert (
            render_macro(
                env,
                "mismatched_unit_sensors_by_dc",
                "Room",
                ["nitrogen_dioxide", "µg/m³"],
            )
            == "NO2 ppb"
        )


class TestKitchenFixtureStillWorks:
    def test_celsius_sources_without_unit_attr(self, kitchen_env):
        assert render_macro(kitchen_env, "avg", "Kitchen", ["temperature", "°C"]) == "22.0"

    def test_available_numeric(self, kitchen_env):
        assert (
            render_macro(kitchen_env, "available_numeric_by_dc", "Kitchen", ["temperature", "°C"])
            == "True"
        )

    def test_mismatched_empty_when_all_convertible(self, kitchen_env):
        assert (
            render_macro(
                kitchen_env,
                "mismatched_unit_sensors_by_dc",
                "Kitchen",
                ["temperature", "°C"],
            )
            == ""
        )
