"""Contract tests for the self-contained Esp32Tap breadboard wizard."""

from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path

import pytest


HTML_PATH = Path(__file__).parents[1] / "bringup" / "breadboard-wizard.html"
TOP_LEVEL_KEYS = {
    "schema_version",
    "storage_key",
    "bom",
    "disconnected_pins",
    "nodes",
    "items",
    "baseline",
    "truth_table",
    "steps",
    "meter_checks",
    "photo_instruction",
}
EXPECTED_NODES = {
    "3v3": "red rail columns 1-27",
    "gnd": "blue rail columns 1-27",
    "gpio4": "a4-e4",
    "gpio5": "a6-e6",
    "gpio6": "a10-e10",
    "gpio6_sw": "a12-e12",
    "gpio7": "a15-e15",
    "gpio15": "a20-e20",
    "led15_a": "f20-j20",
    "led15_k": "f22-j22",
    "gpio21": "a25-e25",
    "led21_a": "f25-j25",
    "led21_k": "f27-j27",
    "dpdt_common_a": "off-board common A",
    "dpdt_common_b": "off-board common B",
    "dpdt_ground_a": "off-board grounded throw A",
    "dpdt_ground_b": "off-board grounded throw B",
    "dpdt_unused_a": "off-board insulated unused throw A",
    "dpdt_unused_b": "off-board insulated unused throw B",
    "devkit_3v3": "DevKit printed 3V3",
    "devkit_gnd": "DevKit printed GND",
    "devkit_gpio4": "DevKit printed GPIO4",
    "devkit_gpio5": "DevKit printed GPIO5",
    "devkit_gpio6": "DevKit printed GPIO6",
    "devkit_gpio7": "DevKit printed GPIO7",
    "devkit_gpio15": "DevKit printed GPIO15",
    "devkit_gpio21": "DevKit printed GPIO21",
}
EXPECTED_TWO_TERMINAL_ITEMS = [
    ("r4_pullup", "resistor", "10k", "gpio4", "3v3", "r4_pullup"),
    ("r5_pullup", "resistor", "10k", "gpio5", "3v3", "r5_pullup"),
    ("w_dpdt_a", "wire", "", "gpio4", "dpdt_common_a", "w_dpdt_a"),
    ("w_dpdt_b", "wire", "", "gpio5", "dpdt_common_b", "w_dpdt_b"),
    ("w_dpdt_ga", "wire", "", "dpdt_ground_a", "gnd", "w_dpdt_ga"),
    ("w_dpdt_gb", "wire", "", "dpdt_ground_b", "gnd", "w_dpdt_gb"),
    ("r6_pulldown", "resistor", "47k", "gpio6", "gnd", "r6_pulldown"),
    ("sw6", "switch", "SPST", "gpio6", "gpio6_sw", "sw6"),
    ("r6_series", "resistor", "1k", "gpio6_sw", "3v3", "r6_series"),
    ("r7_pullup", "resistor", "10k", "gpio7", "3v3", "r7_pullup"),
    ("sw7", "switch", "SPST", "gpio7", "gnd", "sw7"),
    ("r15_pulldown", "resistor", "47k", "gpio15", "gnd", "r15_pulldown"),
    ("r15_series", "resistor", "1k", "gpio15", "led15_a", "r15_series"),
    ("led15", "led", "red", "led15_a", "led15_k", "led15"),
    ("w_led15_gnd", "wire", "", "led15_k", "gnd", "w_led15_gnd"),
    ("r21_pulldown", "resistor", "47k", "gpio21", "gnd", "r21_pulldown"),
    ("r21_series", "resistor", "1k", "gpio21", "led21_a", "r21_series"),
    ("led21", "led", "yellow", "led21_a", "led21_k", "led21"),
    ("w_led21_gnd", "wire", "", "led21_k", "gnd", "w_led21_gnd"),
    ("j_gnd", "jumper", "", "devkit_gnd", "gnd", "j_gnd"),
    ("j_3v3", "jumper", "", "devkit_3v3", "3v3", "j_3v3"),
    ("j_gpio4", "jumper", "", "devkit_gpio4", "gpio4", "j_gpio4"),
    ("j_gpio5", "jumper", "", "devkit_gpio5", "gpio5", "j_gpio5"),
    ("j_gpio6", "jumper", "", "devkit_gpio6", "gpio6", "j_gpio6"),
    ("j_gpio7", "jumper", "", "devkit_gpio7", "gpio7", "j_gpio7"),
    ("j_gpio15", "jumper", "", "devkit_gpio15", "gpio15", "j_gpio15"),
    ("j_gpio21", "jumper", "", "devkit_gpio21", "gpio21", "j_gpio21"),
]
EXPECTED_STEP_IDS = (
    "safety,bom,rails,r4_pullup,r5_pullup,dpdt_identify,w_dpdt_a,w_dpdt_b,"
    "w_dpdt_ga,w_dpdt_gb,dpdt_insulate,r6_pulldown,sw6,r6_series,r7_pullup,sw7,"
    "r15_pulldown,r15_series,led15,w_led15_gnd,r21_pulldown,r21_series,"
    "led21,w_led21_gnd,precheck,j_gnd,j_3v3,j_gpio4,j_gpio5,j_gpio6,j_gpio7,"
    "j_gpio15,j_gpio21,check_3v3_gnd,check_devkit_gnd,check_devkit_3v3,"
    "check_gpio4,check_gpio5,check_gpio6,check_gpio7,check_gpio15,check_gpio21,"
    "check_empty16,check_empty17,check_empty18,check_empty38,photo"
).split(",")
EXPECTED_TRUTH_TABLE = [
    {"dpdt": "ungrounded", "gpio6": "open", "gpio7": "open", "levels": [1, 1, 0, 1]},
    {"dpdt": "ungrounded", "gpio6": "open", "gpio7": "closed", "levels": [1, 1, 0, 0]},
    {"dpdt": "ungrounded", "gpio6": "closed", "gpio7": "open", "levels": [1, 1, 1, 1]},
    {"dpdt": "ungrounded", "gpio6": "closed", "gpio7": "closed", "levels": [1, 1, 1, 0]},
    {"dpdt": "grounded", "gpio6": "open", "gpio7": "open", "levels": [0, 0, 0, 1]},
    {"dpdt": "grounded", "gpio6": "open", "gpio7": "closed", "levels": [0, 0, 0, 0]},
    {"dpdt": "grounded", "gpio6": "closed", "gpio7": "open", "levels": [0, 0, 1, 1]},
    {"dpdt": "grounded", "gpio6": "closed", "gpio7": "closed", "levels": [0, 0, 1, 0]},
]
PHOTO_INSTRUCTION = (
    "Keep UART USB unplugged. Take one sharp photo directly overhead with every rail, "
    "resistor band, switch lug, LED lead, and DevKit label visible. Attach that photo "
    "in this chat and wait for approval before applying power."
)


class _ModelParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.chunks = []
        self.scripts = 0

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "wiring-data":
            self.scripts += 1
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == "script" and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.chunks.append(data)


def load_model(path=HTML_PATH):
    parser = _ModelParser()
    parser.feed(Path(path).read_text(encoding="utf-8"))
    if parser.scripts != 1:
        raise ValueError(f"expected exactly one wiring-data script, found {parser.scripts}")
    try:
        model = json.loads("".join(parser.chunks))
    except json.JSONDecodeError as exc:
        raise ValueError("wiring-data must contain JSON") from exc
    if set(model) != TOP_LEVEL_KEYS:
        raise ValueError("wiring-data top-level keys differ from the canonical schema")
    return model


def test_load_model_rejects_missing_duplicate_invalid_and_extra_keys(tmp_path):
    missing = tmp_path / "missing.html"
    missing.write_text("<html></html>", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        load_model(missing)

    duplicate = tmp_path / "duplicate.html"
    duplicate.write_text(
        '<script id="wiring-data">{}</script><script id="wiring-data">{}</script>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exactly one"):
        load_model(duplicate)

    invalid = tmp_path / "invalid.html"
    invalid.write_text('<script id="wiring-data">not json</script>', encoding="utf-8")
    with pytest.raises(ValueError, match="must contain JSON"):
        load_model(invalid)

    extra = tmp_path / "extra.html"
    extra.write_text(
        '<script id="wiring-data">' + json.dumps(dict.fromkeys(TOP_LEVEL_KEYS | {"extra"})) + "</script>",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="top-level keys"):
        load_model(extra)


def test_canonical_nodes_bom_items_and_endpoints():
    model = load_model()
    assert model["schema_version"] == 1
    assert model["storage_key"] == "esp32tap-breadboard-wizard-v1"
    assert model["bom"] == {"10k": 3, "47k": 3, "1k": 3, "led": 2, "dpdt": 1, "spst": 2}
    assert model["disconnected_pins"] == [16, 17, 18, 38]
    assert model["nodes"] == EXPECTED_NODES

    dpdt = next(item for item in model["items"] if item["id"] == "dpdt")
    assert set(dpdt) == {"id", "type", "value", "step", "terminals", "contacts"}
    assert (dpdt["type"], dpdt["value"], dpdt["step"]) == ("switch", "DPDT", "dpdt_identify")
    assert dpdt["terminals"] == {
        "common_a": "dpdt_common_a", "grounded_a": "dpdt_ground_a", "unused_a": "dpdt_unused_a",
        "common_b": "dpdt_common_b", "grounded_b": "dpdt_ground_b", "unused_b": "dpdt_unused_b",
    }
    assert len(set(dpdt["terminals"].values())) == 6
    assert dpdt["contacts"] == [
        {"common": "dpdt_common_a", "grounded": "dpdt_ground_a", "unused": "dpdt_unused_a"},
        {"common": "dpdt_common_b", "grounded": "dpdt_ground_b", "unused": "dpdt_unused_b"},
    ]
    assert all(contact["common"] != other["common"] for contact, other in [(dpdt["contacts"][0], dpdt["contacts"][1])])
    assert all(contact["grounded"].endswith(suffix) and contact["unused"].endswith(suffix)
               for contact, suffix in zip(dpdt["contacts"], ("_a", "_b"), strict=True))

    two_terminal = [item for item in model["items"] if item["id"] != "dpdt"]
    assert [tuple(item[key] for key in ("id", "type", "value", "from", "to", "step")) for item in two_terminal] == EXPECTED_TWO_TERMINAL_ITEMS
    assert all(set(item) == {"id", "type", "value", "from", "to", "step"} for item in two_terminal)
    assert all(item[endpoint] in model["nodes"] for item in two_terminal for endpoint in ("from", "to"))
    assert not any(item.get(endpoint) in {"dpdt_unused_a", "dpdt_unused_b"}
                   for item in two_terminal for endpoint in ("from", "to"))
    assert Counter(item["value"] for item in two_terminal if item["type"] == "resistor") == {"10k": 3, "47k": 3, "1k": 3}
    assert Counter(item["type"] for item in model["items"]) == {
        "resistor": 9, "wire": 6, "switch": 3, "led": 2, "jumper": 8,
    }
    assert len({item["id"] for item in model["items"]}) == len(model["items"])


def test_geometry_switch_states_and_step_contract():
    model = load_model()
    assert model["nodes"]["3v3"] == "red rail columns 1-27"
    assert model["nodes"]["gnd"] == "blue rail columns 1-27"
    assert model["nodes"]["led15_a"] != model["nodes"]["led15_k"]
    assert model["nodes"]["led21_a"] != model["nodes"]["led21_k"]
    assert model["baseline"] == {"dpdt": "ungrounded", "gpio6": "open", "gpio7": "open", "levels": [1, 1, 0, 1]}
    assert model["truth_table"] == EXPECTED_TRUTH_TABLE

    steps = model["steps"]
    assert [step["id"] for step in steps] == EXPECTED_STEP_IDS
    required_fields = {"id", "phase", "highlight", "instruction", "purpose", "confirmation_ids", "applies_power"}
    assert all(set(step) == required_fields for step in steps)
    assert all(step["instruction"].strip() and step["purpose"].strip() for step in steps)
    assert all(step["applies_power"] is False for step in steps)
    phases = {step["id"]: step["phase"] for step in steps}
    assert all(phases[step] == "prepare" for step in EXPECTED_STEP_IDS[:3])
    assert all(phases[step] == "inputs" for step in EXPECTED_STEP_IDS[3:24])
    assert all(phases[step] == "attach" for step in EXPECTED_STEP_IDS[24:33])
    assert all(phases[step] == "verify" for step in EXPECTED_STEP_IDS[33:])

    item_steps = {item["step"]: item["id"] for item in model["items"]}
    step_map = {step["id"]: step for step in steps}
    for step_id, item_id in item_steps.items():
        assert step_map[step_id]["highlight"] == item_id
    assert step_map["dpdt_insulate"]["highlight"] == "dpdt"
    assert step_map["dpdt_identify"]["confirmation_ids"] == ["placed", "both_pole_pairs_meter_identified"]
    for step in steps:
        if step["id"] != "dpdt_identify":
            assert len(step["confirmation_ids"]) == 1
    result_ids = [result for step in steps for result in step["confirmation_ids"]]
    assert len(result_ids) == len(set(result_ids))


def test_meter_checks_and_unpowered_handoff_are_complete_and_independent():
    model = load_model()
    meter = model["meter_checks"]
    assert set(meter) == {"semantics", "pre", "post"}
    assert meter["semantics"] == {
        "continuity_pass": "<2 Ohm",
        "short_fail": "<100 Ohm after waiting five seconds",
    }
    assert len(meter["pre"]) == 11
    pre_text = " ".join(check["check"] for check in meter["pre"])
    for required in ("UART USB disconnected", "no power", "GND rail", "3V3 rail", "3V3-to-ground", "LED15 cathode", "LED21 cathode", "DPDT", "GPIO6 SPST", "GPIO7 SPST"):
        assert required in pre_text

    expected_post_steps = [step for step in EXPECTED_STEP_IDS if step.startswith("check_")]
    assert [check["step"] for check in meter["post"]] == expected_post_steps
    result_ids = [check["result_id"] for check in meter["pre"] + meter["post"]]
    assert len(result_ids) == len(set(result_ids))
    assert all(isinstance(check["result_id"], str) and check["result_id"] for check in meter["pre"] + meter["post"])
    post_text = " ".join(check["check"] for check in meter["post"])
    for required in ("3V3-to-ground", "DevKit GND", "DevKit 3V3", "GPIO4", "GPIO5", "GPIO6", "GPIO7", "GPIO15", "GPIO21", "GPIO16", "GPIO17", "GPIO18", "GPIO38"):
        assert required in post_text

    assert model["photo_instruction"] == PHOTO_INSTRUCTION
    all_text = HTML_PATH.read_text(encoding="utf-8")
    assert "Powered testing is deferred until the overhead photo is reviewed." in all_text
    assert "I attached the overhead photo in this chat" in all_text
    lowered_model = json.dumps(model).lower()
    assert "5v" not in lowered_model
    assert "native usb" not in lowered_model
    assert "treadmill" not in lowered_model


def test_self_contained_view_exposes_required_bench_controls_and_labels():
    html = HTML_PATH.read_text(encoding="utf-8")
    for element_id in (
        "board-svg", "step-title", "step-copy", "confirmations", "previous-step",
        "next-step", "zoom-in", "zoom-out", "reset-progress", "netlist-panel",
        "truth-table", "photo-handoff",
    ):
        assert f'id="{element_id}"' in html
    for css_class in (".item-complete", ".item-active", ".item-future"):
        assert css_class in html
    for warning in ("NO 5V", "UART USB UNPLUGGED", "NO NATIVE USB", "NO TREADMILL"):
        assert warning in html
    for label in (">A<", ">K<", ">3V3<", ">GND<"):
        assert label in html
    assert "http://" not in html
    assert "https://" not in html
