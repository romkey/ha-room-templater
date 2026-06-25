"""Unit tests for gen_templates helpers and YAML structure."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
import yaml

import gen_templates as gt
from inline_jinja import render_entity_template
from tests.ha_mock import HAMock, build_jinja_env, render_template_string


@pytest.fixture
def config():
    return gt.Config(
        ha_url="http://test.local:8123",
        ha_token="token",
        exclude_areas=frozenset({"Garage"}),
        output_path=gt.Path("out.yaml"),
        dashboard_output_path=gt.Path("dash.yaml"),
        name_prefix="canonical_",
    )


class TestNamingHelpers:
    def test_slug(self):
        assert gt.slug("Back Yard") == "back_yard"
        assert gt.slug("John's Office") == "john_s_office"

    def test_slug_matches_home_assistant_for_unicode_apostrophes(self):
        """Regression for areas like "John's Office" (curly U+2019). HA's
        slugify strips smart quotes entirely (entity_id ends up as
        `sensor.johns_office_...`); a homegrown isalnum-or-underscore
        translation would emit `john_s_office_...` and the dashboard would
        reference a sensor HA never created. This test pins us to HA's
        actual behavior via python-slugify."""
        assert gt.slug("John\u2019s Office") == "johns_office"
        assert gt.slug("Mike's Office") == "mike_s_office"  # straight ASCII '

    def test_slug_collapses_whitespace_and_punctuation(self):
        assert gt.slug("Back  Yard   ") == "back_yard"
        assert gt.slug("Salle à manger") == "salle_a_manger"

    def test_template_name(self):
        assert (
            gt.template_name("Kitchen", "canonical_", "Temperature")
            == "Kitchen canonical_temperature"
        )

    def test_template_unique_id(self):
        assert (
            gt.template_unique_id("Kitchen", "canonical_", "Temperature")
            == "kitchen_canonical_temperature"
        )

    def test_entity_id(self):
        assert (
            gt.entity_id("sensor", "Kitchen", "canonical_", "Temperature")
            == "sensor.kitchen_canonical_temperature"
        )

    def test_entity_id_prefers_registered_value_over_slug(self):
        """When HA has already registered an entity under a different
        (e.g. stuck) entity_id, the dashboard must reference HA's actual
        entity_id, not whatever our slug would have produced. Regression:
        an area renamed from curly-quote `John’s Office` to straight-quote
        `John's Office` keeps the original `sensor.johns_office_*` entity_id
        in HA's registry; predicting `sensor.john_s_office_*` from the new
        name results in dashboard rows that show 'Unavailable'."""
        eid_map = {
            "John's Office canonical_temperature": "sensor.johns_office_canonical_temperature",
        }
        assert (
            gt.entity_id(
                "sensor", "John's Office", "canonical_", "Temperature",
                entity_id_map=eid_map,
            )
            == "sensor.johns_office_canonical_temperature"
        )

    def test_entity_id_falls_back_to_slug_when_unregistered(self):
        """First-run case: HA has no entity registered yet, so /api/states
        knows nothing about it. Fall back to the python-slugify prediction
        so the generator still emits a usable dashboard."""
        assert (
            gt.entity_id(
                "sensor", "Back Room", "canonical_", "Temperature",
                entity_id_map={},
            )
            == "sensor.back_room_canonical_temperature"
        )

    def test_entity_id_ignores_map_entry_of_wrong_kind(self):
        """If a `binary_sensor.foo` and a `sensor.foo` share a friendly name
        (unlikely but possible), only the matching domain is honored."""
        eid_map = {"Kitchen canonical_occupancy": "sensor.somethingelse"}
        assert (
            gt.entity_id(
                "binary_sensor", "Kitchen", "canonical_", "Occupancy",
                entity_id_map=eid_map,
            )
            == "binary_sensor.kitchen_canonical_occupancy"
        )

    def test_companion_entity_ids_are_lowercase(self):
        assert (
            gt.entity_id("sensor", "Kitchen", "canonical_", "Lights On")
            == "sensor.kitchen_canonical_lights_on"
        )
        assert (
            gt.entity_id("sensor", "Kitchen", "canonical_", "Temperature_all_sensors")
            == "sensor.kitchen_canonical_temperature_all_sensors"
        )

    def test_jinja_str_escaping(self):
        assert gt.jinja_str("John's Office") == "'John\\'s Office'"


class TestIgnoreLabelInGeneratedTemplates:
    """Macros and inlined output use the single label ignore_canonical."""

    @pytest.mark.parametrize(
        "macro,args",
        [
            ("avg", ["temperature", "°C"]),
            ("lights_on", []),
            ("any_unlocked", []),
            ("ignored_lights_in_area", []),
        ],
    )
    def test_inlined_macros_reference_ignore_canonical_only(self, macro: str, args: list[str]):
        body = render_entity_template(macro, "Kitchen", args)
        assert "ignore_canonical" in body
        assert "ignore_canonical_" not in body


class TestMakeTemplateEntry:
    def test_structure(self, config):
        entry = gt.make_template_entry(
            "Kitchen",
            config,
            "Temperature",
            "avg",
            ["temperature", "°C"],
            availability_args=["temperature", "°C"],
            device_class="temperature",
            unit="°C",
        )
        assert entry["name"] == "Kitchen canonical_temperature"
        assert entry["unique_id"] == "kitchen_canonical_temperature"
        assert entry["device_class"] == "temperature"
        assert entry["unit_of_measurement"] == "°C"
        assert "state" in entry
        assert "availability" in entry
        assert "from 'room.jinja'" not in entry["state"]
        assert "_hc_track" not in entry["state"]
        assert "_hc_track" not in entry["availability"]
        assert entry["state"].startswith(("{%-", "{{"))

    def test_state_is_single_line(self, config):
        entry = gt.make_template_entry(
            "Kitchen",
            config,
            "Temperature",
            "avg",
            ["temperature", "°C"],
            availability_args=["temperature", "°C"],
        )
        assert "\n" not in entry["state"]
        assert "\n" not in entry["availability"]

    def test_default_entity_id_matches_unique_id(self, config):
        """`default_entity_id` is what makes HA pick the entity_id we expect on
        first registration. It must be the unique_id, prefixed with the right
        domain. See homeassistant.components.template.schemas:
        make_template_entity_base_schema accepts `default_entity_id`."""
        entry = gt.make_template_entry(
            "Kitchen",
            config,
            "Temperature",
            "avg",
            ["temperature", "°C"],
            kind="sensor",
        )
        assert entry["default_entity_id"] == f"sensor.{entry['unique_id']}"

    def test_default_entity_id_uses_binary_sensor_domain(self, config):
        entry = gt.make_template_entry(
            "Kitchen",
            config,
            "Occupancy",
            "any_on",
            ["occupancy"],
            kind="binary_sensor",
        )
        assert entry["default_entity_id"] == f"binary_sensor.{entry['unique_id']}"

    def test_default_entity_id_survives_unicode_apostrophe(self, config):
        """Areas with smart apostrophes (e.g. "John's Office", U+2019) are the
        case this exists to fix: HA's slugifier collapses them in ways that
        differ from naive translation, and the registry pins entity_ids on
        first registration. Setting `default_entity_id` ourselves makes the
        slug deterministic and matches our `unique_id` / dashboard refs."""
        entry = gt.make_template_entry(
            "John\u2019s Office",
            config,
            "Temperature",
            "avg",
            ["temperature", "°C"],
            kind="sensor",
        )
        assert entry["unique_id"] == "johns_office_canonical_temperature"
        assert entry["default_entity_id"] == "sensor.johns_office_canonical_temperature"

    def test_companion_entries_include_default_entity_id(self, config):
        out: dict = {"sensor": [], "binary_sensor": []}
        temp = next(s for s in gt.SENSORS if s["name"] == "Temperature")
        gt.append_companion_sensors(out, "Kitchen", config, temp)
        for entry in out["sensor"]:
            assert entry["default_entity_id"] == f"sensor.{entry['unique_id']}"


class TestDashboard:
    def test_build_room_dashboard_entities(self):
        main = gt.DashboardEntity("sensor.kitchen_canonical_temperature", "canonical_temperature")
        all_s = gt.DashboardEntity("sensor.kitchen_canonical_temperature_all_sensors", "All")
        active = gt.DashboardEntity("sensor.kitchen_canonical_temperature_active_sensors", "Active")
        ignored = gt.DashboardEntity(
            "sensor.kitchen_canonical_temperature_ignored_sensors", "Ignored"
        )
        m = gt.DashboardMeasurement("Temperature", main, all_s, active, ignored)
        rows = gt.build_room_dashboard_entities([m])
        assert rows[0]["type"] == "conditional"
        assert rows[0]["row"]["entity"] == main.entity_id
        assert any(r.get("type") == "section" and r.get("label") == "Sources" for r in rows)

    def test_build_dashboard_view_paths(self):
        main = gt.DashboardEntity("sensor.kitchen_canonical_temperature", "canonical_temperature")
        all_s = gt.DashboardEntity("sensor.kitchen_canonical_temperature_all_sensors", "All")
        active = gt.DashboardEntity("sensor.kitchen_canonical_temperature_active_sensors", "Active")
        ignored = gt.DashboardEntity(
            "sensor.kitchen_canonical_temperature_ignored_sensors", "Ignored"
        )
        m = gt.DashboardMeasurement("Temperature", main, all_s, active, ignored)
        dash = gt.build_dashboard({"Kitchen": [m]})
        assert dash["views"][0]["path"] == "kitchen"

    def test_area_settings_link_row_shape(self):
        """The link row must be a `weblink` that deep-links to
        /config/areas/area/<id> and stays in the current tab so it feels
        like internal navigation rather than spawning a new browser tab."""
        row = gt.area_settings_link_row("kitchen")
        assert row == {
            "type": "weblink",
            "name": "Area settings",
            "icon": "mdi:cog",
            "url": "/config/areas/area/kitchen",
            "new_tab": False,
        }

    def test_area_settings_link_url_uses_raw_area_id(self):
        """area_id is HA's internal handle and is **not** the friendly name
        slugified — it survives renames. The URL must pass it through
        verbatim, even when it contains characters slug() would alter."""
        row = gt.area_settings_link_row("johns_office_2")
        assert row["url"] == "/config/areas/area/johns_office_2"

    def test_build_room_dashboard_entities_prepends_settings_link(self):
        main = gt.DashboardEntity("sensor.kitchen_canonical_temperature", "canonical_temperature")
        all_s = gt.DashboardEntity("sensor.kitchen_canonical_temperature_all_sensors", "All")
        active = gt.DashboardEntity("sensor.kitchen_canonical_temperature_active_sensors", "Active")
        ignored = gt.DashboardEntity(
            "sensor.kitchen_canonical_temperature_ignored_sensors", "Ignored"
        )
        m = gt.DashboardMeasurement("Temperature", main, all_s, active, ignored)
        rows = gt.build_room_dashboard_entities([m], area_id="kitchen")
        assert rows[0]["type"] == "weblink"
        assert rows[0]["url"] == "/config/areas/area/kitchen"
        assert rows[1]["type"] == "conditional"  # canonical value still comes next

    def test_build_room_dashboard_entities_omits_link_when_no_area_id(self):
        """Backward compatibility: callers that don't supply an area_id get
        the original layout (first row is the canonical value)."""
        main = gt.DashboardEntity("sensor.kitchen_canonical_temperature", "canonical_temperature")
        all_s = gt.DashboardEntity("sensor.kitchen_canonical_temperature_all_sensors", "All")
        active = gt.DashboardEntity("sensor.kitchen_canonical_temperature_active_sensors", "Active")
        ignored = gt.DashboardEntity(
            "sensor.kitchen_canonical_temperature_ignored_sensors", "Ignored"
        )
        m = gt.DashboardMeasurement("Temperature", main, all_s, active, ignored)
        rows = gt.build_room_dashboard_entities([m])
        assert not any(r.get("type") == "weblink" for r in rows)
        assert rows[0]["type"] == "conditional"

    def test_build_dashboard_threads_area_id_per_room(self):
        """Each view must receive its own room's area_id; a room missing
        from the mapping must still render (just without the link) so a
        partial fetch doesn't break the whole dashboard."""
        k_prefix = "sensor.kitchen_canonical_temperature"
        b_prefix = "sensor.bedroom_canonical_temperature"
        main_k = gt.DashboardEntity(k_prefix, "v")
        all_k = gt.DashboardEntity(f"{k_prefix}_all_sensors", "All")
        act_k = gt.DashboardEntity(f"{k_prefix}_active_sensors", "Active")
        ign_k = gt.DashboardEntity(f"{k_prefix}_ignored_sensors", "Ignored")
        main_b = gt.DashboardEntity(b_prefix, "v")
        all_b = gt.DashboardEntity(f"{b_prefix}_all_sensors", "All")
        act_b = gt.DashboardEntity(f"{b_prefix}_active_sensors", "Active")
        ign_b = gt.DashboardEntity(f"{b_prefix}_ignored_sensors", "Ignored")
        km = gt.DashboardMeasurement("Temperature", main_k, all_k, act_k, ign_k)
        bm = gt.DashboardMeasurement("Temperature", main_b, all_b, act_b, ign_b)

        dash = gt.build_dashboard(
            {"Kitchen": [km], "Bedroom": [bm]},
            area_ids={"Kitchen": "kitchen_id"},  # Bedroom intentionally missing
        )
        kitchen_rows = dash["views"][0]["cards"][0]["entities"]
        bedroom_rows = dash["views"][1]["cards"][0]["entities"]
        assert kitchen_rows[0]["type"] == "weblink"
        assert kitchen_rows[0]["url"] == "/config/areas/area/kitchen_id"
        assert not any(r.get("type") == "weblink" for r in bedroom_rows)


class TestFetchAreaIds:
    """`fetch_area_ids` returns the friendly_name -> area_id map needed to
    deep-link from each dashboard view to its area's Settings page. We mock
    HA's template API rather than going over the network."""

    @patch("gen_templates.render_template")
    def test_returns_name_to_id_mapping(self, mock_render, config):
        mock_render.return_value = (
            '[{"name": "Kitchen", "id": "kitchen"}, '
            '{"name": "John\'s Office", "id": "johns_office"}]'
        )
        result = gt.fetch_area_ids(config)
        assert result == {"Kitchen": "kitchen", "John's Office": "johns_office"}

    @patch("gen_templates.render_template")
    def test_returns_empty_dict_on_malformed_response(self, mock_render, config, capsys):
        """If HA returns something unparseable (older build, error string),
        degrade gracefully rather than crashing the whole run; the dashboard
        is still useful without the Settings link."""
        mock_render.return_value = "not json at all"
        assert gt.fetch_area_ids(config) == {}
        assert "unexpected response" in capsys.readouterr().out.lower()

    def test_source_rows_show_sources_attribute_for_debugging(self):
        """Each source row must be `type: attribute` showing the `sources`
        attribute, so the dashboard renders the actual sensor names (joined
        string) instead of just the count that lives in entity state."""
        main = gt.DashboardEntity("sensor.x_canonical_temperature", "canonical_temperature")
        all_s = gt.DashboardEntity("sensor.x_canonical_temperature_all_sensors", "All")
        active = gt.DashboardEntity("sensor.x_canonical_temperature_active_sensors", "Active")
        ignored = gt.DashboardEntity("sensor.x_canonical_temperature_ignored_sensors", "Ignored")
        mismatched = gt.DashboardEntity(
            "sensor.x_canonical_temperature_mismatched_unit_sensors", "Mismatched units"
        )
        m = gt.DashboardMeasurement("Temperature", main, all_s, active, ignored, mismatched)
        rows = gt.build_room_dashboard_entities([m])

        for source in (all_s, active, ignored, mismatched):
            wrapper = next(
                r for r in rows
                if r.get("type") == "conditional"
                and r["row"].get("entity") == source.entity_id
            )
            inner = wrapper["row"]
            assert inner["type"] == "attribute", source.entity_id
            assert inner["attribute"] == gt.SOURCES_ATTRIBUTE, source.entity_id
            assert inner["name"] == source.label, source.entity_id

    def test_sources_subsection_anchored_on_companion_not_main(self):
        """Sources subsection must be conditional on the `_all_sensors`
        companion (broad availability), not the main canonical entity. Without
        this, areas where every source is labeled `ignore_canonical` lose the
        Sources breakdown even though `_ignored_sensors` still has useful state.
        """
        main = gt.DashboardEntity("sensor.x_canonical_temperature", "canonical_temperature")
        all_s = gt.DashboardEntity("sensor.x_canonical_temperature_all_sensors", "All")
        active = gt.DashboardEntity("sensor.x_canonical_temperature_active_sensors", "Active")
        ignored = gt.DashboardEntity("sensor.x_canonical_temperature_ignored_sensors", "Ignored")
        mismatched = gt.DashboardEntity(
            "sensor.x_canonical_temperature_mismatched_unit_sensors", "Mismatched units"
        )
        m = gt.DashboardMeasurement("Temperature", main, all_s, active, ignored, mismatched)
        rows = gt.build_room_dashboard_entities([m])

        main_row = next(
            r for r in rows
            if r.get("type") == "conditional"
            and r["row"].get("entity") == main.entity_id
        )
        assert main_row["conditions"][0]["entity"] == main.entity_id, (
            "Main canonical row must still hide when the main is unavailable"
        )

        source_rows = [
            r for r in rows
            if r.get("type") == "conditional"
            and "row" in r
            and (
                r["row"].get("entity") in {all_s.entity_id, active.entity_id, ignored.entity_id, mismatched.entity_id}
                or r["row"].get("label") == m.section_label
            )
        ]
        assert source_rows, "no source rows found"
        for r in source_rows:
            assert r["conditions"][0]["entity"] == all_s.entity_id, (
                f"row {r['row']} must be anchored on `_all_sensors`, not main"
            )


class TestFetchEntityIdMap:
    """`fetch_entity_id_map` builds the friendly_name -> entity_id lookup
    that the dashboard generator consults instead of guessing entity_ids."""

    @patch("gen_templates.requests.get")
    def test_returns_mapping_from_states_response(self, mock_get, config):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {
                    "entity_id": "sensor.johns_office_canonical_temperature",
                    "attributes": {"friendly_name": "John's Office canonical_temperature"},
                },
                {
                    "entity_id": "sensor.back_room_thermostat",
                    "attributes": {"friendly_name": "Back Room Thermostat"},
                },
                {
                    "entity_id": "sensor.no_friendly_name",
                    "attributes": {},
                },
            ],
        )
        mock_get.return_value.raise_for_status = MagicMock()
        result = gt.fetch_entity_id_map(config)
        assert (
            result["John's Office canonical_temperature"]
            == "sensor.johns_office_canonical_temperature"
        )
        assert result["Back Room Thermostat"] == "sensor.back_room_thermostat"
        assert "" not in result  # entities without friendly_name are skipped

    @patch("gen_templates.requests.get")
    def test_returns_empty_dict_on_request_failure(self, mock_get, config):
        import requests as _r

        mock_get.side_effect = _r.RequestException("network")
        assert gt.fetch_entity_id_map(config) == {}


class TestReportDashboardResolution:
    """`report_dashboard_resolution` is the user-visible check that surfaces
    mismatches between the dashboard's predicted entity_ids and HA's actual
    registry, so a smart-apostrophe or area-rename problem is obvious instead
    of leaving the user to debug "Unavailable" rows by hand.
    """

    def test_returns_zero_when_all_matched(self, capsys):
        expected = [
            ("Kitchen canonical_temperature", "sensor.kitchen_canonical_temperature"),
            ("Kitchen canonical_humidity", "sensor.kitchen_canonical_humidity"),
        ]
        registry = {
            "Kitchen canonical_temperature": "sensor.kitchen_canonical_temperature",
            "Kitchen canonical_humidity": "sensor.kitchen_canonical_humidity",
        }
        assert gt.report_dashboard_resolution(expected, registry) == 0
        out = capsys.readouterr().out
        assert "all 2 entity references verified" in out

    def test_returns_miss_count_and_lists_unresolved(self, capsys):
        expected = [
            ("Kitchen canonical_temperature", "sensor.kitchen_canonical_temperature"),
            (
                "John\u2019s Office canonical_temperature",
                "sensor.john_s_office_canonical_temperature",
            ),
        ]
        registry = {
            "Kitchen canonical_temperature": "sensor.kitchen_canonical_temperature",
            "John\u2019s Office canonical_temperature": "sensor.johns_office_canonical_temperature",
        }
        misses = gt.report_dashboard_resolution(expected, registry)
        assert misses == 1
        out = capsys.readouterr().out
        assert "1 of 2 entity refs confirmed" in out
        assert "sensor.john_s_office_canonical_temperature" in out
        assert "sensor.johns_office_canonical_temperature" in out

    def test_no_close_match_is_labeled(self, capsys):
        expected = [
            (
                "Phantom Room canonical_temperature",
                "sensor.phantom_room_canonical_temperature",
            ),
        ]
        registry = {"Kitchen canonical_humidity": "sensor.kitchen_canonical_humidity"}
        misses = gt.report_dashboard_resolution(expected, registry)
        assert misses == 1
        out = capsys.readouterr().out
        assert "no close match in HA" in out

    def test_empty_expected_returns_zero(self, capsys):
        assert gt.report_dashboard_resolution([], {}) == 0
        assert capsys.readouterr().out == ""


class TestSensorConfigIntegrity:
    def test_every_sensor_macro_has_companion_and_availability(self):
        for sensor in gt.SENSORS:
            macro = sensor["macro"]
            assert macro in gt.SENSOR_LIST_MACROS, macro
            assert macro in gt.SENSOR_IGNORED_MACROS, macro
            assert macro in gt.AVAILABILITY_MACROS, macro
            all_m, active_m = gt.SENSOR_LIST_MACROS[macro]
            assert all_m in gt.AVAILABILITY_MACROS
            assert active_m in gt.AVAILABILITY_MACROS

    def test_companion_macros_expand(self):
        for sensor in gt.SENSORS:
            macros = [
                gt.SENSOR_LIST_MACROS[sensor["macro"]][0],
                gt.SENSOR_LIST_MACROS[sensor["macro"]][1],
                gt.SENSOR_IGNORED_MACROS[sensor["macro"]],
            ]
            if sensor["macro"] in gt.NUMERIC_MACROS:
                macros.append("mismatched_unit_sensors_by_dc")
            for suffix_macro in macros:
                args = gt.sensor_macro_args(sensor, suffix_macro)
                body = render_entity_template(suffix_macro, "Kitchen", args)
                assert body
                assert "join_names" not in body

    def test_sensor_macro_args_appends_unit_for_numeric(self):
        temp = next(s for s in gt.SENSORS if s["name"] == "Temperature")
        assert gt.sensor_macro_args(temp, "avg") == ["temperature", "°C"]
        assert gt.sensor_macro_args(temp, "all_sensors_by_dc") == ["temperature"]

    def test_sensor_macro_args_appends_empty_unit_when_missing(self):
        """Numeric sensors without a canonical unit (e.g. AQI) still need the
        canonical_unit placeholder bound, otherwise HA renders the template
        with `canonical_unit` undefined and emits a warning."""
        aqi = next(s for s in gt.SENSORS if s["name"] == "AQI")
        assert aqi.get("unit") in (None, "")
        for macro in gt.MACROS_WITH_CANONICAL_UNIT:
            args = gt.sensor_macro_args(aqi, macro)
            assert args[-1] == "", (macro, args)

    def test_companion_state_is_count_not_joined_names(self, config):
        """Companion entities must emit a numeric count (HA caps state at 255
        chars). The full friendly-name list lives in the `sources` attribute
        as a comma-joined string instead."""
        out: dict = {"sensor": [], "binary_sensor": []}
        temp = next(s for s in gt.SENSORS if s["name"] == "Temperature")
        gt.append_companion_sensors(out, "Kitchen", config, temp)

        ha = HAMock()
        for i in range(40):
            ha.add_entity(
                f"sensor.kitchen_temp_{i}",
                "21.5",
                area="Kitchen",
                attributes={
                    "device_class": "temperature",
                    "unit_of_measurement": "°C",
                    "friendly_name": f"Probe {i:02d} with a comfortably long friendly name",
                },
            )
        env = build_jinja_env(ha)

        for entry in out["sensor"]:
            assert "attributes" in entry, entry["name"]
            assert gt.SOURCES_ATTRIBUTE in entry["attributes"], entry["name"]
            state = render_template_string(env, entry["state"])
            assert state.isdigit(), (entry["name"], state)
            assert len(state) <= 255

            sources = render_template_string(env, entry["attributes"][gt.SOURCES_ATTRIBUTE])
            names = [n for n in sources.split(", ") if n]
            assert len(names) == int(state), entry["name"]

    def test_companion_state_length_bounded_for_many_sources(self, config):
        """Regression: 30+ temperature sources used to push state past 255 chars
        and HA refused to update it (entity stayed at `unknown`)."""
        out: dict = {"sensor": [], "binary_sensor": []}
        temp = next(s for s in gt.SENSORS if s["name"] == "Temperature")
        gt.append_companion_sensors(out, "Back Room", config, temp)

        ha = HAMock()
        for i in range(50):
            ha.add_entity(
                f"sensor.back_room_huge_friendly_name_temperature_sensor_number_{i}",
                "21.5",
                area="Back Room",
                attributes={
                    "device_class": "temperature",
                    "unit_of_measurement": "°C",
                    "friendly_name": f"Back Room Temperature Probe Number {i:02d}",
                },
            )
        env = build_jinja_env(ha)

        for entry in out["sensor"]:
            state = render_template_string(env, entry["state"])
            assert len(state) <= 255, (entry["name"], len(state))

    def test_no_unbound_canonical_unit_in_generated_templates(self, config):
        """Every generated state/availability template must have `canonical_unit`
        substituted; a literal `canonical_unit` token means the parameter was
        never bound and HA will warn at render time."""
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
            assert "canonical_unit" not in entry["state"], sensor["name"]
            assert "canonical_unit" not in entry["availability"], sensor["name"]
            for suffix_macro in (
                *gt.SENSOR_LIST_MACROS[sensor["macro"]],
                gt.SENSOR_IGNORED_MACROS[sensor["macro"]],
                *(
                    ("mismatched_unit_sensors_by_dc",)
                    if sensor["macro"] in gt.NUMERIC_MACROS
                    else ()
                ),
            ):
                rendered = render_entity_template(
                    suffix_macro, "Kitchen", gt.sensor_macro_args(sensor, suffix_macro)
                )
                assert "canonical_unit" not in rendered, (sensor["name"], suffix_macro)


class TestGenerationFunctional:
    """Functional test: mock HA API and verify generated YAML."""

    @patch("gen_templates.fetch_areas")
    def test_generate_one_room(self, mock_areas, config, tmp_path):
        mock_areas.return_value = ["Kitchen", "Garage"]
        out = tmp_path / "rooms.yaml"
        dash = tmp_path / "dash.yaml"
        cfg = replace(config, output_path=out, dashboard_output_path=dash)

        rooms = [a for a in mock_areas.return_value if a not in cfg.exclude_areas]
        result = []
        for room in rooms:
            room_out = {"sensor": [], "binary_sensor": []}
            for sensor in gt.SENSORS:
                state_args = gt.sensor_macro_args(sensor, sensor["macro"])
                avail_args = gt.sensor_macro_args(sensor, gt.AVAILABILITY_MACROS[sensor["macro"]])
                room_out[sensor["kind"]].append(
                    gt.make_template_entry(
                        room,
                        cfg,
                        sensor["name"],
                        sensor["macro"],
                        state_args,
                        availability_args=avail_args,
                        device_class=sensor["device_class"],
                        unit=sensor["unit"],
                    )
                )
                gt.append_companion_sensors(room_out, room, cfg, sensor)
            block: dict = {}
            for kind in ("sensor", "binary_sensor"):
                if room_out[kind]:
                    block[kind] = room_out[kind]
            if block:
                result.append(block)

        out.write_text(yaml.dump(result, sort_keys=False, allow_unicode=True, width=10_000))
        parsed = yaml.safe_load(out.read_text())
        assert len(parsed) == 1  # Garage excluded
        block = parsed[0]
        assert "triggers" not in block
        sensor_kinds = [s for s in gt.SENSORS if s["kind"] == "sensor"]
        binary_kinds = [s for s in gt.SENSORS if s["kind"] == "binary_sensor"]

        def companion_count(s: dict) -> int:
            n = 3
            if s["macro"] in gt.NUMERIC_MACROS:
                n += 1
            return n

        sensor_kinds = [s for s in gt.SENSORS if s["kind"] == "sensor"]
        expected_sensors = sum(companion_count(s) for s in gt.SENSORS) + len(sensor_kinds)
        assert len(block["sensor"]) == expected_sensors
        assert len(block["binary_sensor"]) == len(binary_kinds)
        for ent in block["sensor"] + block["binary_sensor"]:
            assert "\n" not in ent["state"]
            assert "from 'room.jinja'" not in ent["state"]
            assert "_hc_track" not in ent["state"]
            assert "join_names" not in ent["state"]
