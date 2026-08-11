from __future__ import annotations

import html
import json
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
BRINGUP = ROOT / "hardware/Esp32Tap/bringup"
BUILD_HTML = BRINGUP / "esp32tap-cluster-build-guide.html"
BUILD_PDF = BRINGUP / "esp32tap-cluster-build-guide.pdf"
AUDIT_HTML = BRINGUP / "esp32tap-cluster-audit-guide.html"
AUDIT_PDF = BRINGUP / "esp32tap-cluster-audit-guide.pdf"
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
    r"(?<![A-Za-z0-9_.])(?:[a-j](?:[1-9]|[1-5]\d|6[0-3])|[+-](?:[1-5]\d|6[0-3]))(?![A-Za-z0-9_.])"
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

# Each expression is intentionally about an observable bench contract, rather
# than an exact sentence, so formatting and explanatory prose may evolve.
COMMON_SAFETY_AND_EVIDENCE = {
    "8.00 V source": r"8\.00\s*V",
    "250 mA initial current limit": r"250\s*mA.{0,40}initial|initial.{0,40}250\s*mA",
    "coil-open current below 50 mA": r"(?:below|<)\s*50\s*mA.{0,50}coil.?open|coil.?open.{0,50}(?:below|<)\s*50\s*mA",
    "VIN window": r"VIN.{0,30}7\.20\s*[–-]\s*7\.90\s*V",
    "LOGIC_3V3 window": r"LOGIC_3V3.{0,30}3\.20\s*[–-]\s*3\.40\s*V",
    "TREAD_OK three-state behavior": r"TREAD_OK.{0,180}low.{0,50}(?:below|under).{0,30}UV.{0,80}high.{0,50}8\.00\s*V.{0,80}low.{0,50}(?:above|over).{0,30}OV",
    "UV threshold": r"UV.{0,40}6\.25\s*[–-]\s*6\.55\s*V",
    "OV falling threshold": r"OV.{0,30}falling.{0,30}10\.30\s*[–-]\s*10\.90\s*V",
    "TPS709 enabled output": r"TPS709.{0,50}(?:enabled|enable).{0,40}4\.75\s*[–-]\s*5\.25\s*V",
    "TPS709 disabled output": r"TPS709.{0,50}(?:disabled|disable).{0,40}(?:below|<)\s*0\.25\s*V",
    "loaded-relay current limit": r"loaded.{0,30}relay.{0,50}(?:no more than|≤|<=)\s*500\s*mA",
    "relay coil current": r"coil.{0,40}90\s*[–-]\s*110\s*mA",
    "relay coil voltage": r"coil.{0,40}(?:≥|>=|at least)\s*4\.50\s*V",
    "BC337 saturation": r"BC337.{0,40}VCE.{0,20}(?:≤|<=|no more than)\s*0\.30\s*V",
    "feedback truth table": r"\(1\s*,\s*0\).{0,40}energized.{0,80}\(0\s*,\s*1\).{0,40}bypass.{0,100}(?:00|0\s*,\s*0).{0,40}(?:11|1\s*,\s*1).{0,40}fault",
    "relay release time": r"release.{0,30}(?:≤|<=|no more than)\s*100\s*ms",
    "five-minute thermal hold": r"(?:five|5)[ -]minute.{0,80}(?:≤|<=|no more than)\s*45\s*°?C.{0,80}(?:≤|<=|no more than)\s*10\s*°?C.{0,30}(?:over|above)\s*ambient",
    "separate treadmill current": r"treadmill.{0,40}current.{0,40}(?:separate|separately).{0,40}(?:≤|<=|no more than)\s*500\s*mA|(?:separate|separately).{0,40}treadmill.{0,40}current.{0,40}(?:≤|<=|no more than)\s*500\s*mA",
    "pass-through voltage drops": r"drop(?:s)?.{0,40}(?:≤|<=|no more than)\s*50\s*mV",
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


def _assert_each_cluster_has_workflow_and_evidence(
    text: str, guide_name: str, workflow_labels: tuple[str, ...]
) -> None:
    heading_matches = [
        re.search(rf"Cluster\s+{number}\s*(?:—|-)\s*{re.escape(name)}", text)
        for number, name in EXPECTED_CLUSTERS.items()
    ]
    assert all(heading_matches), f"{guide_name}: cannot identify every cluster section"
    starts = [match.start() for match in heading_matches if match]
    for index, (number, _) in enumerate(EXPECTED_CLUSTERS.items()):
        section = text[
            starts[index] : starts[index + 1] if index + 1 < len(starts) else None
        ]
        for label in (*workflow_labels, "Operator", "Date", "Signed PASS"):
            assert label in section, f"{guide_name} cluster {number}: missing {label!r}"


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

    for cluster in clusters:
        assert SHARED_CLUSTER_FIELDS <= cluster.keys(), (
            f"{path.name} cluster {cluster.get('number')}: incomplete shared contract"
        )
        for field in ("inputs", "outputs", "stop_gate", "pass_gate"):
            assert cluster[field], (
                f"{path.name} cluster {cluster['number']}: {field} must be nonempty"
            )
        _assert_operator_evidence(cluster, path.name)
        if expected_mode == "assembled_board_audit":
            actions = cluster.get("actions")
            assert isinstance(actions, dict), (
                f"{path.name} cluster {cluster['number']}: actions must be an object"
            )
            for action in ("isolate", "measure", "restore"):
                assert actions.get(action), (
                    f"{path.name} cluster {cluster['number']}: {action} action must be nonempty"
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
    for hole in ("a29", "f36", "-52"):
        assert PRESCRIBED_BREADBOARD_HOLE.fullmatch(hole)
    for allowed in ("A29", "GPIO29", "U6.29", "3.29 V", "-5.2 V", "+8.00 V", "VIN_A29"):
        assert PRESCRIBED_BREADBOARD_HOLE.search(allowed) is None


@pytest.mark.parametrize(
    ("path", "labels"),
    ((BUILD_HTML, BUILD_LABELS), (AUDIT_HTML, AUDIT_LABELS)),
)
def test_html_contains_mode_workflow_and_safety_contracts(
    path: Path, labels: tuple[str, ...]
):
    text = _visible_html_text(path)
    _assert_headings_in_order(text, path.name)
    for label in labels:
        assert label in text, f"{path.name}: missing {label!r} workflow label"
    per_cluster_labels = labels[:-1] if path == BUILD_HTML else labels
    _assert_each_cluster_has_workflow_and_evidence(text, path.name, per_cluster_labels)
    _assert_contract_language(text, path.name)


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
    ("path", "mode_label", "workflow_labels"),
    (
        (BUILD_PDF, "Empty-board build", BUILD_LABELS[:-1]),
        (AUDIT_PDF, "Assembled-board audit", AUDIT_LABELS),
    ),
)
def test_pdf_preserves_contracts_and_has_letter_page_size(
    path: Path, mode_label: str, workflow_labels: tuple[str, ...]
):
    text = _pdf_text(path)
    _assert_headings_in_order(text, path.name)
    assert mode_label in text, f"{path.name}: missing mode label"
    for label in (*workflow_labels, *IDENTITY_AND_MANIFEST_LABELS, "STOP", "PASS"):
        assert label in text, f"{path.name}: missing {label!r}"
    _assert_each_cluster_has_workflow_and_evidence(text, path.name, workflow_labels)
    _assert_contract_language(text, path.name)
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
