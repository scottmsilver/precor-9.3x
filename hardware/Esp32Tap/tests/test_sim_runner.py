from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
from types import ModuleType

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
SIM_DIR = PROJECT_DIR / "sim"
RUNNER_PATH = SIM_DIR / "run_simulations.py"
MANIFEST_PATH = SIM_DIR / "assertions.json"
MODEL_PATH = SIM_DIR / "models" / "behavioral.inc"
DECK_NAMES = {
    "input_protection",
    "tread_permission",
    "safety_truth_table",
    "relay_drive_release",
    "vbus_present",
    "buck_averaged",
    "uart_taps",
    "harness_supply_drop",
}
DOCKER_IMAGE_ID = (
    "sha256:6cb6c92d8ddfedc8857bec3884eb9dea6af1a28fac3524446abbc8bef4c1d0ae"
)


@pytest.fixture(scope="module")
def sim_runner() -> ModuleType:
    assert RUNNER_PATH.is_file(), "Task 5 simulation runner is missing"
    spec = importlib.util.spec_from_file_location(
        "esp32tap_sim_runner",
        RUNNER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest() -> dict[str, object]:
    assert MANIFEST_PATH.is_file(), "Task 5 assertion manifest is missing"
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_simulation_layout_has_exactly_eight_committed_decks() -> None:
    assert RUNNER_PATH.is_file()
    assert MANIFEST_PATH.is_file()
    assert MODEL_PATH.is_file()
    assert (SIM_DIR / "README.md").is_file()
    assert {
        path.stem for path in (SIM_DIR / "decks").glob("*.cir")
    } == DECK_NAMES


def test_harness_supply_manifest_limits_only_predecessor_supported_claims() -> None:
    scenario = _manifest()["scenarios"]["harness_supply_drop"]
    assertions = scenario["assertions"]

    normal = {
        f"normal_{interface}_{rail}_{branch}_a"
        for interface in ("console", "motor")
        for rail in ("power", "ground")
        for branch in ("high", "low")
    }
    open_contacts = {
        f"{interface}_{rail}_{opened}_open_survivor_a"
        for interface in ("console", "motor")
        for rail in ("power", "ground")
        for opened in ("a", "b")
    }
    doubled_contacts = {
        f"{interface}_{rail}_{doubled}_doubled_max_a"
        for interface in ("console", "motor")
        for rail in ("power", "ground")
        for doubled in ("a", "b")
    }
    assert set(assertions) == normal | open_contacts | doubled_contacts
    for name in normal:
        expected = 1.35 if name.endswith("_high_a") else 0.65
        assert assertions[name]["expected"] == expected
    for name in open_contacts:
        assert assertions[name]["expected"] == 2.0
    for name in doubled_contacts:
        assert assertions[name]["max"] <= 1.5

    unsupported = {
        entry["claim"].lower(): entry["reason"].lower()
        for entry in scenario["unsupported"]
    }
    for claim_fragment in (
        "rj45 single-open 2.0 a",
        "minimum vin",
        "source impedance",
        "ambient and thermal",
        "transient response",
        "complete installed supply-plus-return drop",
        "usb return current",
        "esd",
        "rf",
        "switching-loop",
    ):
        assert any(claim_fragment in claim for claim in unsupported)
    assert all(
        "unsupported" in reason
        or "not measured" in reason
        or "not characterized" in reason
        or "physical" in reason
        for reason in unsupported.values()
    )


def test_harness_deck_names_every_required_physical_element() -> None:
    text = (
        SIM_DIR / "decks" / "harness_supply_drop.cir"
    ).read_text(encoding="utf-8")
    upper = text.upper()
    for token in (
        "CONSOLE HARNESS",
        "MOTOR HARNESS",
        "FOUR RJ45 TERMINATIONS",
        "430450809",
        "430451010",
        "PCB COPPER",
        "PCB VIA",
        "LOCAL LOAD",
        "USB GROUND PATH",
        "SOURCE IMPEDANCE",
        "1.35 A / 0.65 A",
    ):
        assert token in upper
    assert "ASSUMPTIONS:" in upper
    assert "NON-CLAIMS:" in upper
    assert ".MEAS" in upper
    assert ".END" in upper


def test_manifest_locks_engines_repeats_and_rev_b_values() -> None:
    manifest = _manifest()

    assert manifest["schema_version"] == 1
    assert manifest["repeat_count"] == 3
    assert manifest["engines"] == {
        "host": {
            "major": 42,
            "executable": "/usr/bin/ngspice",
        },
        "docker": {
            "major": 39,
            "image": "ngspice-cached:latest",
            "image_id": DOCKER_IMAGE_ID,
        },
    }
    assert manifest["rev_b_values"] == {
        "input_nominal_v": 8.0,
        "f1_part": "1812L075/24DR",
        "f1_cold_resistance_ohm": [0.11, 0.29],
        "d1_part": "SS34",
        "d3_part": "SMBJ10A",
        "uv_divider_ohm": [150000.0, 10000.0],
        "ov_divider_ohm": [255000.0, 10000.0],
        "sense_filter_f": 1e-09,
        "supervisor_startup_max_s": 0.00045,
        "relay_part": "G6K-2F-Y-TR DC5",
        "relay_coil_nominal_ohm": 237.0,
        "relay_supply_v": 5.0,
        "relay_clamp_part": "SMAJ6.0CA",
        "relay_output_cap_f": 4.7e-06,
        "q1_base_resistance_ohm": 560.0,
        "vbus_cap_f": 1e-07,
        "vbus_discharge_ohm": 10000.0,
        "vbus_detector_part": "2N7002",
        "uart_tap_ohm": 10000.0,
        "uart_baud": 9600,
        "buck_part": "TPS54202DDCR",
        "buck_output_v": 3.3,
    }


def test_manifest_contains_expected_truth_table_rows() -> None:
    manifest = _manifest()
    rows = manifest["truth_table"]

    assert len(rows) == 16
    assert len(
        {
            (
                row["rail_3v3"],
                row["tread_ok"],
                row["relay_cmd"],
                row["tx_enable"],
            )
            for row in rows
        }
    ) == 16
    for row in rows:
        rail = bool(row["rail_3v3"])
        tread = bool(row["tread_ok"])
        assert bool(row["relay_gate"]) == (
            rail and tread and bool(row["relay_cmd"])
        )
        assert bool(row["tx_gate"]) == (
            rail and tread and bool(row["tx_enable"])
        )
        assert row["relay_measure"].startswith("relay_gate_row_")
        assert row["tx_measure"].startswith("tx_gate_row_")


def test_manifest_has_limits_and_explicit_unsupported_claims() -> None:
    scenarios = _manifest()["scenarios"]

    assert set(scenarios) == DECK_NAMES
    unsupported_text = []
    for name, scenario in scenarios.items():
        assert scenario["deck"] == f"decks/{name}.cir"
        assert scenario["assertions"], name
        for measure, limit in scenario["assertions"].items():
            assert limit["unit"]
            assert limit["abs_tolerance"] >= 0
            assert limit["rel_tolerance"] >= 0
            assert any(
                key in limit
                for key in (
                    "min",
                    "max",
                    "min_exclusive",
                    "max_exclusive",
                    "expected",
                )
            ), measure
        assert scenario["unsupported"], name
        unsupported_text.extend(
            item["claim"].lower() + " " + item["reason"].lower()
            for item in scenario["unsupported"]
        )

    joined = "\n".join(unsupported_text)
    for required in (
        "ptc thermal trip",
        "pulse beyond",
        "contact motion",
        "loop margin",
        "switch-node ripple",
        "emi",
        "vendor-model startup",
        "usb eye",
        "rf",
        "real treadmill",
    ):
        assert required in joined

    unplug = scenarios["vbus_present"]["assertions"][
        "unplug_worst_indication_s"
    ]
    assert unplug["max_exclusive"] == 0.003
    assert "max" not in unplug

    relative_startup = scenarios["tread_permission"]["assertions"][
        "tread_after_3v3_s"
    ]
    assert relative_startup["min"] >= 0.0003

    u5_startup = scenarios["tread_permission"]["assertions"]["u5_enable_s"]
    assert u5_startup["min"] >= 0.0014
    assert u5_startup["max"] <= 0.0016
    ov_disable = scenarios["tread_permission"]["assertions"]["ov_disable_s"]
    assert ov_disable["min"] >= 18e-6

    protection = scenarios["input_protection"]["assertions"]
    for pulse in ("safe16", "safe20"):
        high_bv = protection[f"{pulse}_high_bv_vin_peak_v"]
        assert high_bv["max"] <= 17.0

    relay = scenarios["relay_drive_release"]["assertions"]
    assert relay["conservative_base_current_a"]["min"] >= 0.0017
    assert relay["conservative_coil_voltage_v"]["min"] >= 4.0
    assert relay["conservative_q1_vce_v"]["max"] <= 0.7
    assert relay["conservative_forced_beta"]["max"] <= 10.0


def test_every_deck_documents_assumptions_and_nonclaims() -> None:
    for name in DECK_NAMES:
        text = (SIM_DIR / "decks" / f"{name}.cir").read_text(
            encoding="utf-8"
        )
        upper = text.upper()
        assert ".INCLUDE MODELS/BEHAVIORAL.INC" in upper
        assert "ASSUMPTIONS:" in upper
        assert "NON-CLAIMS:" in upper
        assert ".MEAS" in upper
        assert ".END" in upper

    buck = (SIM_DIR / "decks" / "buck_averaged.cir").read_text(
        encoding="utf-8"
    )
    assert "AVERAGED BEHAVIORAL MODEL" in buck
    for forbidden_claim in (
        "assert loop margin",
        "assert switch-node ripple",
        "assert emi",
        "assert vendor-model startup",
    ):
        assert forbidden_claim not in buck.lower()

    relay = (SIM_DIR / "decks" / "relay_drive_release.cir").read_text(
        encoding="utf-8"
    )
    assert (
        "d4_peak_current_a MAX par('-i(VLO_L_CLAMP_SENSE)')"
        in relay
    )

    vbus = (SIM_DIR / "decks" / "vbus_present.cir").read_text(
        encoding="utf-8"
    )
    for slow_hotplug_corner in (
        "VHP_SLOW_SRC hp_slow_src 0 4.4",
        "CHP_SLOW vbus_hp_slow 0 110n",
        "XHP_SLOW_DETECT vbus_hp_slow rail_hp_slow "
        "vbus_present_n_hp_slow ESP_VBUS_DETECTOR_2V5",
    ):
        assert slow_hotplug_corner in vbus


def test_shared_models_document_selected_parts_and_bounds() -> None:
    text = MODEL_PATH.read_text(encoding="utf-8")

    for required in (
        "1812L075/24DR",
        "0.11",
        "0.29",
        "SS34",
        "SMBJ10A",
        "SMBJ10A_HIGH",
        "SMAJ6.0CA",
        "G6K-2F-Y-TR DC5",
        "237",
        "2N7002",
        "TPS3700",
        "TPS70950",
    ):
        assert required in text
    assert "thermal" in text.lower()
    assert "not modeled" in text.lower()


def test_parse_measure_log_accepts_ngspice_numeric_formats(
    sim_runner: ModuleType,
) -> None:
    log = """\
Circuit: fixture
vin_peak = 8.125000e+00
release_time = 1.25m targ= 1.500000e-03 trig= 2.500000e-04
gate_state = -0.000000e+00
"""

    assert sim_runner.parse_measure_log(
        log,
        {"vin_peak", "release_time", "gate_state"},
    ) == {
        "vin_peak": 8.125,
        "release_time": 0.00125,
        "gate_state": -0.0,
    }


@pytest.mark.parametrize(
    ("log", "message"),
    [
        ("one = 1.0\n", "missing.*two"),
        ("one = 1.0\none = 1.0\ntwo = 2.0\n", "duplicate.*one"),
        ("one = nan\ntwo = 2.0\n", "non-finite.*one"),
        ("one = inf\ntwo = 2.0\n", "non-finite.*one"),
        ("one = nonsense\ntwo = 2.0\n", "malformed.*one"),
        (
            "one = 1.0 trailing-garbage\ntwo = 2.0\n",
            "malformed.*one",
        ),
        ("one = 1.0 unexpected=2\ntwo = 2.0\n", "malformed.*one"),
        ("one = 1.0\none =\ntwo = 2.0\n", "duplicate.*one"),
    ],
)
def test_parse_measure_log_rejects_bad_fixture_logs(
    sim_runner: ModuleType,
    log: str,
    message: str,
) -> None:
    with pytest.raises(sim_runner.SimulationError, match=message):
        sim_runner.parse_measure_log(log, {"one", "two"})


def test_parse_measure_log_does_not_accept_substring_names(
    sim_runner: ModuleType,
) -> None:
    with pytest.raises(sim_runner.SimulationError, match="missing.*vin"):
        sim_runner.parse_measure_log("not_vin = 8.0\n", {"vin"})


@pytest.mark.parametrize(
    ("limit", "value", "should_pass"),
    [
        ({"min": 1.0}, 1.0, True),
        ({"min": 1.0}, 0.999, False),
        ({"max": 2.0}, 2.0, True),
        ({"max": 2.0}, 2.001, False),
        ({"min_exclusive": 1.0}, 1.0, False),
        ({"min_exclusive": 1.0}, 1.001, True),
        ({"max_exclusive": 3e-3}, 3e-3, False),
        ({"max_exclusive": 3e-3}, 2.999e-3, True),
        ({"expected": 3.3, "assert_tolerance": 0.01}, 3.305, True),
        ({"expected": 3.3, "assert_tolerance": 0.01}, 3.311, False),
    ],
)
def test_runner_enforces_inclusive_exclusive_and_expected_limits(
    sim_runner: ModuleType,
    limit: dict[str, float],
    value: float,
    should_pass: bool,
) -> None:
    full_limit = {
        "unit": "fixture",
        "abs_tolerance": 1e-12,
        "rel_tolerance": 1e-9,
        **limit,
    }
    if should_pass:
        sim_runner.assert_measure_in_range("fixture", value, full_limit)
    else:
        with pytest.raises(sim_runner.SimulationError, match="fixture"):
            sim_runner.assert_measure_in_range(
                "fixture",
                value,
                full_limit,
            )


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("** ngspice-42 : Circuit level simulation program", 42),
        ("ngspice compiled release 39", 39),
    ],
)
def test_engine_major_parser_accepts_required_version_banners(
    sim_runner: ModuleType,
    output: str,
    expected: int,
) -> None:
    assert sim_runner.parse_engine_major(output) == expected


@pytest.mark.parametrize("major", [38, 40, 41, 43, 44])
def test_engine_major_validation_rejects_wrong_versions(
    sim_runner: ModuleType,
    major: int,
) -> None:
    with pytest.raises(
        sim_runner.SimulationError,
        match=f"expected.*42.*got.*{major}",
    ):
        sim_runner.require_engine_major(
            "host",
            f"ngspice-{major}",
            42,
        )


def test_docker_engine_major_validation_rejects_non_39(
    sim_runner: ModuleType,
) -> None:
    with pytest.raises(
        sim_runner.SimulationError,
        match="expected.*39.*got.*42",
    ):
        sim_runner.require_engine_major(
            "docker",
            "ngspice-42",
            39,
        )


def test_engine_major_parser_rejects_missing_or_ambiguous_banner(
    sim_runner: ModuleType,
) -> None:
    with pytest.raises(sim_runner.SimulationError, match="version"):
        sim_runner.parse_engine_major("no simulator banner")
    with pytest.raises(sim_runner.SimulationError, match="ambiguous"):
        sim_runner.parse_engine_major("ngspice-39\nngspice-42\n")


def test_cross_engine_comparison_uses_per_measure_tolerances(
    sim_runner: ModuleType,
) -> None:
    specs = {
        "tight": {
            "unit": "V",
            "min": 0.0,
            "abs_tolerance": 1e-6,
            "rel_tolerance": 1e-6,
        },
        "relative": {
            "unit": "A",
            "min": 0.0,
            "abs_tolerance": 1e-9,
            "rel_tolerance": 1e-3,
        },
    }
    sim_runner.compare_engine_measures(
        "fixture",
        {"tight": 1.0, "relative": 100.0},
        {"tight": 1.0000005, "relative": 100.05},
        specs,
    )

    with pytest.raises(
        sim_runner.SimulationError,
        match="cross-engine.*tight",
    ):
        sim_runner.compare_engine_measures(
            "fixture",
            {"tight": 1.0, "relative": 100.0},
            {"tight": 1.00001, "relative": 100.05},
            specs,
        )


def test_cross_engine_comparison_rejects_nonfinite_and_key_mismatch(
    sim_runner: ModuleType,
) -> None:
    specs = {
        "one": {
            "unit": "V",
            "min": 0.0,
            "abs_tolerance": 1e-6,
            "rel_tolerance": 1e-6,
        }
    }
    with pytest.raises(sim_runner.SimulationError, match="measure set"):
        sim_runner.compare_engine_measures(
            "fixture",
            {"one": 1.0},
            {"other": 1.0},
            specs,
        )
    with pytest.raises(sim_runner.SimulationError, match="non-finite"):
        sim_runner.compare_engine_measures(
            "fixture",
            {"one": math.inf},
            {"one": 1.0},
            specs,
        )


def test_unsupported_entries_can_only_render_unsupported(
    sim_runner: ModuleType,
) -> None:
    rendered = sim_runner.render_unsupported(
        "fixture",
        [
            {
                "claim": "pulse beyond published envelope",
                "reason": "outside selected protection ratings",
            }
        ],
    )

    assert rendered == [
        "fixture: pulse beyond published envelope: UNSUPPORTED "
        "(outside selected protection ratings)"
    ]
    assert all("PASS" not in line for line in rendered)


def test_manifest_loader_rejects_missing_duplicate_or_unsupported_measures(
    sim_runner: ModuleType,
    tmp_path: Path,
) -> None:
    valid = _manifest()

    missing = json.loads(json.dumps(valid))
    missing["scenarios"]["uart_taps"]["assertions"] = {}
    missing_path = tmp_path / "missing.json"
    missing_path.write_text(json.dumps(missing), encoding="utf-8")
    with pytest.raises(sim_runner.SimulationError, match="assertions"):
        sim_runner.load_manifest(missing_path)

    duplicate = json.loads(json.dumps(valid))
    duplicate["scenarios"]["uart_taps"]["unsupported"].append(
        {
            "claim": duplicate["scenarios"]["uart_taps"]["unsupported"][0][
                "claim"
            ],
            "reason": "duplicate fixture",
        }
    )
    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_text(json.dumps(duplicate), encoding="utf-8")
    with pytest.raises(sim_runner.SimulationError, match="duplicate"):
        sim_runner.load_manifest(duplicate_path)

    overlap = json.loads(json.dumps(valid))
    overlap["scenarios"]["uart_taps"]["unsupported"].append(
        {
            "claim": next(
                iter(
                    overlap["scenarios"]["uart_taps"]["assertions"]
                )
            ),
            "reason": "numeric assertion must not be unsupported",
        }
    )
    overlap_path = tmp_path / "overlap.json"
    overlap_path.write_text(json.dumps(overlap), encoding="utf-8")
    with pytest.raises(sim_runner.SimulationError, match="overlap"):
        sim_runner.load_manifest(overlap_path)


def test_manifest_loader_rejects_truth_table_assertion_drift(
    sim_runner: ModuleType,
    tmp_path: Path,
) -> None:
    drift = json.loads(json.dumps(_manifest()))
    drift["scenarios"]["safety_truth_table"]["assertions"][
        "relay_gate_row_14_v"
    ]["expected"] = 0.0
    path = tmp_path / "truth-drift.json"
    path.write_text(json.dumps(drift), encoding="utf-8")

    with pytest.raises(sim_runner.SimulationError, match="truth table"):
        sim_runner.load_manifest(path)


def test_manifest_loader_rejects_nonbinary_truth_table_outputs(
    sim_runner: ModuleType,
    tmp_path: Path,
) -> None:
    invalid = json.loads(json.dumps(_manifest()))
    invalid["truth_table"][15]["relay_gate"] = "asserted"
    path = tmp_path / "truth-nonbinary.json"
    path.write_text(json.dumps(invalid), encoding="utf-8")

    with pytest.raises(sim_runner.SimulationError, match="relay_gate.*0 or 1"):
        sim_runner.load_manifest(path)


def test_runner_commands_are_offline_read_only_and_mount_only_sim_tree(
    sim_runner: ModuleType,
) -> None:
    host, docker = sim_runner.build_engine_commands(
        SIM_DIR,
        Path("/usr/bin/ngspice"),
        DOCKER_IMAGE_ID,
        "decks/vbus_present.cir",
    )

    assert host == [
        "/usr/bin/ngspice",
        "-n",
        "-b",
        "decks/vbus_present.cir",
    ]
    assert docker[:7] == [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
    ]
    assert "/tmp" in docker
    assert "-w" in docker and "/sim" in docker
    assert docker[-5:] == [
        DOCKER_IMAGE_ID,
        "ngspice",
        "-n",
        "-b",
        "decks/vbus_present.cir",
    ]
    mounts = [
        docker[index + 1]
        for index, value in enumerate(docker[:-1])
        if value == "--mount"
    ]
    assert mounts == [
        f"type=bind,src={SIM_DIR.resolve()},dst=/sim,readonly"
    ]
