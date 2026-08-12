from __future__ import annotations

import html
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
BRINGUP = ROOT / "hardware/Esp32Tap/bringup"
BUILD_HTML = BRINGUP / "esp32tap-cluster-build-and-test.html"
BUILD_PDF = BRINGUP / "esp32tap-cluster-build-and-test.pdf"
LEGACY_FROM_TO_HTML = BRINGUP / "esp32tap-breadboard-from-to.html"
AUDIT_HTML = BRINGUP / "esp32tap-cluster-audit-and-test.html"
AUDIT_PDF = BRINGUP / "esp32tap-cluster-audit-and-test.pdf"
OLD_HTML = BRINGUP / "esp32tap-module-test-checklist.html"
OLD_PDF = BRINGUP / "esp32tap-module-test-checklist.pdf"

EXPECTED_CLUSTERS = {
    1: "Raw protection",
    2: "TSR supply",
    3: "DevKit and logic supply",
    4: "TPS3700 voltage monitor",
    5: "AHC08 permission logic",
    6: "TPS709 and BC337 driver",
    7: "Relay coil, local contacts, and feedback",
    8: "AHC126 and UART taps",
    9: "Indicators and VBUS sensing",
    10: "Whole-device standalone bench test",
    11: "RJ45 pass-through and treadmill bypass",
}
EXPECTED_LEGACY_REFS_BY_CLUSTER = {
    1: {*range(15, 20), 23, 24},
    2: {20, 21, 22, 25, 26, 27, 28, 31},
    3: {29, 30},
    4: set(range(32, 58)),
    5: set(range(58, 74)),
    6: set(range(74, 88)),
    7: {*range(88, 92), *range(105, 112)},
    8: set(range(92, 101)),
    9: set(range(112, 127)),
    10: set(),
    11: {*range(1, 15), *range(101, 105)},
}
QUALIFIED_LEGACY_GROUPS = {70: 4, 71: 2, 97: 6, 98: 3}
EXPECTED_CONNECTION_IDS_BY_LEGACY_REF = {
    15: ("C1-1",),
    16: ("C1-2", "C1-3"),
    17: ("C1-4",),
    18: ("C1-5",),
    19: ("C1-6",),
    23: ("C1-7",),
    24: ("C1-8",),
    20: ("C2-1",),
    21: ("C2-3",),
    22: ("C2-5",),
    25: ("C2-2",),
    26: ("C2-4",),
    27: ("C2-6",),
    28: ("C2-7",),
    31: ("C2-8",),
    29: ("C3-1",),
    30: ("C3-2",),
    **{
        ref: (f"C4-{index}",)
        for ref, index in {
            32: 1,
            33: 2,
            34: 7,
            35: 8,
            36: 3,
            37: 9,
            38: 10,
            39: 11,
            40: 12,
            41: 13,
            42: 14,
            43: 4,
            44: 15,
            45: 16,
            46: 17,
            47: 18,
            48: 5,
            49: 6,
            50: 19,
            51: 20,
            52: 21,
            53: 22,
            54: 23,
            56: 26,
            57: 27,
        }.items()
    },
    55: ("C4-24", "C4-25"),
    58: ("C5-1",),
    59: ("C5-2",),
    60: ("C5-3",),
    61: ("C5-3", "C5-15"),
    62: ("C5-16",),
    63: ("C5-4",),
    64: ("C5-5",),
    65: ("C5-6",),
    66: ("C5-6", "C5-17"),
    67: ("C5-18",),
    68: ("C5-7",),
    69: ("C5-8",),
    "70a": ("C5-9",),
    "70b": ("C5-10",),
    "70c": ("C5-11",),
    "70d": ("C5-12",),
    "71a": ("C5-13",),
    "71b": ("C5-14",),
    72: ("C5-1", "C5-19"),
    73: ("C5-2", "C5-20"),
    **{
        ref: (f"C6-{index}",)
        for ref, index in {
            74: 1,
            75: 2,
            76: 3,
            77: 5,
            78: 4,
            79: 6,
            80: 7,
            81: 8,
            82: 9,
            83: 10,
            86: 14,
            87: 15,
        }.items()
    },
    84: ("C6-11", "C6-12"),
    85: ("C6-12", "C6-13"),
    88: ("C7-2",),
    89: ("C7-1",),
    90: ("C7-1", "C7-3"),
    91: ("C7-2", "C7-4"),
    105: ("C7-8",),
    106: ("C7-9", "C7-10"),
    107: ("C7-13",),
    108: ("C7-14", "C7-10"),
    109: ("C7-11", "C7-12"),
    110: ("C7-15",),
    111: ("C7-16", "C7-12"),
    92: ("C8-1",),
    93: ("C8-2",),
    94: ("C8-3",),
    95: ("C8-4",),
    96: ("C8-5", "C8-6"),
    "97a": ("C8-8",),
    "97b": ("C8-9",),
    "97c": ("C8-10",),
    "97d": ("C8-11",),
    "97e": ("C8-12",),
    "97f": ("C8-13",),
    "98a": ("C8-14",),
    "98b": ("C8-15",),
    "98c": ("C8-16",),
    99: ("C8-1", "C8-17"),
    100: ("C8-2", "C8-18"),
    112: ("C9-1", "C9-2"),
    113: ("C9-1", "C9-3"),
    114: ("C9-4",),
    115: ("C9-5",),
    116: ("C9-6", "C9-7"),
    117: ("C9-8",),
    118: ("C9-9", "C9-7"),
    119: ("C9-10", "C9-11"),
    120: ("C9-12", "C9-13"),
    121: ("C9-14",),
    122: ("C9-15",),
    123: ("C9-16", "C9-17"),
    124: ("C9-18",),
    125: ("C9-19",),
    126: ("C9-20",),
    **{ref: (f"C11-{index}",) for ref, index in zip(range(1, 12), range(2, 13))},
    12: ("C11-13",),
    13: ("C11-14",),
    14: ("C11-15",),
    101: ("C11-13", "C11-16"),
    102: ("C11-16",),
    103: ("C11-17",),
    104: ("C11-17",),
}
BUILD_ROW_FIELDS = {
    "step",
    "reference",
    "connection_ids",
    "action_ids",
    "from",
    "to",
    "color",
    "part_description",
    "note",
    "directive",
}
FROM_TO_HEADERS = ("Step", "Ref", "Color", "From", "To", "Part", "Note")
WIRE_COLORS = {
    "BLACK",
    "RED",
    "ORANGE",
    "BLUE",
    "VIOLET",
    "YELLOW",
    "GREEN",
    "WHITE",
    "NO WIRE",
}

SHARED_CLUSTER_FIELDS = {
    "number",
    "name",
    "inputs",
    "outputs",
    "dependencies",
    "source_state",
    "stop_gate",
    "pass_gate",
}
SUPERSESSION_REFERENCE_ALLOWLIST = {
    ROOT
    / "docs/superpowers/specs/2026-08-10-esp32tap-cluster-build-audit-guides-design.md",
    ROOT / "docs/superpowers/plans/2026-08-03-esp32tap-module-test-checklist-pdf.md",
    ROOT / "docs/superpowers/plans/2026-08-10-esp32tap-cluster-build-audit-guides.md",
}
PRESCRIBED_BREADBOARD_HOLE = re.compile(
    r"(?:"
    r"(?<![A-Za-z0-9_.])[a-j](?:[1-9]|[1-5]\d|6[0-3])(?![A-Za-z0-9_.])"
    r"|(?<![A-Za-z0-9_.])[+-](?:[1-9]|[1-5]\d|6[0-3])"
    r"(?![A-Za-z0-9_.]|\s*(?:V|mV|A|mA)\b)"
    r"|(?i:\b(?:hole|row|coordinate|breadboard)\b)\s*[:#-]?\s*[A-J](?:[1-9]|[1-5]\d|6[0-3])"
    r"(?![A-Za-z0-9_.])"
    r")"
)

BUILD_LABELS = (
    "Parts",
    "Build",
    "Unpowered test",
    "Powered test",
    "PASS — continue",
    "exact pin/net/color wiring",
)
AUDIT_LABELS = (
    "Isolate",
    "Inspect",
    "Measure",
    "Likely causes",
    "Restore",
    "PASS — continue",
)
IDENTITY_AND_MANIFEST_LABELS = (
    "Guide mode",
    "Device serial",
    "Hardware revision",
    "Operator",
    "Date",
    "Source manifest",
)
SOURCE_AND_SAFETY_LABELS = ("Source contract", "Safety contract")
RENDERED_BOUNDARY_LABELS = (
    "Dependencies",
    "Inputs",
    "Outputs",
    "Source state",
    "STOP gate",
    "PASS gate",
)
RENDERED_IDENTITY_LABELS = (
    "GPIO15 jumper",
    "GPIO21 jumper",
    "Firmware identity",
    "Relay exerciser identity",
    "Observer identity",
    "Observer manifest",
)
UNPOWERED_CHECK_KINDS = {"continuity", "resistance", "mapping", "polarity"}
BYPASS_SEQUENCE_CONTRACTS = {
    "bypass-only source sequence": r"USB.{0,30}(?:absent|disconnected|removed).{0,80}standalone.{0,30}(?:installed|connected).{0,80}observer.{0,30}verified.{0,80}relay.{0,20}off.{0,80}TX.{0,20}disabled",
    "power removed before harness removal": r"(?:power.{0,30}(?:off|removed|disconnected)|(?:remove|disconnect).{0,20}power).{0,80}before.{0,30}(?:remove|removal|disconnect).{0,30}harness",
    "both independent +8V paths restored": r"restore.{0,80}(?:both|two).{0,50}independent.{0,30}\+8V.{0,30}paths",
    "voltage drop after direct-path restoration": r"(?:direct.?path.{0,40}restor(?:e|ed|ation).{0,100}(?:before|then).{0,80}(?:voltage )?drop|(?:voltage )?drop.{0,80}(?:only )?after.{0,50}direct.?path.{0,40}restor(?:e|ed|ation))",
    "thermal test after direct-path restoration": r"(?:direct.?path.{0,40}restor(?:e|ed|ation).{0,100}(?:before|then).{0,80}thermal|thermal.{0,80}(?:only )?after.{0,50}direct.?path.{0,40}restor(?:e|ed|ation))",
    "no-control diagnostic cannot pass relay exercise": r"(?:no.?control.{0,80}(?:diagnostic|current)|current.{0,30}no.?control.{0,30}diagnostic).{0,120}(?:cannot|does not|must not).{0,80}relay.{0,30}exercise.{0,30}(?:gate|PASS)",
    "no-control diagnostic cannot pass UART exercise": r"(?:no.?control.{0,80}(?:diagnostic|current)|current.{0,30}no.?control.{0,30}diagnostic).{0,120}(?:cannot|does not|must not).{0,80}UART.{0,30}exercise.{0,30}(?:gate|PASS)",
}

# Each expression is intentionally about an observable bench contract, rather
# than an exact sentence, so formatting and explanatory prose may evolve.
COMMON_SAFETY_AND_EVIDENCE = {
    "8.00 V source": r"8\.00\s*V",
    "250 mA initial current limit": r"250\s*mA.{0,40}initial|initial.{0,40}250\s*mA",
    "coil-open current below 50 mA": r"(?:below|<)\s*50\s*mA.{0,50}coil.?open|coil.?open.{0,50}(?:below|<)\s*50\s*mA",
    "VIN window": r"VIN.{0,30}7\.20\s*[–-]\s*7\.90\s*V",
    "LOGIC_3V3 window": r"LOGIC_3V3.{0,30}3\.20\s*[–-]\s*3\.40\s*V",
    "TREAD_OK three-state behavior": r"TREAD_OK.{0,180}low.{0,50}(?:below|under).{0,30}UV.{0,80}high.{0,50}8\.00\s*V.{0,80}low.{0,50}(?:above|over).{0,30}OV",
    "rising UV threshold": r"UV.{0,30}rising.{0,30}6\.25\s*[–-]\s*6\.55\s*V|rising.{0,30}UV.{0,30}6\.25\s*[–-]\s*6\.55\s*V",
    "OV falling threshold": r"OV.{0,30}falling.{0,30}10\.30\s*[–-]\s*10\.90\s*V",
    "TPS709 enabled output": r"TPS709.{0,50}(?:enabled|enable).{0,40}4\.75\s*[–-]\s*5\.25\s*V",
    "TPS709 disabled output": r"TPS709.{0,50}(?:disabled|disable).{0,40}(?:below|<)\s*0\.25\s*V",
    "loaded-relay current limit": r"loaded.{0,30}relay.{0,50}(?:no more than|≤|<=)\s*500\s*mA",
    "relay coil current": r"coil.{0,40}90\s*[–-]\s*110\s*mA",
    "relay coil voltage": r"coil.{0,40}(?:≥|>=|at least)\s*4\.50\s*V",
    "BC337 saturation": r"BC337.{0,40}VCE.{0,20}(?:≤|<=|no more than)\s*0\.30\s*V",
    "feedback truth table": r"\(1\s*,\s*0\).{0,40}energized.{0,80}\(0\s*,\s*1\).{0,40}bypass.{0,100}(?:00|0\s*,\s*0).{0,40}(?:11|1\s*,\s*1).{0,40}fault",
    "USB logic power removal releases relay": r"(?:remove|removal|loss).{0,30}USB.{0,30}logic power.{0,200}(?:release|NC.{0,20}bypass)|(?:release|NC.{0,20}bypass).{0,200}(?:remove|removal|loss).{0,30}USB.{0,30}logic power",
    "GPIO21 command removal releases relay": r"(?:remove|removal|loss).{0,30}GPIO21.{0,30}command.{0,200}(?:release|NC.{0,20}bypass)|(?:release|NC.{0,20}bypass).{0,200}(?:remove|removal|loss).{0,30}GPIO21.{0,30}command",
    "TREAD_OK removal releases relay": r"(?:remove|removal|loss).{0,30}TREAD_OK.{0,200}(?:release|NC.{0,20}bypass)|(?:release|NC.{0,20}bypass).{0,200}(?:remove|removal|loss).{0,30}TREAD_OK",
    "VIN removal releases relay": r"(?:remove|removal|loss).{0,30}VIN.{0,200}(?:release|NC.{0,20}bypass)|(?:release|NC.{0,20}bypass).{0,200}(?:remove|removal|loss).{0,30}VIN",
    "NC bypass release time": r"NC.{0,30}bypass.{0,50}(?:≤|<=|no more than|within)\s*100\s*ms",
    "five-minute thermal hold": r"(?:five|5)[ -]minute.{0,80}(?:≤|<=|no more than)\s*45\s*°?C.{0,80}(?:≤|<=|no more than)\s*10\s*°?C.{0,30}(?:over|above)\s*ambient",
    "separate treadmill current": r"treadmill.{0,40}current.{0,40}(?:separate|separately).{0,40}(?:≤|<=|no more than)\s*500\s*mA|(?:separate|separately).{0,40}treadmill.{0,40}current.{0,40}(?:≤|<=|no more than)\s*500\s*mA",
    "pass-through supply drop": r"(?:supply.{0,30}drop|drop.{0,30}supply).{0,40}(?:≤|<=|no more than)\s*50\s*mV",
    "pass-through ground-return drop": r"(?:ground.?return.{0,30}drop|drop.{0,30}ground.?return).{0,40}(?:≤|<=|no more than)\s*50\s*mV",
    "fifteen-minute thermal hold": r"(?:fifteen|15)[ -]minute.{0,80}(?:≤|<=|no more than)\s*40\s*°?C.{0,80}(?:≤|<=|no more than)\s*10\s*°?C.{0,30}(?:over|above)\s*ambient",
    "mutually exclusive power sources": r"USB.{0,50}STANDALONE POWER.{0,60}(?:mutual(?:ly)? exclusive|never.{0,20}(?:together|simultaneous)|one source only)",
    "coil power remains open": r"COIL POWER.{0,50}(?:open|disconnected).{0,80}unloaded test",
    "reset means STOP": r"reset.{0,40}STOP",
    "no treadmill transfer or TX": r"no.{0,40}treadmill.{0,40}(?:relay )?transfer.{0,80}(?:no|or).{0,30}(?:MCU )?TX",
    "sources off before RJ45 changes": r"all sources.{0,30}off.{0,100}RJ45",
    "sources off before harness changes": r"all sources.{0,30}off.{0,100}harness",
    "sources off before path changes": r"all sources.{0,30}off.{0,100}path",
    "cluster 7 is local-only": r"Cluster\s*7.{0,100}local.?only",
    "cluster 11 is end-to-end": r"Cluster\s*11.{0,100}end.?to.?end.{0,80}CONSOLE\.6.{0,20}(?:↔|<->|to).{0,20}MOTOR\.6",
    "active-low isolated VBUS sense": r"VBUS.{0,80}active.?low.{0,100}(?:no|never).{0,30}(?:join|connect).{0,40}VBUS.{0,30}local rail",
    "no treadmill cable before clusters 1 through 10": r"(?:no|do not connect).{0,30}treadmill cable.{0,50}(?:(?:before|through).{0,30}clusters?\s*1\s*[–-]\s*10|until.{0,30}cluster\s*11)",
    "GPIO15 jumper removed before manual injection": r"(?:remove|removed).{0,30}GPIO15.{0,30}jumper.{0,80}before.{0,30}manual injection|before.{0,30}manual injection.{0,80}(?:remove|removed).{0,30}GPIO15.{0,30}jumper",
    "GPIO21 jumper removed before manual injection": r"(?:remove|removed).{0,30}GPIO21.{0,30}jumper.{0,80}before.{0,30}manual injection|before.{0,30}manual injection.{0,80}(?:remove|removed).{0,30}GPIO21.{0,30}jumper",
    "bounded relay exerciser": r"relay exerciser.{0,80}bounded|bounded.{0,80}relay exerciser",
    "bypass-only observation": r"bypass.?only",
    "bypass observation sequence": r"source.{0,50}state.{0,50}harness.{0,50}direct.?path",
    "future qualified firmware and production evidence gate": r"future.{0,80}qualified functional[ -]firmware.{0,160}production safety evidence|qualified functional[ -]firmware.{0,160}production safety evidence.{0,80}future",
}


def _guide_metadata(path: Path) -> dict:
    assert path.is_file(), f"{path.name}: guide HTML is missing"
    source = path.read_text(encoding="utf-8")
    match = re.search(
        r'<script\s+id="guide-metadata"\s+type="application/json">\s*(.*?)\s*</script>',
        source,
        re.DOTALL,
    )
    assert match, f"{path.name}: guide-metadata JSON is missing"
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as error:
        pytest.fail(f"{path.name}: guide-metadata is not valid JSON: {error}")


def _visible_html_text(path: Path) -> str:
    assert path.is_file(), f"{path.name}: guide HTML is missing"
    source = path.read_text(encoding="utf-8")
    source = re.sub(
        r"<script\b.*?</script>", " ", source, flags=re.DOTALL | re.IGNORECASE
    )
    source = re.sub(
        r"<style\b.*?</style>", " ", source, flags=re.DOTALL | re.IGNORECASE
    )
    return _normalize_text(html.unescape(re.sub(r"<[^>]+>", " ", source)))


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _cell_text(cell_html: str) -> str:
    return _normalize_text(html.unescape(re.sub(r"<[^>]+>", " ", cell_html)))


def _json_script(path: Path, script_id: str) -> dict:
    source = path.read_text(encoding="utf-8")
    match = re.search(
        rf'<script\s+id="{re.escape(script_id)}"\s+type="application/json">\s*(.*?)\s*</script>',
        source,
        re.DOTALL,
    )
    assert match, f"{path.name}: {script_id} JSON is missing"
    return json.loads(match.group(1))


def _legacy_rows() -> dict[int, dict[str, str]]:
    source = LEGACY_FROM_TO_HTML.read_text(encoding="utf-8")
    metadata = _json_script(LEGACY_FROM_TO_HTML, "cluster-metadata")
    color_by_ref = {
        reference: color
        for color, references in metadata["wire_colors"].items()
        for reference in references
    }
    rows: dict[int, dict[str, str]] = {}
    for row_html in re.findall(r"<tr\b[^>]*>(.*?)</tr>", source, re.DOTALL):
        cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html, re.DOTALL)
        if len(cells) != 5 or not _cell_text(cells[0]).isdigit():
            continue
        reference = int(_cell_text(cells[0]))
        rows[reference] = {
            "from": _cell_text(cells[1]),
            "to": _cell_text(cells[2]),
            "part_description": _cell_text(cells[3]),
            "note": _cell_text(cells[4]),
            "color": color_by_ref[reference],
        }
    assert set(rows) == set(range(1, 127)), (
        f"{LEGACY_FROM_TO_HTML.name}: expected authoritative refs 1..126"
    )
    return rows


LEGACY_ROWS = _legacy_rows()


def _normalize_directive(text: str) -> str:
    return re.sub(r"[.!?;:]+$", "", _normalize_text(text))


def _qualified_legacy_endpoints(reference: int) -> list[tuple[str, str]]:
    source = LEGACY_ROWS[reference]
    pin_match = re.fullmatch(r"(.+?) pins ([0-9,]+)", source["from"])
    assert pin_match, f"legacy Ref {reference}: grouped From endpoint is malformed"
    prefix, pin_list = pin_match.groups()
    return [(f"{prefix} pin {pin}", source["to"]) for pin in pin_list.split(",")]


def _raw_cluster_sections(path: Path) -> dict[int, str]:
    source = re.sub(
        r"<script\b.*?</script>",
        " ",
        path.read_text(encoding="utf-8"),
        flags=re.DOTALL | re.IGNORECASE,
    )
    starts = []
    for number, name in EXPECTED_CLUSTERS.items():
        match = re.search(
            rf"<h2\b[^>]*>\s*Cluster\s+{number}\s*(?:—|-)\s*{re.escape(name)}\s*</h2>",
            source,
            re.DOTALL,
        )
        assert match, f"{path.name}: cannot identify raw Cluster {number} section"
        starts.append((number, match.start()))
    return {
        number: source[
            start : starts[index + 1][1] if index + 1 < len(starts) else None
        ]
        for index, (number, start) in enumerate(starts)
    }


def _visible_from_to_tables(path: Path) -> dict[int, list[list[list[str]]]]:
    tables_by_cluster: dict[int, list[list[list[str]]]] = {}
    for number, section in _raw_cluster_sections(path).items():
        tables = []
        for table_html in re.findall(
            r'<table\b[^>]*class="[^"]*\bfrom-to\b[^"]*"[^>]*>(.*?)</table>',
            section,
            re.DOTALL | re.IGNORECASE,
        ):
            rows = []
            for row_html in re.findall(
                r"<tr\b[^>]*>(.*?)</tr>", table_html, re.DOTALL | re.IGNORECASE
            ):
                cells = re.findall(
                    r"<t[dh]\b[^>]*>(.*?)</t[dh]>",
                    row_html,
                    re.DOTALL | re.IGNORECASE,
                )
                if cells:
                    rows.append([_cell_text(cell) for cell in cells])
            tables.append(rows)
        tables_by_cluster[number] = tables
    return tables_by_cluster


def _assert_rendered_from_to_rows(text: str, guide_name: str) -> None:
    for cluster in _guide_metadata(BUILD_HTML)["clusters"]:
        blocks = _pdf_from_to_row_blocks(text, cluster)
        for row, block in zip(cluster["build_rows"], blocks, strict=True):
            normalized_block = _normalize_text(block)
            cursor = 0
            for value in (
                str(row["step"]),
                row["reference"],
                row["color"],
                row["from"],
                row["to"],
                row["part_description"],
                row["note"],
                row["directive"],
            ):
                if not value:
                    continue
                position = normalized_block.find(_normalize_text(value), cursor)
                assert position >= 0, (
                    f"{guide_name} cluster {cluster['number']} Step {row['step']}: "
                    f"{value!r} is absent from its bounded extracted row"
                )
                cursor = position + len(_normalize_text(value))


def _pdf_text(path: Path) -> str:
    assert path.is_file(), f"{path.name}: guide PDF is missing"
    completed = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    return _normalize_text(completed.stdout)


def _pdf_layout_text(path: Path) -> str:
    assert path.is_file(), f"{path.name}: guide PDF is missing"
    completed = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _pdf_from_to_row_blocks(text: str, cluster: dict) -> list[str]:
    heading = re.search(
        rf"Cluster\s+{cluster['number']}\s*(?:—|-)\s*{re.escape(cluster['name'])}",
        text,
    )
    assert heading, f"PDF: missing Cluster {cluster['number']} heading"
    next_heading = re.search(
        r"^\s*Cluster\s+\d+\s*(?:—|-)", text[heading.end() :], re.M
    )
    section_end = heading.end() + next_heading.start() if next_heading else len(text)
    section = text[heading.start() : section_end]
    starts = []
    for row in cluster["build_rows"]:
        match = re.search(
            rf"^\s*{row['step']}\s+{re.escape(row['reference'])}\s+{re.escape(row['color'])}(?:\s|$)",
            section,
            re.MULTILINE,
        )
        assert match, (
            f"PDF cluster {cluster['number']} Step {row['step']}: "
            "missing row-start Step/Ref/Color tuple"
        )
        starts.append(match.start())
    assert starts == sorted(starts) and len(starts) == len(set(starts))
    build_end = re.search(r"^\s*Unpowered test", section, re.M)
    end = build_end.start() if build_end else len(section)
    blocks = []
    for index, start in enumerate(starts):
        stop = starts[index + 1] if index + 1 < len(starts) else end
        boundary = re.search(
            r"^\s*Independent isolated-map evidence", section[start:stop], re.M
        )
        if boundary:
            stop = start + boundary.start()
        blocks.append(section[start:stop])
    return blocks


def _assert_headings_in_order(text: str, guide_name: str) -> None:
    positions = []
    for number, name in EXPECTED_CLUSTERS.items():
        match = re.search(rf"Cluster\s+{number}\s*(?:—|-)\s*{re.escape(name)}", text)
        assert match, f"{guide_name}: missing Cluster {number} — {name}"
        positions.append(match.start())
    assert positions == sorted(positions), (
        f"{guide_name}: cluster headings are out of order"
    )


def _cluster_sections(text: str, guide_name: str) -> dict[int, str]:
    heading_matches = [
        re.search(rf"Cluster\s+{number}\s*(?:—|-)\s*{re.escape(name)}", text)
        for number, name in EXPECTED_CLUSTERS.items()
    ]
    assert all(heading_matches), f"{guide_name}: cannot identify every cluster section"
    starts = [match.start() for match in heading_matches if match]
    return {
        number: text[
            starts[index] : starts[index + 1] if index + 1 < len(starts) else None
        ]
        for index, number in enumerate(EXPECTED_CLUSTERS)
    }


def _assert_each_cluster_has_workflow_and_evidence(
    text: str, guide_name: str, workflow_labels: tuple[str, ...]
) -> None:
    for number, section in _cluster_sections(text, guide_name).items():
        for label in (*workflow_labels, "Operator", "Date", "Signed PASS"):
            assert label in section, f"{guide_name} cluster {number}: missing {label!r}"


def _assert_bypass_sequence_contracts(text: str, guide_name: str) -> None:
    sections = _cluster_sections(text, guide_name)
    bypass_section = sections[11]
    for contract, expression in BYPASS_SEQUENCE_CONTRACTS.items():
        assert re.search(expression, bypass_section, re.IGNORECASE), (
            f"{guide_name} cluster 11: missing {contract} contract"
        )
    for number in (7, 8):
        assert "Relay exerciser identity" in sections[number], (
            f"{guide_name} cluster {number}: relay exerciser identity must be local to the cluster"
        )
    for number in (10, 11):
        assert "Observer identity" in sections[number], (
            f"{guide_name} cluster {number}: observer identity must be local to the cluster"
        )
        assert "Observer manifest" in sections[number], (
            f"{guide_name} cluster {number}: observer manifest must be local to the cluster"
        )


def _metadata_values(value: object) -> list[str]:
    if isinstance(value, dict):
        return [token for child in value.values() for token in _metadata_values(child)]
    if isinstance(value, list):
        return [token for child in value for token in _metadata_values(child)]
    if value is None or isinstance(value, bool):
        return []
    return [_normalize_text(str(value))]


def _assert_shared_metadata_values_render(
    text: str, metadata: dict, guide_name: str
) -> None:
    sections = _cluster_sections(text, guide_name)
    for cluster in metadata["clusters"]:
        number = cluster["number"]
        section = sections[number]
        fields = ("inputs", "outputs", "source_state", "stop_gate", "pass_gate")
        for field in fields:
            for value in _metadata_values(cluster[field]):
                assert value in section, (
                    f"{guide_name} cluster {number}: {field} value {value!r} is absent from the PDF"
                )
        for dependency in cluster["dependencies"]:
            rendered = (
                f"Cluster {dependency}" if isinstance(dependency, int) else dependency
            )
            assert rendered in section, (
                f"{guide_name} cluster {number}: dependency {rendered!r} is absent from the PDF"
            )


def _scalar_render_pattern(value: str) -> str:
    escaped = re.escape(value).replace(r"\ ", r"\s+")
    prefix = r"(?<![A-Za-z0-9_.])" if value[0].isalnum() else ""
    suffix = r"(?![A-Za-z0-9_.])" if value[-1].isalnum() else ""
    return prefix + escaped + suffix


def _record_render_pattern(record: dict, fields: tuple[str, ...]) -> str:
    values = [
        value
        for field in fields
        if field in record
        for value in _metadata_values(record[field])
        if value
    ]
    assert values, f"operator record has no renderable values for {fields!r}"
    separator = r".{0,96}?"
    return separator.join(_scalar_render_pattern(value) for value in values)


def _operator_record_groups(
    cluster: dict, mode: str
) -> dict[str, list[tuple[str, str]]]:
    actions = cluster["actions"]
    groups: dict[str, list[tuple[str, str]]] = {}

    def add(group: str, records: list[dict], fields: tuple[str, ...]) -> None:
        groups[group] = [
            (f"{group} record {index}", _record_render_pattern(record, fields))
            for index, record in enumerate(records, 1)
        ]

    if mode == "empty_board_build":
        add(
            "part",
            cluster["parts"],
            (
                "reference",
                "part",
                "value",
                "ordered_part_number",
                "polarity_orientation",
            ),
        )
        add("wiring", cluster["wiring"], ("part", "pin", "net", "color"))
        add("build", actions["build"], ("instruction",))
        add(
            "unpowered test",
            actions["unpowered_test"],
            ("instruction", "check_kind", "points", "expected"),
        )
        add(
            "powered test",
            actions["powered_test"],
            ("instruction", "input", "output", "evidence", "limits"),
        )
    else:
        add("isolate", actions["isolate"], ("instruction", "link"))
        add("inspect", actions["inspect"], ("instruction", "orientation"))
        add(
            "unpowered evidence",
            actions["unpowered_evidence"],
            ("instruction", "points", "expected"),
        )
        add(
            "measurement",
            actions["measure"],
            (
                "instruction",
                "ledger_stage",
                "device",
                "pin",
                "net",
                "stimulus",
                "jumper_state",
                "firmware_identity",
                "observation_state",
                "limits",
            ),
        )
        add("likely cause", actions["likely_causes"], ("stage", "instruction"))
        add("restore", actions["restore"], ("instruction", "link"))
        if actions.get("state_sequence"):
            add(
                "state sequence",
                actions["state_sequence"],
                ("step", "stage", "state", "action", "evidence"),
            )
    return groups


def _assert_operator_metadata_values_render(
    text: str, metadata: dict, guide_name: str, medium: str
) -> None:
    sections = _cluster_sections(text, guide_name)
    for cluster in metadata["clusters"]:
        number = cluster["number"]
        section = sections[number]
        groups = _operator_record_groups(cluster, metadata["mode"])
        assert groups, f"{guide_name} cluster {number}: no operator metadata records"
        for group, contracts in groups.items():
            cursor = 0
            for label, pattern in contracts:
                match = re.search(pattern, section[cursor:], re.IGNORECASE)
                assert match, (
                    f"{guide_name} cluster {number}: {label} is absent or incomplete in visible {medium}"
                )
                cursor += match.end()


def _assert_contract_language(text: str, guide_name: str) -> None:
    for label in SOURCE_AND_SAFETY_LABELS:
        assert label in text, f"{guide_name}: missing {label!r}"
    for contract, expression in COMMON_SAFETY_AND_EVIDENCE.items():
        assert re.search(expression, text, re.IGNORECASE), (
            f"{guide_name}: missing {contract} contract"
        )


def _assert_operator_evidence(cluster: dict, guide_name: str) -> None:
    evidence = cluster.get("evidence_fields")
    assert isinstance(evidence, list), (
        f"{guide_name} cluster {cluster['number']}: evidence_fields must be a list"
    )
    normalized = {
        str(field).strip().lower().replace("-", "_").replace(" ", "_")
        for field in evidence
    }
    assert {
        "operator",
        "date",
        "signed_pass",
    } <= normalized, (
        f"{guide_name} cluster {cluster['number']}: operator, date, and signed-pass fields are required"
    )


def _assert_dependency_contract(
    cluster: dict, clusters: list[dict], guide_name: str
) -> None:
    number = cluster["number"]
    dependencies = cluster["dependencies"]
    assert isinstance(dependencies, list) and dependencies, (
        f"{guide_name} cluster {number}: dependencies must be a nonempty list"
    )
    for dependency in dependencies:
        assert (
            isinstance(dependency, int)
            and not isinstance(dependency, bool)
            and 1 <= dependency < number
        ) or (isinstance(dependency, str) and dependency.strip()), (
            f"{guide_name} cluster {number}: dependency {dependency!r} is not an earlier cluster or named prerequisite"
        )

    sources = cluster.get("input_sources")
    assert isinstance(sources, list) and len(sources) == len(cluster["inputs"]), (
        f"{guide_name} cluster {number}: input_sources must map every input"
    )
    for source in sources:
        assert (
            isinstance(source, dict)
            and {"input", "source_cluster", "verified"} <= source.keys()
        ), (
            f"{guide_name} cluster {number}: each input source needs input, source_cluster, and verified"
        )
        assert source["input"] in cluster["inputs"]
        assert source["verified"] is True, (
            f"{guide_name} cluster {number}: input {source['input']!r} consumes unverified upstream state"
        )
        upstream = source["source_cluster"]
        assert upstream is None or (
            isinstance(upstream, int)
            and not isinstance(upstream, bool)
            and upstream in dependencies
        ), (
            f"{guide_name} cluster {number}: input source {upstream!r} is not a declared dependency"
        )
        if upstream is None:
            assert (
                isinstance(source.get("source_name"), str)
                and source["source_name"].strip()
            ), (
                f"{guide_name} cluster {number}: external input {source['input']!r} needs a clear source_name prerequisite"
            )
            assert "source_output" not in source
        else:
            upstream_cluster = clusters[upstream - 1]
            assert upstream_cluster["number"] == upstream
            source_output = source.get("source_output", source["input"])
            assert source_output in upstream_cluster["outputs"], (
                f"{guide_name} cluster {number}: input {source['input']!r} falsely claims Cluster {upstream}; "
                f"source output {source_output!r} is absent from Cluster {upstream} outputs"
            )
            if source_output != source["input"]:
                assert "source_output" in source
                assert (
                    isinstance(source.get("source_mapping"), str)
                    and source["source_mapping"].strip()
                ), (
                    f"{guide_name} cluster {number}: renamed source output {source_output!r} "
                    f"needs an explicit mapping to input {source['input']!r}"
                )
    assert {source["input"] for source in sources} == set(cluster["inputs"]), (
        f"{guide_name} cluster {number}: every input must have exactly one provenance record"
    )


def _assert_instruction_steps(steps: object, label: str) -> list[dict]:
    assert isinstance(steps, list) and steps, f"{label} must be a nonempty list"
    assert all(isinstance(step, dict) and step.get("instruction") for step in steps), (
        f"{label} steps must be structured objects with nonempty instructions"
    )
    return steps


def _assert_build_actions(cluster: dict, guide_name: str) -> set[str]:
    number = cluster["number"]
    parts = cluster.get("parts")
    assert isinstance(parts, list) and parts, (
        f"{guide_name} cluster {number}: parts must be nonempty"
    )
    for part in parts:
        assert isinstance(part, dict), (
            f"{guide_name} cluster {number}: every part must be a structured object"
        )
        for field in (
            "reference",
            "part",
            "value",
            "ordered_part_number",
            "polarity_orientation",
        ):
            assert part.get(field), (
                f"{guide_name} cluster {number}: every part needs {field}"
            )
        order_identity = part["ordered_part_number"]
        assert isinstance(order_identity, str) and (
            order_identity == "NOT ORDERED" or re.search(r"[A-Za-z0-9]", order_identity)
        ), (
            f"{guide_name} cluster {number}: ordered_part_number needs a real identity or NOT ORDERED"
        )

    wiring = cluster.get("wiring")
    assert isinstance(wiring, list) and wiring, (
        f"{guide_name} cluster {number}: wiring must be nonempty"
    )
    assert all(
        isinstance(connection, dict)
        and all(connection.get(field) for field in ("part", "pin", "net", "color"))
        for connection in wiring
    ), (
        f"{guide_name} cluster {number}: every wiring record needs exact part, pin, net, and color"
    )

    actions = cluster.get("actions")
    assert isinstance(actions, dict), (
        f"{guide_name} cluster {number}: actions must be an object"
    )
    _assert_instruction_steps(
        actions.get("build"), f"{guide_name} cluster {number} build"
    )
    unpowered = _assert_instruction_steps(
        actions.get("unpowered_test"),
        f"{guide_name} cluster {number} unpowered_test",
    )
    kinds = set()
    for check in unpowered:
        kind = check.get("check_kind")
        assert kind in UNPOWERED_CHECK_KINDS, (
            f"{guide_name} cluster {number}: invalid unpowered check_kind {kind!r}"
        )
        assert check.get("points") and check.get("expected"), (
            f"{guide_name} cluster {number}: unpowered checks need points and expected evidence"
        )
        kinds.add(kind)

    powered = _assert_instruction_steps(
        actions.get("powered_test"), f"{guide_name} cluster {number} powered_test"
    )
    for measurement in powered:
        for field in ("input", "output", "evidence", "limits"):
            assert measurement.get(field), (
                f"{guide_name} cluster {number}: powered measurements need {field}"
            )
    return kinds


def _assert_audit_actions(cluster: dict, guide_name: str) -> None:
    number = cluster["number"]
    actions = cluster.get("actions")
    assert isinstance(actions, dict), (
        f"{guide_name} cluster {number}: actions must be an object"
    )
    structured = {
        action: _assert_instruction_steps(
            actions.get(action), f"{guide_name} cluster {number} {action}"
        )
        for action in (
            "isolate",
            "inspect",
            "unpowered_evidence",
            "measure",
            "likely_causes",
            "restore",
        )
    }

    assert any(step.get("orientation") for step in structured["inspect"]), (
        f"{guide_name} cluster {number}: inspection needs explicit orientation evidence"
    )
    for evidence in structured["unpowered_evidence"]:
        assert evidence.get("points") and evidence.get("expected"), (
            f"{guide_name} cluster {number}: unpowered audit evidence needs points and expected result"
        )
    for measurement in structured["measure"]:
        for field in (
            "device",
            "pin",
            "net",
            "stimulus",
            "jumper_state",
            "firmware_identity",
            "observation_state",
            "limits",
        ):
            assert measurement.get(field), (
                f"{guide_name} cluster {number}: audit measurements need exact {field}"
            )
    cause_stages = [cause.get("stage") for cause in structured["likely_causes"]]
    assert cause_stages == [
        "source",
        "component",
        "output",
    ], (
        f"{guide_name} cluster {number}: likely causes must be ordered source -> component -> output"
    )

    for step in structured["isolate"]:
        assert isinstance(step.get("opens_link"), bool), (
            f"{guide_name} cluster {number}: every isolation step must declare opens_link"
        )
        if step["opens_link"]:
            assert step.get("link"), (
                f"{guide_name} cluster {number}: link-opening isolation steps must name the link"
            )
    for step in structured["restore"]:
        assert isinstance(step.get("restores_link"), bool), (
            f"{guide_name} cluster {number}: every restore step must declare restores_link"
        )
        if step["restores_link"]:
            assert step.get("link"), (
                f"{guide_name} cluster {number}: link-restoring steps must name the link"
            )
    opened_links = {
        step["link"] for step in structured["isolate"] if step["opens_link"]
    }
    restored_links = {
        step["link"] for step in structured["restore"] if step["restores_link"]
    }
    assert opened_links <= restored_links, (
        f"{guide_name} cluster {number}: opened audit links not restored: "
        + ", ".join(sorted(opened_links - restored_links))
    )


def test_new_build_and_audit_artifacts_exist():
    missing = [
        path.relative_to(ROOT).as_posix()
        for path in (BUILD_HTML, BUILD_PDF, AUDIT_HTML, AUDIT_PDF)
        if not path.is_file()
    ]
    assert not missing, f"missing cluster guide artifacts: {', '.join(missing)}"


def test_old_checklist_artifacts_are_removed():
    stale = [
        path.relative_to(ROOT).as_posix()
        for path in (OLD_HTML, OLD_PDF)
        if path.exists()
    ]
    assert not stale, f"superseded checklist artifacts still exist: {', '.join(stale)}"


@pytest.mark.parametrize(
    ("path", "expected_mode"),
    ((BUILD_HTML, "empty_board_build"), (AUDIT_HTML, "assembled_board_audit")),
)
def test_html_metadata_defines_ordered_cluster_contracts(
    path: Path, expected_mode: str
):
    data = _guide_metadata(path)
    assert data.get("mode") == expected_mode
    clusters = data.get("clusters")
    assert isinstance(clusters, list) and len(clusters) == len(EXPECTED_CLUSTERS)
    assert [
        (cluster.get("number"), cluster.get("name")) for cluster in clusters
    ] == list(EXPECTED_CLUSTERS.items())

    unpowered_check_kinds = set()
    for cluster in clusters:
        assert SHARED_CLUSTER_FIELDS <= cluster.keys(), (
            f"{path.name} cluster {cluster.get('number')}: incomplete shared contract"
        )
        assert isinstance(cluster["inputs"], list), (
            f"{path.name} cluster {cluster['number']}: inputs must be a list"
        )
        assert isinstance(cluster["outputs"], list), (
            f"{path.name} cluster {cluster['number']}: outputs must be a list"
        )
        assert all(isinstance(item, str) and item for item in cluster["inputs"]), (
            f"{path.name} cluster {cluster['number']}: inputs must be named"
        )
        assert len(set(cluster["inputs"])) == len(cluster["inputs"]), (
            f"{path.name} cluster {cluster['number']}: inputs must be unique"
        )
        assert all(isinstance(item, str) and item for item in cluster["outputs"]), (
            f"{path.name} cluster {cluster['number']}: outputs must be named"
        )
        for field in (
            "inputs",
            "outputs",
            "dependencies",
            "source_state",
            "stop_gate",
            "pass_gate",
        ):
            assert cluster[field], (
                f"{path.name} cluster {cluster['number']}: {field} must be nonempty"
            )
        _assert_operator_evidence(cluster, path.name)
        _assert_dependency_contract(cluster, data["clusters"], path.name)
        if expected_mode == "assembled_board_audit":
            _assert_audit_actions(cluster, path.name)
        else:
            unpowered_check_kinds.update(_assert_build_actions(cluster, path.name))
    if expected_mode == "empty_board_build":
        assert UNPOWERED_CHECK_KINDS <= unpowered_check_kinds, (
            f"{path.name}: build metadata must cover continuity, resistance, mapping, and polarity checks"
        )


def test_build_and_audit_metadata_share_the_same_cluster_contracts():
    build = _guide_metadata(BUILD_HTML)
    audit = _guide_metadata(AUDIT_HTML)
    for build_cluster, audit_cluster in zip(
        build["clusters"], audit["clusters"], strict=True
    ):
        assert {field: build_cluster[field] for field in SHARED_CLUSTER_FIELDS} == {
            field: audit_cluster[field] for field in SHARED_CLUSTER_FIELDS
        }


@pytest.mark.parametrize("path", (BUILD_HTML, AUDIT_HTML))
def test_html_has_no_prescribed_breadboard_holes(path: Path):
    searchable = (
        _visible_html_text(path)
        + " "
        + json.dumps(_guide_metadata(path), sort_keys=True)
    )
    match = PRESCRIBED_BREADBOARD_HOLE.search(searchable)
    assert match is None, (
        f"{path.name}: prescribed breadboard hole token {match.group(0)!r} is forbidden"
    )


def test_breadboard_hole_pattern_distinguishes_holes_from_pins_and_voltages():
    for hole in ("a1", "a29", "j63", "f36", "+1", "+52", "-52"):
        assert PRESCRIBED_BREADBOARD_HOLE.fullmatch(hole)
    for contextual_hole in (
        "hole A1",
        "row A29",
        "coordinate: F36",
        "breadboard J63",
    ):
        assert PRESCRIBED_BREADBOARD_HOLE.search(contextual_hole)
    for allowed in (
        "J1",
        "C1",
        "D1",
        "F1",
        "A1",
        "A29",
        "C10",
        "C11",
        "C13",
        "D12",
        "F10",
        "GPIO29",
        "U6.29",
        "3.29 V",
        "+8 V",
        "-5.2 V",
        "+8.00 V",
        "VIN_A29",
    ):
        assert PRESCRIBED_BREADBOARD_HOLE.search(allowed) is None


@pytest.mark.parametrize(
    ("path", "labels"),
    ((BUILD_HTML, BUILD_LABELS), (AUDIT_HTML, AUDIT_LABELS)),
)
def test_html_contains_mode_workflow_and_safety_contracts(
    path: Path, labels: tuple[str, ...]
):
    text = _visible_html_text(path)
    metadata = _guide_metadata(path)
    _assert_headings_in_order(text, path.name)
    for label in labels:
        assert label in text, f"{path.name}: missing {label!r} workflow label"
    for label in RENDERED_IDENTITY_LABELS:
        assert label in text, (
            f"{path.name}: missing {label!r} identity or manifest field"
        )
    per_cluster_labels = labels[:-1] if path == BUILD_HTML else labels
    _assert_each_cluster_has_workflow_and_evidence(text, path.name, per_cluster_labels)
    _assert_operator_metadata_values_render(text, metadata, path.name, "HTML")
    _assert_contract_language(text, path.name)
    _assert_bypass_sequence_contracts(text, path.name)


def test_superseded_checklist_has_no_operational_references():
    completed = subprocess.run(
        [
            "rg",
            "--files-with-matches",
            "--fixed-strings",
            "--glob",
            "!.git/**",
            "--glob",
            f"!{Path(__file__).relative_to(ROOT).as_posix()}",
            "-e",
            OLD_HTML.name,
            "-e",
            OLD_PDF.name,
            str(ROOT),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode in (0, 1), completed.stderr
    references = {Path(line) for line in completed.stdout.splitlines() if line}
    unexpected = references - SUPERSESSION_REFERENCE_ALLOWLIST
    assert not unexpected, (
        "operational references to the superseded checklist remain: "
        + ", ".join(sorted(path.relative_to(ROOT).as_posix() for path in unexpected))
    )


@pytest.mark.parametrize(
    ("path", "html_path", "mode_label", "workflow_labels"),
    (
        (BUILD_PDF, BUILD_HTML, "Empty-board build", BUILD_LABELS[:-1]),
        (AUDIT_PDF, AUDIT_HTML, "Assembled-board audit", AUDIT_LABELS),
    ),
)
def test_pdf_preserves_contracts_and_has_letter_page_size(
    path: Path,
    html_path: Path,
    mode_label: str,
    workflow_labels: tuple[str, ...],
):
    text = _pdf_text(path)
    metadata = _guide_metadata(html_path)
    _assert_headings_in_order(text, path.name)
    assert mode_label in text, f"{path.name}: missing mode label"
    for label in (*workflow_labels, *IDENTITY_AND_MANIFEST_LABELS, "STOP", "PASS"):
        assert label in text, f"{path.name}: missing {label!r}"
    _assert_each_cluster_has_workflow_and_evidence(text, path.name, workflow_labels)
    for label in RENDERED_IDENTITY_LABELS:
        assert label in text, f"{path.name}: missing rendered {label!r}"
    for number, section in _cluster_sections(text, path.name).items():
        for label in RENDERED_BOUNDARY_LABELS:
            assert label in section, (
                f"{path.name} cluster {number}: missing rendered {label!r}"
            )
    _assert_shared_metadata_values_render(text, metadata, path.name)
    _assert_operator_metadata_values_render(text, metadata, path.name, "PDF")
    _assert_contract_language(text, path.name)
    _assert_bypass_sequence_contracts(text, path.name)
    assert re.search(r"file:///|/home/|[A-Za-z]:\\", text, re.IGNORECASE) is None
    assert str(ROOT) not in text

    info = subprocess.run(
        ["pdfinfo", "-f", "1", "-l", "999999", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    sizes = re.findall(
        r"^Page(?:\s+\d+)? size:\s+([\d.]+) x ([\d.]+) pts", info, re.MULTILINE
    )
    assert sizes, f"{path.name}: pdfinfo did not report a page size"
    page_count_match = re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE)
    assert page_count_match and len(sizes) == int(page_count_match.group(1)), (
        f"{path.name}: pdfinfo must report a size for every page"
    )
    assert all(
        (float(width), float(height)) == (612.0, 792.0) for width, height in sizes
    ), f"{path.name}: every page must be US Letter (612 x 792 pts)"


def test_build_cluster_8_does_not_consume_unbuilt_rj45_state():
    clusters = {
        cluster["number"]: cluster
        for cluster in _guide_metadata(BUILD_HTML)["clusters"]
    }
    cluster = clusters[8]
    forbidden = {"CONSOLE.6", "PIN3", "MOTOR.6"}
    assert forbidden.isdisjoint(cluster["inputs"])
    cluster_7_inputs = {
        source["input"]
        for source in cluster["input_sources"]
        if source["source_cluster"] == 7
    }
    assert cluster_7_inputs == {"LOCAL_NC", "LOCAL_NO"}
    assert "RJ45" not in " ".join(cluster["inputs"] + cluster["outputs"])


def test_build_cluster_11_maps_isolated_conductors_before_common_links():
    cluster = _guide_metadata(BUILD_HTML)["clusters"][10]
    instructions = [step["instruction"] for step in cluster["actions"]["build"]]
    assert len(instructions) >= 2
    rendered = " ".join(instructions)
    map_position = rendered.index("map each conductor independently")
    join_position = rendered.index("join pins 1/7 and 2/8")
    assert map_position < join_position
    assert "unrelated pins open" in rendered[map_position:join_position]


def test_build_has_dedicated_measurement_evidence_fields():
    clusters = {
        cluster["number"]: cluster
        for cluster in _guide_metadata(BUILD_HTML)["clusters"]
    }
    required = {
        7: {
            "usb_logic_release_ms",
            "gpio21_release_ms",
            "tread_ok_release_ms",
            "vin_release_ms",
            "ambient_temp_c",
            "tps709_temp_c",
            "bc337_temp_c",
        },
        10: {"gpio16_idle_v", "gpio18_idle_v"},
        11: {
            "treadmill_current_ma",
            "supply_drop_mv",
            "ground_return_drop_mv",
            "plus_8v_endpoint_temp_c",
            "gnd_endpoint_temp_c",
            *(f"console_{pin}_temp_c" for pin in range(1, 9)),
            *(f"motor_{pin}_temp_c" for pin in range(1, 9)),
        },
    }
    for number, fields in required.items():
        assert fields <= set(clusters[number]["evidence_fields"])

    visible = _visible_html_text(BUILD_HTML)
    for label in (
        "USB logic release (ms)",
        "GPIO21 release (ms)",
        "TREAD_OK release (ms)",
        "VIN release (ms)",
        "TPS709 temperature (°C)",
        "BC337 temperature (°C)",
        "GPIO16 UART idle (V)",
        "GPIO18 UART idle (V)",
        "Treadmill current (mA)",
        "Supply drop (mV)",
        "Ground-return drop (mV)",
        "CONSOLE temperature (°C)",
        "MOTOR temperature (°C)",
        "+8V endpoint temperature (°C)",
        "GND endpoint temperature (°C)",
    ):
        assert label in visible


def test_build_records_exact_command_jumper_endpoints():
    text = _visible_html_text(BUILD_HTML)
    assert "DevKit GPIO15 ↔ removable GPIO15 jumper ↔ TX_ENABLE / AHC08 pin 4" in text
    assert "DevKit GPIO21 ↔ removable GPIO21 jumper ↔ RELAY_CMD / AHC08 pin 1" in text


def test_build_adapter_headers_formulas_and_logic_bypass_are_explicit():
    metadata = _guide_metadata(BUILD_HTML)
    clusters = {cluster["number"]: cluster for cluster in metadata["clusters"]}
    for number in (4, 6):
        assert any(
            "S1011EC-40-ND" in part["ordered_part_number"]
            for part in clusters[number]["parts"]
        )
    text = _visible_html_text(BUILD_HTML)
    assert "UV_SENSE = VIN × 10 kΩ / (150 kΩ + 10 kΩ) = VIN / 16" in text
    assert "OV_SENSE = VIN × 10 kΩ / (255 kΩ + 10 kΩ) = VIN / 26.5" in text
    assert "continuity-map adapter pins 1–6 to the header before installation" in text
    assert "C7 lead 1 to LOGIC_3V3 and C7 lead 2 to GND" in text


def test_build_cluster_8_proves_local_future_interface_without_connectors():
    cluster = _guide_metadata(BUILD_HTML)["clusters"][7]
    assert 7 in cluster["dependencies"]
    assert {"LOCAL_NC", "LOCAL_NO"} <= set(cluster["inputs"])
    assert {"GPIO18_RX_LOCAL", "GPIO16_RX_LOCAL", "LOCAL_TX_SELECTED"} <= set(
        cluster["outputs"]
    )
    provenance = {
        source["input"]: source["source_cluster"] for source in cluster["input_sources"]
    }
    assert provenance["LOCAL_NC"] == provenance["LOCAL_NO"] == 7
    rendered = _normalize_text(
        json.dumps(cluster["actions"], ensure_ascii=False)
        + " "
        + _cluster_sections(_visible_html_text(BUILD_HTML), BUILD_HTML.name)[8]
    )
    for required in (
        "LOCAL_CONSOLE_6_STUB",
        "LOCAL_PIN3_STUB",
        "GPIO18_RX_LOCAL",
        "GPIO16_RX_LOCAL",
        "LOCAL_TX_SELECTED",
        "bounded UART/relay exerciser",
    ):
        assert required in rendered
    assert "RJ45" not in " ".join(cluster["inputs"] + cluster["outputs"])


def test_complete_command_jumpers_are_constructed_in_cluster_5_only():
    sections = _cluster_sections(_visible_html_text(BUILD_HTML), BUILD_HTML.name)
    complete_gpio15 = (
        "DevKit GPIO15 ↔ removable GPIO15 jumper ↔ TX_ENABLE / AHC08 pin 4"
    )
    complete_gpio21 = (
        "DevKit GPIO21 ↔ removable GPIO21 jumper ↔ RELAY_CMD / AHC08 pin 1"
    )
    assert complete_gpio15 not in sections[3]
    assert complete_gpio21 not in sections[3]
    assert complete_gpio15 in sections[5]
    assert complete_gpio21 in sections[5]
    assert "DevKit-side GPIO15 jumper post" in sections[3]
    assert "DevKit-side GPIO21 jumper post" in sections[3]


def test_cluster_11_records_isolated_map_before_commoning():
    section = _cluster_sections(_visible_html_text(BUILD_HTML), BUILD_HTML.name)[11]
    map_step = section.index("Isolated mapping first")
    evidence = section.index("Independent isolated-map evidence")
    common_step = section.index("Common only after isolated-map PASS")
    assert map_step < evidence < common_step
    post_common = section[common_step:]
    assert "unrelated pins open" not in post_common


def test_each_build_connection_has_one_numbered_wiring_record():
    metadata = _guide_metadata(BUILD_HTML)
    for cluster in metadata["clusters"]:
        wiring = cluster["wiring"]
        build = cluster["actions"]["build"]
        assert len(wiring) >= 3, (
            f"cluster {cluster['number']} needs explicit connections"
        )
        assert len(build) == len(wiring), (
            f"cluster {cluster['number']} build/wiring mismatch"
        )
        wiring_ids = [record.get("connection_id") for record in wiring]
        build_ids = [record.get("connection_id") for record in build]
        assert wiring_ids == build_ids
        assert wiring_ids == [
            f"C{cluster['number']}-{index}" for index in range(1, len(wiring) + 1)
        ]
        consumed_ids = [
            connection_id
            for row in cluster["build_rows"]
            for connection_id in row["connection_ids"]
        ]
        assert set(consumed_ids) == set(wiring_ids)


def test_build_from_to_rows_cover_wiring_and_actions_once():
    metadata = _guide_metadata(BUILD_HTML)
    for cluster in metadata["clusters"]:
        number = cluster["number"]
        wiring = cluster["wiring"]
        actions = cluster["actions"]["build"]
        rows = cluster.get("build_rows")
        assert isinstance(rows, list) and rows, (
            f"cluster {number}: build_rows must be a nonempty list"
        )
        assert [row.get("step") for row in rows] == list(range(1, len(rows) + 1))

        wiring_ids = [record["connection_id"] for record in wiring]
        action_ids = []
        for action in actions:
            assert action.get("action_id") == action["connection_id"], (
                f"cluster {number}: action_id must equal connection_id"
            )
            action_ids.append(action["action_id"])
        assert wiring_ids == action_ids, (
            f"cluster {number}: wiring/build IDs must retain operator order"
        )
        assert len(wiring_ids) == len(set(wiring_ids))

        consumed_connections = []
        consumed_actions = []
        for row in rows:
            assert BUILD_ROW_FIELDS <= row.keys(), (
                f"cluster {number} Step {row.get('step')}: incomplete build-row schema"
            )
            assert isinstance(row["connection_ids"], list) and row["connection_ids"]
            assert isinstance(row["action_ids"], list) and row["action_ids"]
            assert row["connection_ids"] == row["action_ids"]
            assert row["color"] in WIRE_COLORS
            assert all(
                isinstance(row[field], str) and row[field].strip()
                for field in (
                    "reference",
                    "from",
                    "to",
                    "color",
                    "part_description",
                    "directive",
                )
            )
            assert isinstance(row["note"], str)
            selected = [actions[action_ids.index(item)] for item in row["action_ids"]]
            expected_directive = _normalize_directive(
                " ".join(action["instruction"] for action in selected)
            )
            assert _normalize_directive(row["directive"]) == expected_directive, (
                f"cluster {number} Step {row['step']}: directive differs from ordered actions"
            )
            consumed_connections.extend(row["connection_ids"])
            consumed_actions.extend(row["action_ids"])

        assert set(consumed_connections) == set(wiring_ids), (
            f"cluster {number}: every wiring record must be covered"
        )
        assert set(consumed_actions) == set(action_ids), (
            f"cluster {number}: every build action must be covered"
        )
        justified_reuse = Counter(
            connection_id
            for expected in EXPECTED_CONNECTION_IDS_BY_LEGACY_REF.values()
            for connection_id in expected
            if connection_id.startswith(f"C{number}-")
        )
        for connection_id, count in Counter(consumed_connections).items():
            if count > 1:
                assert count == justified_reuse[connection_id], (
                    f"cluster {number}: {connection_id} reuse is not justified by exact legacy endpoint rows"
                )
        assert len({row["reference"] for row in rows}) == len(rows), (
            f"cluster {number}: displayed row references must be unique"
        )


def test_build_from_to_legacy_references_match_source():
    metadata = _guide_metadata(BUILD_HTML)
    displayed_legacy_references = []
    for cluster in metadata["clusters"]:
        number = cluster["number"]
        numeric_bases = set()
        qualified_by_base: dict[int, list[dict]] = {}
        for row in cluster["build_rows"]:
            reference = row["reference"]
            legacy_match = re.fullmatch(r"(\d+)([a-z]?)", reference)
            if not legacy_match:
                assert reference == f"NEW {row['connection_ids'][0]}", (
                    f"cluster {number} Step {row['step']}: unmapped rows need their stable NEW connection ID"
                )
                continue

            base = int(legacy_match.group(1))
            suffix = legacy_match.group(2)
            assert base in EXPECTED_LEGACY_REFS_BY_CLUSTER[number], (
                f"cluster {number}: legacy Ref {base} belongs to another cluster"
            )
            numeric_bases.add(base)
            displayed_legacy_references.append(reference)
            source = LEGACY_ROWS[base]
            assert row["color"] == source["color"]
            fixture_key = reference if suffix else base
            assert (
                tuple(row["connection_ids"])
                == EXPECTED_CONNECTION_IDS_BY_LEGACY_REF[fixture_key]
            ), (
                f"legacy Ref {reference}: connection IDs must exactly match the "
                "authoritative endpoint mapping; unrelated IDs or permutations are forbidden"
            )
            assert (
                tuple(row["action_ids"])
                == EXPECTED_CONNECTION_IDS_BY_LEGACY_REF[fixture_key]
            )
            if base not in QUALIFIED_LEGACY_GROUPS:
                assert not suffix, (
                    f"legacy Ref {base} must be displayed without a suffix"
                )
                for field in ("from", "to", "part_description"):
                    assert _normalize_text(row[field]) == source[field], (
                        f"legacy Ref {base}: {field} differs from the authoritative row"
                    )
                assert source["note"] in _normalize_text(row["note"])
            else:
                assert suffix, f"grouped legacy Ref {base} requires a suffix"
                qualified_by_base.setdefault(base, []).append(row)

        assert numeric_bases == EXPECTED_LEGACY_REFS_BY_CLUSTER[number], (
            f"cluster {number}: legacy-reference ownership does not match the approved map"
        )
        for base, count in QUALIFIED_LEGACY_GROUPS.items():
            if base not in EXPECTED_LEGACY_REFS_BY_CLUSTER[number]:
                continue
            rows = qualified_by_base.get(base, [])
            assert [row["reference"] for row in rows] == [
                f"{base}{chr(ord('a') + index)}" for index in range(count)
            ]
            assert [
                (_normalize_text(row["from"]), _normalize_text(row["to"]))
                for row in rows
            ] == _qualified_legacy_endpoints(base)
            source = LEGACY_ROWS[base]
            group_semantics = (
                f"legacy Ref {base}; {count} intentional opens"
                if source["color"] == "NO WIRE"
                else f"legacy Ref {base}; {count} wires"
            )
            for row in rows:
                assert row["color"] == source["color"]
                assert source["note"] in _normalize_text(row["note"])
                assert group_semantics in _normalize_text(row["note"])
                if source["color"] == "NO WIRE":
                    assert row["part_description"] in {"NO WIRE", "—"}
                else:
                    assert row["part_description"] == "Wire"

    assert len(displayed_legacy_references) == len(set(displayed_legacy_references))


def test_build_from_to_tables_render_in_html():
    metadata = _guide_metadata(BUILD_HTML)
    tables_by_cluster = _visible_from_to_tables(BUILD_HTML)
    for cluster in metadata["clusters"]:
        number = cluster["number"]
        tables = tables_by_cluster[number]
        assert len(tables) == (2 if number == 11 else 1), (
            f"cluster {number}: expected visible from-to table(s)"
        )
        assert all(table and tuple(table[0]) == FROM_TO_HEADERS for table in tables)
        visible_rows = [row for table in tables for row in table[1:]]
        assert len(visible_rows) == len(cluster["build_rows"])
        for metadata_row, visible_row in zip(
            cluster["build_rows"], visible_rows, strict=True
        ):
            assert len(visible_row) == len(FROM_TO_HEADERS)
            expected = [
                str(metadata_row["step"]),
                metadata_row["reference"],
                metadata_row["color"],
                metadata_row["from"],
                metadata_row["to"],
                metadata_row["part_description"],
            ]
            assert visible_row[:6] == [_normalize_text(value) for value in expected]
            note_cell = visible_row[6]
            note_position = note_cell.find(_normalize_text(metadata_row["note"]))
            directive_position = note_cell.find(
                _normalize_text(metadata_row["directive"])
            )
            assert note_position >= 0 and directive_position >= note_position, (
                f"cluster {number} Step {metadata_row['step']}: Note cell must show note then complete directive"
            )


def test_build_from_to_tables_render_in_pdf():
    text = _pdf_layout_text(BUILD_PDF)
    header_pattern = r"^\s*" + r"\s+".join(map(re.escape, FROM_TO_HEADERS)) + r"\s*$"
    assert len(re.findall(header_pattern, text, re.MULTILINE)) == 12
    _assert_rendered_from_to_rows(text, BUILD_PDF.name)


def test_build_from_to_cluster_11_keeps_map_gate_between_steps():
    cluster = _guide_metadata(BUILD_HTML)["clusters"][10]
    assert [row["step"] for row in cluster["build_rows"][:2]] == [1, 2]
    first, second = cluster["build_rows"][:2]
    for path, text in (
        (BUILD_HTML, _visible_html_text(BUILD_HTML)),
        (BUILD_PDF, _pdf_text(BUILD_PDF)),
    ):
        section = _cluster_sections(text, path.name)[11]
        first_match = re.search(
            _scalar_render_pattern("1")
            + r".{0,160}?"
            + _scalar_render_pattern(first["reference"]),
            section,
        )
        evidence = section.find("Independent isolated-map evidence")
        second_match = re.search(
            _scalar_render_pattern("2")
            + r".{0,160}?"
            + _scalar_render_pattern(second["reference"]),
            section,
        )
        assert first_match and second_match and evidence >= 0
        assert first_match.start() < evidence < second_match.start(), (
            f"{path.name}: Cluster 11 must keep Step 1 → isolated-map evidence/signoff → Step 2"
        )


def test_cluster_8_records_receive_taps_and_local_relay_transfer_evidence():
    cluster = _guide_metadata(BUILD_HTML)["clusters"][7]
    unpowered = cluster["actions"]["unpowered_test"]
    powered = cluster["actions"]["powered_test"]

    for source, destination in (
        ("LOCAL_CONSOLE_6_STUB", "GPIO18_RX_LOCAL"),
        ("LOCAL_PIN3_STUB", "GPIO16_RX_LOCAL"),
    ):
        mapping = next(
            record
            for record in unpowered
            if source in record["points"] and destination in record["points"]
        )
        assert "10 kΩ" in mapping["expected"]
        assert "isolat" in mapping["expected"].lower()
        observation = next(
            record
            for record in powered
            if source in record["input"] and destination in record["output"]
        )
        assert observation["evidence"]
        assert observation["limits"]

    transfer = next(
        record
        for record in powered
        if "TX_DRV" in record["input"] and "LOCAL_TX_SELECTED" in record["output"]
    )
    assert "bounded" in transfer["instruction"].lower()
    assert "K1" in transfer["input"]
    assert transfer["evidence"]
    assert transfer["limits"]

    assert {
        "gpio18_rx_local_low_v",
        "gpio18_rx_local_high_v",
        "gpio16_rx_local_low_v",
        "gpio16_rx_local_high_v",
        "local_tx_selected_nc_ohm",
        "local_tx_selected_no_ohm",
    } <= set(cluster["evidence_fields"])
    section = _cluster_sections(_visible_html_text(BUILD_HTML), BUILD_HTML.name)[8]
    for label in (
        "GPIO18_RX_LOCAL low (V)",
        "GPIO18_RX_LOCAL high (V)",
        "GPIO16_RX_LOCAL low (V)",
        "GPIO16_RX_LOCAL high (V)",
        "LOCAL_TX_SELECTED NC (Ω)",
        "LOCAL_TX_SELECTED NO (Ω)",
    ):
        assert label in section


def test_build_pdf_has_two_substantive_cluster_11_pages():
    completed = subprocess.run(
        ["pdftotext", "-layout", str(BUILD_PDF), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    pages = [_normalize_text(page) for page in completed.stdout.split("\f")]
    pages = [page for page in pages if page]
    first = next(
        index
        for index, page in enumerate(pages)
        if "Cluster 11 — RJ45 pass-through and treadmill bypass" in page
    )
    cluster_11_pages = pages[first:]
    assert len(cluster_11_pages) == 2
    assert all(len(page) >= 1000 for page in cluster_11_pages)
    assert "Bypass-only controlled sequence" in cluster_11_pages[1]
    assert "dedicated bypass and thermal evidence" in cluster_11_pages[1]


def test_command_net_provenance_crosses_devkit_posts_at_cluster_5():
    clusters = {
        cluster["number"]: cluster
        for cluster in _guide_metadata(BUILD_HTML)["clusters"]
    }
    assert {"GPIO21_CMD_POST", "GPIO15_TX_ENABLE_POST"} <= set(clusters[3]["outputs"])
    assert {"RELAY_CMD", "TX_ENABLE"}.isdisjoint(clusters[3]["outputs"])

    sources = {source["input"]: source for source in clusters[5]["input_sources"]}
    assert sources["RELAY_CMD"]["source_output"] == "GPIO21_CMD_POST"
    assert sources["TX_ENABLE"]["source_output"] == "GPIO15_TX_ENABLE_POST"
    assert "jumper" in sources["RELAY_CMD"]["source_mapping"].lower()
    assert "jumper" in sources["TX_ENABLE"]["source_mapping"].lower()


def test_netlist_required_terminals_have_explicit_build_connections():
    clusters = {
        cluster["number"]: cluster
        for cluster in _guide_metadata(BUILD_HTML)["clusters"]
    }
    required = {
        4: {
            ("R_UV_TOP", "1", "VIN"),
            ("R_UV_TOP", "2", "UV_SENSE"),
            ("R_UV_BOTTOM", "1", "UV_SENSE"),
            ("R_UV_BOTTOM", "2", "GND"),
            ("C_UV", "1", "UV_SENSE"),
            ("C_UV", "2", "GND"),
            ("R_OV_TOP", "1", "VIN"),
            ("R_OV_TOP", "2", "OV_SENSE"),
            ("R_OV_BOTTOM", "1", "OV_SENSE"),
            ("R_OV_BOTTOM", "2", "GND"),
            ("C_OV", "1", "OV_SENSE"),
            ("C_OV", "2", "GND"),
            ("C_TPS3700", "1", "VIN"),
            ("C_TPS3700", "2", "GND"),
        },
        5: {
            ("R_RELAY_CMD_PD", "1", "RELAY_CMD"),
            ("R_RELAY_CMD_PD", "2", "GND"),
            ("R_TX_ENABLE_PD", "1", "TX_ENABLE"),
            ("R_TX_ENABLE_PD", "2", "GND"),
            ("C_AHC08", "1", "LOGIC_3V3"),
            ("C_AHC08", "2", "GND"),
            *(("U4", str(pin), "GND") for pin in (9, 10, 12, 13)),
        },
        6: {
            ("C_TPS709_IN", "1", "VIN"),
            ("C_TPS709_IN", "2", "GND"),
            ("C_TPS709_OUT", "positive", "+5V_RLY"),
            ("C_TPS709_OUT", "negative", "GND"),
            ("R_BASE_PD", "1", "Q_BASE"),
            ("R_BASE_PD", "2", "GND"),
        },
        7: {
            ("R_FB_NC", "1", "LOGIC_3V3"),
            ("R_FB_NC", "2", "K1_NC_FB"),
            ("R_FB_NO", "1", "LOGIC_3V3"),
            ("R_FB_NO", "2", "K1_NO_FB"),
        },
        8: {
            ("C_AHC126", "1", "LOGIC_3V3"),
            ("C_AHC126", "2", "GND"),
            *(("U6", str(pin), "GND") for pin in (4, 5, 9, 10, 12, 13)),
        },
        9: {
            ("R_VBUS_DISCHARGE", "1", "VBUS_SENSE"),
            ("R_VBUS_DISCHARGE", "2", "GND"),
            ("R_VBUS_PULLUP", "1", "LOGIC_3V3"),
            ("R_VBUS_PULLUP", "2", "VBUS_PRESENT_N"),
            ("DEVKIT", "GPIO7", "VBUS_PRESENT_N"),
        },
    }
    for number, expected in required.items():
        actual = {
            (record["part"], record["pin"], record["net"])
            for record in clusters[number]["wiring"]
        }
        assert expected <= actual, (
            f"cluster {number} omits netlist terminals: {sorted(expected - actual)}"
        )


def test_audit_cluster_11_isolates_maps_and_restores_each_common_conductor():
    cluster = _guide_metadata(AUDIT_HTML)["clusters"][10]
    actions = cluster["actions"]
    expected_links = {
        "CONSOLE.1 ↔ MOTOR.1 GND link",
        "CONSOLE.7 ↔ MOTOR.7 GND link",
        "CONSOLE.2 ↔ MOTOR.2 +8V_RAW link",
        "CONSOLE.8 ↔ MOTOR.8 +8V_RAW link",
    }
    opened = {
        step["link"]: step["instruction"]
        for step in actions["isolate"]
        if step["opens_link"]
    }
    restored = {
        step["link"]: step["instruction"]
        for step in actions["restore"]
        if step["restores_link"]
    }
    assert expected_links <= opened.keys()
    assert expected_links <= restored.keys()
    assert all("all sources off" in opened[link].lower() for link in expected_links)
    assert all(
        re.search(r"(?:all sources|power).{0,20}off", restored[link], re.IGNORECASE)
        for link in expected_links
    )
    mapping = " ".join(
        record["instruction"] + " " + record["expected"]
        for record in actions["unpowered_evidence"]
    ).lower()
    assert "map" in mapping and "before" in mapping and "restor" in mapping


def test_audit_cluster_6_exercises_relay_cmd_through_ahc08_and_restores_gpio21():
    cluster = _guide_metadata(AUDIT_HTML)["clusters"][5]
    actions = cluster["actions"]
    opened = {step["link"] for step in actions["isolate"] if step["opens_link"]}
    restored = {step["link"] for step in actions["restore"] if step["restores_link"]}
    assert "GPIO21 jumper" in opened
    assert "GPIO21 jumper" in restored

    measurement = actions["measure"][0]
    rendered = _normalize_text(json.dumps(measurement, ensure_ascii=False))
    assert "bounded RELAY_CMD injection" in rendered
    assert "RELAY_CMD → RELAY_GATE" in rendered
    assert "GPIO21 jumper removed" in rendered
    assert "valid TREAD_OK" in rendered
    assert "manual RELAY_GATE injection" not in rendered


def test_audit_cluster_11_installs_fused_dmm_harness_before_treadmill_power():
    actions = _guide_metadata(AUDIT_HTML)["clusters"][10]["actions"]
    sequence = actions["state_sequence"]
    install_index = next(
        index
        for index, record in enumerate(sequence)
        if record["stage"] == "install_harness"
    )
    install = _normalize_text(json.dumps(sequence[install_index], ensure_ascii=False))
    assert "all sources off" in install.lower()
    assert re.search(r"treadmill power.{0,30}(?:off|absent)", install, re.IGNORECASE)

    powered_index = next(
        index
        for index, record in enumerate(sequence)
        if record["stage"] == "apply_treadmill_power"
    )
    assert install_index < powered_index
    restore_text = " ".join(step["instruction"] for step in actions["restore"])
    assert re.search(
        r"(?:power|all sources).{0,30}off.{0,80}before.{0,30}(?:remove|removal).{0,30}harness",
        restore_text,
        re.IGNORECASE,
    )


def test_audit_cluster_7_uses_firmware_gpio21_without_external_fixture_drive():
    cluster = _guide_metadata(AUDIT_HTML)["clusters"][6]
    actions = cluster["actions"]
    isolation = _normalize_text(json.dumps(actions["isolate"], ensure_ascii=False))
    assert "GPIO21 jumper remains installed" in isolation
    assert not any(
        step["opens_link"] and step.get("link") == "GPIO21 jumper"
        for step in actions["isolate"]
    )

    rendered = _normalize_text(json.dumps(actions["measure"], ensure_ascii=False))
    assert "fixture" not in rendered.lower()
    assert "GPIO21 jumper removed" not in rendered
    assert "manual injection" not in rendered.lower()
    assert all(
        "bounded relay exerciser firmware" in record["firmware_identity"].lower()
        for record in actions["measure"]
    )

    usb_release = next(
        record for record in actions["measure"] if "USB logic" in record["net"]
    )
    assert usb_release["ledger_stage"] == 2
    assert "disconnect usb" in actions["state_sequence"][1]["action"].lower()
    assert "removes GPIO21 drive" in usb_release["observation_state"]
    gpio_release = next(
        record for record in actions["measure"] if "GPIO21 command" in record["net"]
    )
    assert gpio_release["ledger_stage"] == 4
    assert "firmware deassert" in actions["state_sequence"][3]["action"].lower()
    assert "logic powered" in gpio_release["observation_state"].lower()
    tread_release = next(
        record for record in actions["measure"] if "TREAD_OK removal" in record["net"]
    )
    assert "USB logic remains powered" in tread_release["observation_state"]
    vin_release = next(
        record for record in actions["measure"] if "VIN removal" in record["net"]
    )
    assert "USB logic remains powered" in vin_release["observation_state"]


def test_audit_cluster_11_state_sequence_restores_ground_before_harness():
    actions = _guide_metadata(AUDIT_HTML)["clusters"][10]["actions"]
    sequence = actions["state_sequence"]
    expected_stages = [
        "map_complete",
        "restore_ground_1",
        "restore_ground_7",
        "verify_ground_return",
        "install_harness",
        "apply_treadmill_power",
        "power_off",
        "remove_harness",
        "restore_plus8_2",
        "restore_plus8_8",
        "drop_test",
        "thermal_hold",
    ]
    assert [step["stage"] for step in sequence] == expected_stages
    for step in sequence[:5]:
        assert "all sources off" in step["state"].lower()

    ground_check = sequence[3]
    assert (
        "ground return continuity"
        in (ground_check["action"] + " " + ground_check["evidence"]).lower()
    )
    harness = sequence[4]
    assert "+8" in harness["action"]
    assert "GND" not in harness["action"]
    assert "treadmill power remains off" in harness["state"].lower()

    plus8_links = {
        "CONSOLE.2 ↔ MOTOR.2 +8V_RAW link",
        "CONSOLE.8 ↔ MOTOR.8 +8V_RAW link",
    }
    restored = {step["link"]: step["instruction"] for step in actions["restore"]}
    for link in plus8_links:
        assert re.search(
            r"(?:remove|removal).{0,30}harness.{0,100}restor",
            restored[link],
            re.IGNORECASE,
        )


def test_audit_cluster_7_rearms_before_every_destructive_release():
    actions = _guide_metadata(AUDIT_HTML)["clusters"][6]["actions"]
    sequence = actions["state_sequence"]
    expected_stages = [
        "rearm_usb_loss",
        "release_usb",
        "rearm_gpio21",
        "release_gpio21",
        "rearm_tread_ok",
        "release_tread_ok",
        "rearm_vin",
        "release_vin",
        "rearm_thermal",
        "thermal_hold",
    ]
    assert [step["stage"] for step in sequence] == expected_stages
    for step in sequence[::2]:
        state = step["state"]
        assert "USB logic powered" in state
        assert "VIN 8.00 V" in state
        assert "TREAD_OK high" in state
        assert "GPIO21 asserted by bounded firmware" in state
        assert "relay energized" in step["evidence"]
    assert "disconnect usb" in sequence[1]["action"].lower()
    assert "firmware deassert GPIO21" in sequence[3]["action"]
    assert "below UV" in sequence[5]["action"]
    assert "switch bench vin off" in sequence[7]["action"].lower()
    assert "five-minute" in sequence[9]["action"]


def test_audit_cluster_8_keeps_gpio21_connected_for_firmware_relay_transfer():
    actions = _guide_metadata(AUDIT_HTML)["clusters"][7]["actions"]
    isolation = _normalize_text(json.dumps(actions["isolate"], ensure_ascii=False))
    measurements = _normalize_text(json.dumps(actions["measure"], ensure_ascii=False))
    assert "GPIO21 jumper remains installed" in isolation
    assert "GPIO21 jumper remains installed" in measurements
    assert "bounded relay exerciser firmware" in measurements.lower()
    assert "GPIO21 jumper removed" not in isolation + " " + measurements

    opened = {step["link"] for step in actions["isolate"] if step["opens_link"]}
    restored = {step["link"] for step in actions["restore"] if step["restores_link"]}
    assert opened == restored == {"GPIO15 jumper"}


def test_audit_c7_c11_measurements_are_evidence_only_and_keyed_to_ledgers():
    clusters = {
        cluster["number"]: cluster
        for cluster in _guide_metadata(AUDIT_HTML)["clusters"]
    }
    expected_measure_stages = {
        7: [1, 2, 4, 6, 8, 10],
        11: [6, 6, 11, 12],
    }
    for number, expected in expected_measure_stages.items():
        actions = clusters[number]["actions"]
        steps = actions["state_sequence"]
        assert [step["step"] for step in steps] == list(range(1, len(steps) + 1))
        measurements = actions["measure"]
        assert [record["ledger_stage"] for record in measurements] == expected
        for record in measurements:
            stage = record["ledger_stage"]
            assert record["instruction"].startswith(
                "Evidence only — do not repeat operations; enter result captured during Ledger stage "
            )
            assert record["stimulus"].startswith(
                f"Captured during Ledger stage {stage}:"
            )

    c11_actions = clusters[11]["actions"]
    receive_only = c11_actions["measure"][0]
    receive_stage = c11_actions["state_sequence"][receive_only["ledger_stage"] - 1]
    powered_contract = " ".join(
        str(receive_stage[field]) for field in ("state", "action", "evidence")
    ).lower()
    assert "treadmill power" in powered_contract
    assert "observer" in powered_contract
    assert "relay off" in powered_contract
    assert "tx disabled" in powered_contract
    assert "receive-only" in powered_contract


def test_guides_use_canonical_relay_coil_net_and_audit_c7_bidirectional_tvs():
    for path in (BUILD_HTML, AUDIT_HTML):
        assert "COIL_LOW" not in path.read_text()
    for path in (BUILD_PDF, AUDIT_PDF):
        assert "COIL_LOW" not in _pdf_text(path)

    cluster7 = _guide_metadata(AUDIT_HTML)["clusters"][6]
    inspection = _normalize_text(
        json.dumps(cluster7["actions"]["inspect"], ensure_ascii=False)
    )
    assert "bidirectional tvs has no cathode orientation" in inspection.lower()
    assert "K1 pins 1 and 16" in inspection
    assert "+5V_RLY" in inspection
    assert "RELAY_COIL−" in inspection
    assert "cathode toward +5V_RLY" not in inspection
