"""Functional tests: generated template strings must match room.jinja macro output."""

from __future__ import annotations

import pytest

import gen_templates as gt
from inline_jinja import render_entity_template
from tests.ha_mock import HAMock, build_jinja_env, render_macro, render_template_string


@pytest.fixture
def config():
    return gt.Config(
        ha_url="http://test.local:8123",
        ha_token="token",
        exclude_areas=frozenset(),
        output_path=gt.Path("out.yaml"),
        dashboard_output_path=gt.Path("dash.yaml"),
        name_prefix="canonical_",
    )


def _assert_inlined_matches_macro(env, macro: str, room: str, args: list[str]) -> None:
    """Inlined generator output must equal {% from %} macro call."""
    macro_result = render_macro(env, macro, room, args)
    inlined = render_template_string(env, render_entity_template(macro, room, args))
    assert inlined == macro_result, (
        f"{macro}({room}, {args}): macro={macro_result!r} inlined={inlined!r}"
    )


class TestInlinedMatchesMacros:
    """Every macro used by the generator must behave identically when inlined."""

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
            ("available_numeric_by_dc", ["temperature", "°C"]),
            ("mismatched_unit_sensors_by_dc", ["temperature", "°C"]),
            ("all_sensors_by_dc", ["temperature"]),
            ("active_sensors_by_dc", ["temperature"]),
            ("ignored_sensors_by_dc", ["temperature"]),
            ("all_lights_in_area", []),
            ("active_lights_in_area", []),
            ("ignored_lights_in_area", []),
            ("all_binary_by_dc", ["occupancy"]),
            ("active_binary_by_dc", ["occupancy"]),
            ("all_switches_named", ["vibration"]),
            ("active_switches_named", ["vibration"]),
            ("active_unlocked_locks", []),
        ],
    )
    def test_kitchen_fixture(self, kitchen_env, macro: str, args: list[str]):
        _assert_inlined_matches_macro(kitchen_env, macro, "Kitchen", args)


class TestGeneratedEntityTemplates:
    """make_template_entry output evaluates correctly against mock HA."""

    @pytest.mark.parametrize(
        "name,macro,args,expected_state,expected_available",
        [
            ("Temperature", "avg", ["temperature", "°C"], "22.0", "True"),
            ("CO2", "highest", ["carbon_dioxide", "ppm"], "850.0", "True"),
            ("Min Battery", "lowest", ["battery", "%"], "15.0", "True"),
            ("Power", "total", ["power", "W"], "150.5", "True"),
            ("Lights On", "lights_on", [], "1", "True"),
            ("Occupancy", "any_on", ["occupancy"], "True", "True"),
            ("Door Open", "any_on", ["door"], "False", "True"),
            ("Vibration", "any_switch_named", ["vibration"], "True", "True"),
            ("Unlocked", "any_unlocked", [], "True", "True"),
        ],
    )
    def test_canonical_sensors(
        self,
        kitchen_env,
        config,
        name,
        macro,
        args,
        expected_state,
        expected_available,
    ):
        entry = gt.make_template_entry(
            "Kitchen",
            config,
            name,
            macro,
            args,
            device_class=None,
            unit=None,
        )
        assert render_template_string(kitchen_env, entry["state"]) == expected_state
        assert render_template_string(kitchen_env, entry["availability"]) == expected_available

    def test_companion_all_sensors(self, kitchen_env, config):
        entry = gt.make_template_entry(
            "Kitchen",
            config,
            "Temperature_all_sensors",
            "all_sensors_by_dc",
            ["temperature"],
        )
        assert render_template_string(kitchen_env, entry["state"]) == "Temp A, Temp B, Bad Temp"

    def test_companion_active_sensors(self, kitchen_env, config):
        entry = gt.make_template_entry(
            "Kitchen",
            config,
            "Temperature_active_sensors",
            "active_sensors_by_dc",
            ["temperature"],
        )
        assert render_template_string(kitchen_env, entry["state"]) == "Temp A, Temp B"

    def test_companion_ignored_sensors(self, kitchen_env, config):
        entry = gt.make_template_entry(
            "Kitchen",
            config,
            "Temperature_ignored_sensors",
            "ignored_sensors_by_dc",
            ["temperature"],
        )
        assert render_template_string(kitchen_env, entry["state"]) == "Ignored Temp"


class TestCompanionStaysAvailableWhenAllIgnored:
    """When every source for a measurement is labeled `ignore_canonical`, the
    main canonical entity correctly goes unavailable (nothing to display), but
    the companion list entities must stay available with state=0 / sources=[]
    (or, for `_ignored_sensors`, the count and list of ignored entities) so the
    dashboard can still show the Sources breakdown."""

    def _back_room_all_ignored(self):
        ha = HAMock()
        for i in range(4):
            ha.add_entity(
                f"sensor.back_room_temp_{i}",
                "20",
                area="Back Room",
                attributes={
                    "device_class": "temperature",
                    "unit_of_measurement": "°C",
                    "friendly_name": f"Probe {i}",
                },
                labels=["ignore_canonical"],
            )
        return ha

    def test_main_unavailable(self, config):
        ha = self._back_room_all_ignored()
        env = build_jinja_env(ha)
        entry = gt.make_template_entry(
            "Back Room",
            config,
            "Temperature",
            "avg",
            ["temperature", "°C"],
            availability_args=["temperature", "°C"],
            unit="°C",
        )
        assert render_template_string(env, entry["availability"]) == "False"
        assert render_template_string(env, entry["state"]) == ""

    def test_companions_available_with_correct_counts(self, config):
        ha = self._back_room_all_ignored()
        env = build_jinja_env(ha)
        out: dict = {"sensor": [], "binary_sensor": []}
        temp = next(s for s in gt.SENSORS if s["name"] == "Temperature")
        gt.append_companion_sensors(out, "Back Room", config, temp)

        by_uid = {e["unique_id"]: e for e in out["sensor"]}

        expectations = {
            "back_room_canonical_temperature_all_sensors": ("0", ""),
            "back_room_canonical_temperature_active_sensors": ("0", ""),
            "back_room_canonical_temperature_ignored_sensors": (
                "4",
                "Probe 0, Probe 1, Probe 2, Probe 3",
            ),
            "back_room_canonical_temperature_mismatched_unit_sensors": ("0", ""),
        }
        for uid, (expected_state, expected_sources) in expectations.items():
            entry = by_uid[uid]
            assert render_template_string(env, entry["availability"]) == "True", uid
            assert render_template_string(env, entry["state"]) == expected_state, uid
            sources = render_template_string(env, entry["attributes"][gt.SOURCES_ATTRIBUTE])
            assert sources == expected_sources, uid


class TestAvailabilityEdgeCases:
    def test_unavailable_when_all_sources_ignored_or_bad(self, config):
        ha = HAMock()
        ha.add_entity(
            "sensor.only_ignored",
            "20",
            area="Room",
            attributes={"device_class": "temperature"},
            labels=["ignore_canonical"],
        )
        ha.add_entity(
            "sensor.only_unavail",
            "unavailable",
            area="Room",
            attributes={"device_class": "temperature"},
        )
        env = build_jinja_env(ha)
        entry = gt.make_template_entry(
            "Room",
            config,
            "Temperature",
            "avg",
            ["temperature", "°C"],
            availability_args=["temperature", "°C"],
            unit="°C",
        )
        assert render_template_string(env, entry["state"]) == ""
        assert render_template_string(env, entry["availability"]) == "False"

    def test_area_with_no_entities(self, config):
        ha = HAMock()
        env = build_jinja_env(ha)
        entry = gt.make_template_entry(
            "Empty",
            config,
            "Temperature",
            "avg",
            ["temperature", "°C"],
            availability_args=["temperature", "°C"],
            unit="°C",
        )
        assert render_template_string(env, entry["state"]) == ""
        assert render_template_string(env, entry["availability"]) == "False"


class TestAllSensorDefinitions:
    """Smoke-test every SENSORS entry through make_template_entry."""

    def test_all_sensor_templates_render_without_error(self, kitchen_env, config):
        for sensor in gt.SENSORS:
            state_args = gt.sensor_macro_args(sensor, sensor["macro"])
            avail_args = gt.sensor_macro_args(sensor, gt.AVAILABILITY_MACROS[sensor["macro"]])
            entry = gt.make_template_entry(
                "Kitchen",
                config,
                sensor["name"],
                sensor["macro"],
                state_args,
                availability_args=avail_args,
                device_class=sensor["device_class"],
                unit=sensor["unit"],
            )
            render_template_string(kitchen_env, entry["state"])
            render_template_string(kitchen_env, entry["availability"])

            all_m, active_m = gt.SENSOR_LIST_MACROS[sensor["macro"]]
            ignored_m = gt.SENSOR_IGNORED_MACROS[sensor["macro"]]
            companion_macros = [all_m, active_m, ignored_m]
            if sensor["macro"] in gt.NUMERIC_MACROS:
                companion_macros.append("mismatched_unit_sensors_by_dc")
            for macro in companion_macros:
                macro_args = gt.sensor_macro_args(sensor, macro)
                avail = gt.sensor_macro_args(sensor, gt.AVAILABILITY_MACROS[macro])
                companion = gt.make_template_entry(
                    "Kitchen",
                    config,
                    f"{sensor['name']}_companion",
                    macro,
                    macro_args,
                    availability_args=avail,
                )
                render_template_string(kitchen_env, companion["state"])
                render_template_string(kitchen_env, companion["availability"])
