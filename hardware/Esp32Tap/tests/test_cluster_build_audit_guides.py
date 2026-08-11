from __future__ import annotations

import html
import json
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
BRINGUP = ROOT / "hardware/Esp32Tap/bringup"
BUILD_HTML = BRINGUP / "esp32tap-cluster-build-and-test.html"
BUILD_PDF = BRINGUP / "esp32tap-cluster-build-and-test.pdf"
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


def _pdf_text(path: Path) -> str:
    assert path.is_file(), f"{path.name}: guide PDF is missing"
    completed = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    return _normalize_text(completed.stdout)


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
    assert {"operator", "date", "signed_pass"} <= normalized, (
        f"{guide_name} cluster {cluster['number']}: operator, date, and signed-pass fields are required"
    )


def _assert_dependency_contract(cluster: dict, guide_name: str) -> None:
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
        assert upstream is None or upstream in dependencies, (
            f"{guide_name} cluster {number}: input source {upstream!r} is not a declared dependency"
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
    assert cause_stages == ["source", "component", "output"], (
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
        _assert_dependency_contract(cluster, path.name)
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
