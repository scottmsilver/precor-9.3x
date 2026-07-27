from __future__ import annotations

import csv
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

SCHEMATIC_TITLE = "Esp32Tap Rev E - ESP32-S3 Precor serial-bus tap"
SCHEMATIC_DATE = "2026-07-24"
GENERATED_SCHEMATIC_FILES = (
    "Esp32Tap.kicad_sch",
    "esp32tap.kicad_sym",
    "sym-lib-table",
)
DETERMINISTIC_ARTIFACTS = (
    ("kicad/Esp32Tap.kicad_sch", "kicad/Esp32Tap.kicad_sch"),
    ("kicad/esp32tap.kicad_sym", "kicad/esp32tap.kicad_sym"),
    ("kicad/sym-lib-table", "kicad/sym-lib-table"),
    ("NETLIST.md", "NETLIST.md"),
    ("bom/BOM.csv", "bom/BOM.csv"),
)
ALLOWED_ERC_IGNORED_CHECKS = {
    "Global label only appears once in the schematic",
    "Four connection points are joined together",
    "SPICE model issue",
    "Assigned footprint doesn't match footprint filters",
}
EXPECTED_POWER_FLAGS = {
    "#FLG01": "GND",
    "#FLG02": "VIN",
    "#FLG03": "+3V3",
    "#FLG04": "VBUS",
}
SEXPR_TOKEN = re.compile(r'\s*(\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+)')


def _load_tool_module(path: Path, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    sys.path.insert(0, str(path.parent))
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def _parse_sexpr(source: str) -> list[Any]:
    roots: list[Any] = []
    stack: list[list[Any]] = [roots]
    for match in SEXPR_TOKEN.finditer(source):
        token = match.group(1)
        if token == "(":
            child: list[Any] = []
            stack[-1].append(child)
            stack.append(child)
        elif token == ")":
            assert len(stack) > 1, "unexpected closing parenthesis"
            stack.pop()
        else:
            stack[-1].append(json.loads(token) if token.startswith('"') else token)
    assert len(stack) == 1, "unterminated S-expression"
    assert len(roots) == 1 and isinstance(roots[0], list)
    return roots[0]


def _tag(node: Any) -> str | None:
    if isinstance(node, list) and node and isinstance(node[0], str):
        return node[0]
    return None


def _atom(value: Any) -> Any:
    return value


def _children(node: list[Any], tag: str) -> list[list[Any]]:
    return [child for child in node[1:] if isinstance(child, list) and _tag(child) == tag]


def _child(node: list[Any], tag: str) -> list[Any]:
    matches = _children(node, tag)
    assert len(matches) == 1, f"expected one {tag!r}, found {len(matches)}"
    return matches[0]


def _properties(node: list[Any]) -> dict[str, str]:
    return {str(_atom(prop[1])): str(_atom(prop[2])) for prop in _children(node, "property")}


def _recursive_children(node: list[Any], tag: str) -> list[list[Any]]:
    matches = []
    for child in node[1:]:
        if not isinstance(child, list):
            continue
        if _tag(child) == tag:
            matches.append(child)
        matches.extend(_recursive_children(child, tag))
    return matches


def _library_pins(symbol: list[Any]) -> dict[str, list[Any]]:
    pins = {}
    for pin in _recursive_children(symbol, "pin"):
        numbers = _children(pin, "number")
        if not numbers:
            continue
        number = str(_atom(numbers[0][1]))
        assert number not in pins, f"duplicate library pin {number}"
        pins[number] = pin
    return pins


def _position(node: list[Any]) -> tuple[float, float]:
    at = _child(node, "at")
    return round(float(at[1]), 4), round(float(at[2]), 4)


def _instances(schematic: list[Any]) -> dict[str, list[Any]]:
    instances = {}
    for symbol in _children(schematic, "symbol"):
        properties = _properties(symbol)
        reference = properties["Reference"]
        assert reference not in instances, f"duplicate reference {reference}"
        instances[reference] = symbol
    return instances


def _library_symbols(schematic: list[Any]) -> dict[str, list[Any]]:
    container = _child(schematic, "lib_symbols")
    return {str(_atom(symbol[1])): symbol for symbol in _children(container, "symbol")}


@pytest.fixture(scope="module")
def schematic(esp32tap_dir: Path) -> list[Any]:
    path = esp32tap_dir / "kicad" / "Esp32Tap.kicad_sch"
    parsed = _parse_sexpr(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, list)
    return parsed


def test_schematic_title_block_identifies_rev_d(
    schematic: list[Any],
) -> None:
    title_block = _child(schematic, "title_block")
    assert _atom(_child(title_block, "title")[1]) == SCHEMATIC_TITLE
    assert _atom(_child(title_block, "rev")[1]) == "E"
    assert _atom(_child(title_block, "date")[1]) == SCHEMATIC_DATE
    comments = {int(comment[1]): str(_atom(comment[2])) for comment in _children(title_block, "comment")}
    assert comments[1] == "Status: generated Rev E typed schematic"
    assert comments[2] == "Source of truth: tools/design.py"


def test_schematic_components_and_pin_types_exactly_match_design(
    schematic: list[Any],
    design: SimpleNamespace,
) -> None:
    instances = _instances(schematic)
    libraries = _library_symbols(schematic)
    design_refs = set(design.COMPONENTS)
    physical_refs = {reference for reference in instances if not reference.startswith("#FLG")}
    assert physical_refs == design_refs

    for reference, component in design.COMPONENTS.items():
        value, footprint_lib, footprint, lcsc, *_metadata, pin_names = component
        instance = instances[reference]
        properties = _properties(instance)
        assert properties["Reference"] == reference
        assert properties["Value"] == value
        assert properties["Footprint"] == f"{footprint_lib}:{footprint}"
        assert properties["LCSC"] == lcsc
        assert _atom(_child(instance, "dnp")[1]) == ("yes" if reference in design.DNP else "no")
        expected_in_bom = reference not in design.DNP and component[4] != "none"
        assert _atom(_child(instance, "in_bom")[1]) == ("yes" if expected_in_bom else "no")

        instance_pads = {str(_atom(pin[1])) for pin in _children(instance, "pin")}
        assert instance_pads == set(pin_names)

        library_id = str(_atom(_child(instance, "lib_id")[1]))
        assert library_id in libraries
        assert _atom(_child(libraries[library_id], "in_bom")[1]) == ("yes" if expected_in_bom else "no")
        library_pins = _library_pins(libraries[library_id])
        assert set(library_pins) == set(pin_names)
        for pad, name in pin_names.items():
            library_pin = library_pins[pad]
            assert str(_atom(library_pin[1])) == design.PIN_TYPES[(reference, pad)]
            assert str(_atom(_child(library_pin, "name")[1])) == name

    for reference in set(instances) - design_refs:
        helper = instances[reference]
        properties = _properties(helper)
        assert reference.startswith("#FLG")
        assert _atom(_child(helper, "in_bom")[1]) == "no"
        assert _atom(_child(helper, "on_board")[1]) == "no"
        assert properties["Footprint"] == ""
        assert properties.get("LCSC", "") == ""


def test_schematic_connectivity_exactly_matches_design(
    schematic: list[Any],
    design: SimpleNamespace,
) -> None:
    instances = _instances(schematic)
    libraries = _library_symbols(schematic)
    labels_by_position: dict[tuple[float, float], list[str]] = defaultdict(list)
    for label in _children(schematic, "global_label"):
        labels_by_position[_position(label)].append(str(_atom(label[1])))
    no_connect_positions = {_position(marker) for marker in _children(schematic, "no_connect")}

    expected_net = {pin: net for net, pins in design.NETS.items() for pin in pins}
    nc = set(design.NC)
    actual_connected_pins = set()
    actual_nc_pins = set()

    for reference, component in design.COMPONENTS.items():
        instance = instances[reference]
        instance_at = _child(instance, "at")
        assert float(instance_at[3]) == 0.0
        instance_x = float(instance_at[1])
        instance_y = float(instance_at[2])
        library_id = str(_atom(_child(instance, "lib_id")[1]))
        library_pins = _library_pins(libraries[library_id])

        for pad in component[7]:
            pin_at = _child(library_pins[pad], "at")
            assert float(pin_at[3]) == 0.0
            position = (
                round(instance_x + float(pin_at[1]), 4),
                round(instance_y - float(pin_at[2]), 4),
            )
            design_pin = (reference, pad)
            if design_pin in nc:
                assert position in no_connect_positions
                assert not labels_by_position[position]
                actual_nc_pins.add(design_pin)
            else:
                assert labels_by_position[position] == [expected_net[design_pin]]
                assert position not in no_connect_positions
                actual_connected_pins.add(design_pin)

    assert actual_connected_pins == set(expected_net)
    assert actual_nc_pins == nc
    assert set(expected_net).isdisjoint(nc)


def test_schematic_power_flags_are_explicit_non_assembly_helpers(
    schematic: list[Any],
) -> None:
    instances = _instances(schematic)
    libraries = _library_symbols(schematic)
    assert {
        reference: _properties(instance)["Value"]
        for reference, instance in instances.items()
        if reference.startswith("#FLG")
    } == {reference: "PWR_FLAG" for reference in EXPECTED_POWER_FLAGS}

    labels_by_position = {_position(label): str(_atom(label[1])) for label in _children(schematic, "global_label")}
    for reference, net in EXPECTED_POWER_FLAGS.items():
        instance = instances[reference]
        properties = _properties(instance)
        assert _atom(_child(instance, "in_bom")[1]) == "no"
        assert _atom(_child(instance, "on_board")[1]) == "no"
        assert properties["Footprint"] == ""
        assert properties["LCSC"] == ""
        assert {str(_atom(pin[1])) for pin in _children(instance, "pin")} == {"1"}

        library_id = str(_atom(_child(instance, "lib_id")[1]))
        library_pin = _library_pins(libraries[library_id])["1"]
        assert str(_atom(library_pin[1])) == "power_out"
        instance_x, instance_y = _position(instance)
        pin_x, pin_y = _position(library_pin)
        absolute_position = (
            round(instance_x + pin_x, 4),
            round(instance_y - pin_y, 4),
        )
        assert labels_by_position[absolute_position] == net


def test_schematic_generation_is_deterministic(
    tmp_path: Path,
    esp32tap_dir: Path,
) -> None:
    sandbox = tmp_path / "Esp32Tap"
    tools = sandbox / "tools"
    kicad = sandbox / "kicad"
    tools.mkdir(parents=True)
    kicad.mkdir()
    for filename in ("design.py", "gen_sch.py"):
        shutil.copy2(esp32tap_dir / "tools" / filename, tools / filename)

    command = [sys.executable, "gen_sch.py"]
    first_run = subprocess.run(
        command,
        cwd=tools,
        check=False,
        capture_output=True,
        text=True,
    )
    assert first_run.returncode == 0, first_run.stderr
    first = {filename: (kicad / filename).read_bytes() for filename in GENERATED_SCHEMATIC_FILES}

    second_run = subprocess.run(
        command,
        cwd=tools,
        check=False,
        capture_output=True,
        text=True,
    )
    assert second_run.returncode == 0, second_run.stderr
    second = {filename: (kicad / filename).read_bytes() for filename in GENERATED_SCHEMATIC_FILES}
    assert second == first


def test_uuid_identity_encoding_cannot_alias_slash_containing_parts(
    esp32tap_dir: Path,
) -> None:
    generator = _load_tool_module(
        esp32tap_dir / "tools" / "gen_sch.py",
        "_esp32tap_gen_sch_uuid_test",
    )
    assert generator.stable_uuid("label", "A/B", "C") != (generator.stable_uuid("label", "A", "B/C"))


def test_schematic_render_rejects_duplicate_uuids(
    esp32tap_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _load_tool_module(
        esp32tap_dir / "tools" / "gen_sch.py",
        "_esp32tap_gen_sch_collision_test",
    )
    duplicate = "00000000-0000-5000-8000-000000000000"
    monkeypatch.setattr(
        generator,
        "stable_uuid",
        lambda *_identity: duplicate,
    )
    with pytest.raises(ValueError, match="duplicate schematic UUID"):
        generator.render_schematic()


def _seed_schematic_outputs(output_directory: Path) -> dict[Path, bytes]:
    output_directory.mkdir(parents=True)
    sentinels = {output_directory / filename: f"old {filename}\n".encode() for filename in GENERATED_SCHEMATIC_FILES}
    sentinels[output_directory / "Esp32Tap.kicad_pro"] = b'{"old": true}\n'
    for path, content in sentinels.items():
        path.write_bytes(content)
    return sentinels


def test_schematic_staging_failure_preserves_every_destination(
    tmp_path: Path,
    esp32tap_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _load_tool_module(
        esp32tap_dir / "tools" / "gen_sch.py",
        "_esp32tap_gen_sch_stage_failure_test",
    )
    output_directory = tmp_path / "kicad"
    sentinels = _seed_schematic_outputs(output_directory)
    original_write_text = Path.write_text

    def fail_during_staging(
        path: Path,
        data: str,
        *args: Any,
        **kwargs: Any,
    ) -> int:
        if path.name == "sym-lib-table":
            raise RuntimeError("injected staged render failure")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_during_staging)
    with pytest.raises(RuntimeError, match="injected staged render failure"):
        generator.write_outputs(output_directory)

    assert {path: path.read_bytes() for path in sentinels} == sentinels


def test_schematic_validation_failure_preserves_every_destination(
    tmp_path: Path,
    esp32tap_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = _load_tool_module(
        esp32tap_dir / "tools" / "gen_sch.py",
        "_esp32tap_gen_sch_validation_failure_test",
    )
    output_directory = tmp_path / "kicad"
    sentinels = _seed_schematic_outputs(output_directory)

    def fail_validation(_staging_directory: Path) -> None:
        raise RuntimeError("injected KiCad validation failure")

    monkeypatch.setattr(
        generator,
        "validate_staged_outputs",
        fail_validation,
        raising=False,
    )
    with pytest.raises(
        RuntimeError,
        match="injected KiCad validation failure",
    ):
        generator.write_outputs(output_directory)

    assert {path: path.read_bytes() for path in sentinels} == sentinels


def _fake_design_source() -> str:
    return """\
def part(
    footprint_library="Resistor_SMD",
    footprint="R_0603_1608Metric",
    lcsc="C1000",
    jlc_class="Basic",
    cost=0.001,
    description="shared role",
):
    return (
        "10k", footprint_library, footprint, lcsc, jlc_class, cost,
        description, {"1": "1"},
    )


COMPONENTS = {
    "R1": part(lcsc="C1001", jlc_class="Basic"),
    "R2": part(lcsc="C1001", jlc_class="Extended"),
    "R3": part(lcsc="C1002", cost=0.001),
    "R4": part(lcsc="C1002", cost=0.125),
    "R5": part(lcsc="C1003", description="first 10k role"),
    "R6": part(lcsc="C1003", description="unrelated second 10k role"),
    "R7": part(lcsc="C1004", footprint_library="Library_A"),
    "R8": part(lcsc="C1004", footprint_library="Library_B"),
    "R9": part(lcsc="C1005", footprint="Footprint_A"),
    "R10": part(lcsc="C1005", footprint="Footprint_B"),
    "R11": part(lcsc="C1006"),
    "R12": part(lcsc="C1007"),
    "C13": (
        "DNP", "Capacitor_SMD", "C_0603_1608Metric", "",
        "DNP", 0.0, "optional tuning capacitor", {"1": "1"},
    ),
    "TP1": (
        "SIG", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "",
        "none", 0.0, "bare test pad", {"1": "1"},
    ),
}
NETS = {
    reference: [(reference, "1")]
    for reference in COMPONENTS
}
NC = []
DNP = {"C13"}

def validate():
    return None
"""


def _docs_sandbox(
    tmp_path: Path,
    esp32tap_dir: Path,
    pcbnew_source: str,
    design_source: str | None = None,
) -> tuple[Path, Path]:
    sandbox = tmp_path / "Esp32Tap"
    tools = sandbox / "tools"
    (sandbox / "bom").mkdir(parents=True)
    (sandbox / "kicad").mkdir()
    tools.mkdir()
    shutil.copy2(esp32tap_dir / "tools" / "gen_docs.py", tools)
    (tools / "design.py").write_text(
        design_source or _fake_design_source(),
        encoding="utf-8",
    )
    (tools / "pcbnew.py").write_text(pcbnew_source, encoding="utf-8")
    return sandbox, tools


def test_skip_cpl_avoids_pcbnew_and_preserves_cpl(
    tmp_path: Path,
    esp32tap_dir: Path,
) -> None:
    sandbox, tools = _docs_sandbox(
        tmp_path,
        esp32tap_dir,
        'raise RuntimeError("pcbnew must not be imported")\n',
    )
    cpl = sandbox / "bom" / "CPL-positions.csv"
    sentinel = b"existing Rev A CPL must remain byte-identical\n"
    cpl.write_bytes(sentinel)
    cpl.chmod(0o444)
    before = cpl.stat()

    completed = subprocess.run(
        [sys.executable, "gen_docs.py", "--skip-cpl"],
        cwd=tools,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert cpl.read_bytes() == sentinel
    after = cpl.stat()
    assert (
        after.st_mode,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) == (
        before.st_mode,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )


def test_gen_docs_rejects_unknown_arguments_clearly(
    tmp_path: Path,
    esp32tap_dir: Path,
) -> None:
    _sandbox, tools = _docs_sandbox(
        tmp_path,
        esp32tap_dir,
        'raise RuntimeError("pcbnew must not be imported")\n',
    )
    completed = subprocess.run(
        [sys.executable, "gen_docs.py", "--not-a-real-option"],
        cwd=tools,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "unrecognized arguments: --not-a-real-option" in completed.stderr


def test_bom_keeps_same_value_parts_with_distinct_metadata_separate(
    tmp_path: Path,
    esp32tap_dir: Path,
) -> None:
    sandbox, tools = _docs_sandbox(
        tmp_path,
        esp32tap_dir,
        'raise RuntimeError("pcbnew must not be imported")\n',
    )
    completed = subprocess.run(
        [sys.executable, "gen_docs.py", "--skip-cpl"],
        cwd=tools,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    with (sandbox / "bom" / "BOM.csv").open(
        newline="",
        encoding="utf-8",
    ) as bom_file:
        rows = list(csv.DictReader(bom_file))
    expected_references = {f"R{number}" for number in range(1, 13)}
    assert {row["Designator"] for row in rows} == expected_references
    by_reference = {row["Designator"]: row for row in rows}
    assert by_reference["R1"]["JLC class"] == "Basic"
    assert by_reference["R2"]["JLC class"] == "Extended"
    assert by_reference["R3"]["Unit cost (USD)"] == "0.001"
    assert by_reference["R4"]["Unit cost (USD)"] == "0.125"
    assert by_reference["R5"]["Description"] == "first 10k role"
    assert by_reference["R6"]["Description"] == "unrelated second 10k role"
    assert by_reference["R7"]["Footprint"] == by_reference["R8"]["Footprint"]
    assert by_reference["R9"]["Footprint"] == "Footprint_A"
    assert by_reference["R10"]["Footprint"] == "Footprint_B"
    assert by_reference["R11"]["LCSC Part #"] == "C1006"
    assert by_reference["R12"]["LCSC Part #"] == "C1007"


def _seed_doc_outputs(sandbox: Path) -> dict[Path, bytes]:
    sentinels = {
        sandbox / "NETLIST.md": b"old netlist\n",
        sandbox / "bom" / "BOM.csv": b"old bom\n",
        sandbox / "bom" / "CPL-positions.csv": b"old cpl\n",
    }
    for path, content in sentinels.items():
        path.write_bytes(content)
    return sentinels


def _assert_files_unchanged(sentinels: dict[Path, bytes]) -> None:
    assert {path: path.read_bytes() for path in sentinels} == sentinels


def test_default_docs_dependency_failure_is_atomic(
    tmp_path: Path,
    esp32tap_dir: Path,
) -> None:
    sandbox, tools = _docs_sandbox(
        tmp_path,
        esp32tap_dir,
        'raise RuntimeError("injected pcbnew import failure")\n',
    )
    (sandbox / "kicad" / "Esp32Tap.kicad_pcb").write_text(
        "(kicad_pcb)",
        encoding="utf-8",
    )
    sentinels = _seed_doc_outputs(sandbox)

    completed = subprocess.run(
        [sys.executable, "gen_docs.py"],
        cwd=tools,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "injected pcbnew import failure" in completed.stderr
    _assert_files_unchanged(sentinels)


def test_default_docs_invalid_board_is_atomic(
    tmp_path: Path,
    esp32tap_dir: Path,
) -> None:
    pcbnew_source = """\
def LoadBoard(_path):
    return None


def ToMM(value):
    return value
"""
    sandbox, tools = _docs_sandbox(
        tmp_path,
        esp32tap_dir,
        pcbnew_source,
    )
    (sandbox / "kicad" / "Esp32Tap.kicad_pcb").write_text(
        "(kicad_pcb)",
        encoding="utf-8",
    )
    sentinels = _seed_doc_outputs(sandbox)

    completed = subprocess.run(
        [sys.executable, "gen_docs.py"],
        cwd=tools,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    _assert_files_unchanged(sentinels)


def test_docs_render_failure_is_atomic(
    tmp_path: Path,
    esp32tap_dir: Path,
) -> None:
    design_source = (
        _fake_design_source()
        + """\

class ExplodingCost:
    def __rmul__(self, _quantity):
        raise RuntimeError("injected BOM render failure")

    def __format__(self, _format_spec):
        raise RuntimeError("injected BOM render failure")


component = list(COMPONENTS["R3"])
component[5] = ExplodingCost()
COMPONENTS["R3"] = tuple(component)
"""
    )
    sandbox, tools = _docs_sandbox(
        tmp_path,
        esp32tap_dir,
        'raise RuntimeError("pcbnew must not be imported")\n',
        design_source,
    )
    sentinels = _seed_doc_outputs(sandbox)

    completed = subprocess.run(
        [sys.executable, "gen_docs.py", "--skip-cpl"],
        cwd=tools,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "injected BOM render failure" in completed.stderr
    _assert_files_unchanged(sentinels)


def test_default_docs_generation_filters_dnp_and_testpoints_from_cpl(
    tmp_path: Path,
    esp32tap_dir: Path,
) -> None:
    pcbnew_source = """\
from pathlib import Path


class _Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class _FootprintId:
    def GetLibItemName(self):
        return "R_0603_1608Metric"


class _Footprint:
    def __init__(self, reference, x):
        self._reference = reference
        self._x = x

    def GetReference(self):
        return self._reference

    def GetPosition(self):
        return _Point(self._x, 150.0)

    def GetValue(self):
        return self._reference

    def GetFPID(self):
        return _FootprintId()

    def GetOrientationDegrees(self):
        return -90.0 if self._reference == "R1" else 0.0


class _Board:
    def GetFootprints(self):
        return [
            _Footprint("R1", 101.0),
            _Footprint("R2", 102.0),
            _Footprint("C13", 103.0),
            _Footprint("TP1", 104.0),
        ]


def LoadBoard(path):
    assert Path(path).is_file()
    return _Board()


def ToMM(value):
    return value
"""
    sandbox, tools = _docs_sandbox(
        tmp_path,
        esp32tap_dir,
        pcbnew_source,
    )
    (sandbox / "kicad" / "Esp32Tap.kicad_pcb").write_text(
        "(kicad_pcb)",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, "gen_docs.py"],
        cwd=tools,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    with (sandbox / "bom" / "CPL-positions.csv").open(
        newline="",
        encoding="utf-8",
    ) as cpl_file:
        rows = list(csv.DictReader(cpl_file))
    assert [row["Designator"] for row in rows] == ["R1", "R2"]
    assert [row["Rotation"] for row in rows] == ["270", "0"]


@pytest.fixture
def generated_tree(
    tmp_path: Path,
    esp32tap_dir: Path,
) -> Path:
    sandbox = tmp_path / "Esp32Tap"
    tools = sandbox / "tools"
    (sandbox / "kicad").mkdir(parents=True)
    (sandbox / "bom").mkdir()
    tools.mkdir()
    for filename in ("design.py", "gen_sch.py", "gen_docs.py"):
        shutil.copy2(esp32tap_dir / "tools" / filename, tools / filename)

    for command in (
        [sys.executable, "gen_sch.py"],
        [sys.executable, "gen_docs.py", "--skip-cpl"],
    ):
        completed = subprocess.run(
            command,
            cwd=tools,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, (
            f"{' '.join(command)} failed\n" f"stdout:\n{completed.stdout}\n" f"stderr:\n{completed.stderr}"
        )
    return sandbox


def test_temp_generation_byte_matches_committed_artifacts(
    generated_tree: Path,
    esp32tap_dir: Path,
) -> None:
    for generated_relative, committed_relative in DETERMINISTIC_ARTIFACTS:
        generated = generated_tree / generated_relative
        committed = esp32tap_dir / committed_relative
        assert generated.read_bytes() == committed.read_bytes(), f"committed artifact is stale: {committed_relative}"


def _assert_erc_semantics(report: str) -> None:
    summary = re.search(
        r"\*\* ERC messages:\s+(\d+)\s+Errors\s+" r"(\d+)\s+Warnings\s+(\d+)",
        report,
    )
    assert summary is not None, report
    assert tuple(int(value) for value in summary.groups()) == (0, 0, 0)
    assert "; excluded" not in report.lower()
    ignored = {line.strip()[2:] for line in report.splitlines() if line.strip().startswith("- ")}
    assert ignored == ALLOWED_ERC_IGNORED_CHECKS


def test_temp_generation_is_accepted_by_kicad(
    generated_tree: Path,
    tmp_path: Path,
    design: SimpleNamespace,
) -> None:
    kicad = generated_tree / "kicad"
    upgraded_symbols = tmp_path / "validated.kicad_sym"
    symbol_check = subprocess.run(
        [
            "kicad-cli",
            "sym",
            "upgrade",
            "--force",
            "-o",
            str(upgraded_symbols),
            str(kicad / "esp32tap.kicad_sym"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert symbol_check.returncode == 0, symbol_check.stderr
    assert upgraded_symbols.is_file()

    xml_netlist = tmp_path / "Esp32Tap.xml"
    netlist_check = subprocess.run(
        [
            "kicad-cli",
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadxml",
            "-o",
            str(xml_netlist),
            str(kicad / "Esp32Tap.kicad_sch"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert netlist_check.returncode == 0, netlist_check.stderr
    exported = ET.parse(xml_netlist).getroot()
    components = exported.find("components")
    assert components is not None
    assert {component.attrib["ref"] for component in components} == set(design.COMPONENTS)
    nets = exported.find("nets")
    assert nets is not None
    actual_nets = {
        net.attrib["name"]: {(node.attrib["ref"], node.attrib["pin"]) for node in net.findall("node")} for net in nets
    }
    for net, pins in design.NETS.items():
        assert actual_nets[net] == set(pins)

    erc_report = tmp_path / "erc.rpt"
    erc_check = subprocess.run(
        [
            "kicad-cli",
            "sch",
            "erc",
            "--severity-all",
            "--exit-code-violations",
            "-o",
            str(erc_report),
            str(kicad / "Esp32Tap.kicad_sch"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert erc_check.returncode == 0, erc_check.stderr
    _assert_erc_semantics(erc_report.read_text(encoding="utf-8"))

    kicad_bom = tmp_path / "kicad-bom.csv"
    bom_check = subprocess.run(
        [
            "kicad-cli",
            "sch",
            "export",
            "bom",
            "--fields",
            "Reference",
            "--labels",
            "Reference",
            "--exclude-dnp",
            "-o",
            str(kicad_bom),
            str(kicad / "Esp32Tap.kicad_sch"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert bom_check.returncode == 0, bom_check.stderr
    with kicad_bom.open(newline="", encoding="utf-8") as bom_file:
        rows = list(csv.DictReader(bom_file))
    assert {row["Reference"] for row in rows} == {
        reference
        for reference, component in design.COMPONENTS.items()
        if reference not in design.DNP and component[4] != "none"
    }


def test_checked_in_erc_has_only_known_project_default_ignores(
    esp32tap_dir: Path,
) -> None:
    report = (esp32tap_dir / "kicad" / "erc.rpt").read_text(encoding="utf-8")
    _assert_erc_semantics(report)


def _expected_component_line(
    reference: str,
    component: tuple[Any, ...],
) -> str:
    value, footprint_lib, footprint, lcsc, jlc_class, _cost, desc, _pins = component
    return f"| {reference} | {value} | {footprint_lib}:{footprint} | " f"{lcsc or '-'} | {jlc_class} | {desc} |"


def _expected_net_line(
    net: str,
    pins: list[tuple[str, str]],
    design: SimpleNamespace,
) -> str:
    entries = ", ".join(f"`{reference}.{pad}` " f"({design.COMPONENTS[reference][7][pad]})" for reference, pad in pins)
    return f"* **{net}** — {entries}"


def test_checked_in_netlist_exactly_matches_design(
    esp32tap_dir: Path,
    design: SimpleNamespace,
) -> None:
    netlist = (esp32tap_dir / "NETLIST.md").read_text(encoding="utf-8")
    assert "`tools/design.py` is the source of truth" in netlist
    for reference, component in design.COMPONENTS.items():
        assert _expected_component_line(reference, component) in netlist
    for net, pins in design.NETS.items():
        assert _expected_net_line(net, pins, design) in netlist
    expected_nc = ", ".join(f"`{reference}.{pad}`" for reference, pad in design.NC)
    assert f"\n{expected_nc}\n" in netlist


def test_checked_in_netlist_describes_rev_b_topology(
    esp32tap_dir: Path,
) -> None:
    netlist = (esp32tap_dir / "NETLIST.md").read_text(encoding="utf-8")
    required_claims = (
        "treadmill-only local power",
        "USB is data-only",
        "U4",
        "TREAD_OK",
        "U5",
        "Q1",
        "one transfer pole",
        "one dry feedback pole",
        "U7",
        "TX isolation",
        "10k UART taps",
        "C13/C14 are DNP",
        "IO4=K1_NC_FB",
        "IO5=K1_NO_FB",
        "IO6=TREAD_OK",
        "IO7=VBUS_PRESENT_N",
        "IO15=TX_ENABLE",
        "IO16=UART2 RX",
        "IO17=UART1 TX",
        "IO18=UART1 RX",
        "IO21=RELAY_CMD",
        "IO38=status LED",
        "schematic-only power flags",
        "no footprint and are excluded from BOM/CPL",
    )
    for claim in required_claims:
        assert claim in netlist
    forbidden_rev_a_claims = (
        "VIN ORing",
        "both poles bridge",
        "parallel relay",
        "RX taps are 4.7k",
    )
    for claim in forbidden_rev_a_claims:
        assert claim not in netlist


def _expected_bom_rows(design: SimpleNamespace) -> list[dict[str, str]]:
    groups: dict[tuple[Any, ...], list[str]] = {}
    for reference, component in design.COMPONENTS.items():
        value, footprint_lib, footprint, lcsc, jlc_class, cost, desc, _pins = component
        if reference in design.DNP or jlc_class == "none":
            continue
        key = (
            value,
            footprint_lib,
            footprint,
            lcsc,
            jlc_class,
            cost,
            desc,
        )
        groups.setdefault(key, []).append(reference)

    expected = []
    for key, references in groups.items():
        (
            value,
            _footprint_lib,
            footprint,
            lcsc,
            jlc_class,
            cost,
            desc,
        ) = key
        quantity = len(references)
        expected.append(
            {
                "Comment": value,
                "Designator": ",".join(references),
                "Footprint": footprint,
                "LCSC Part #": lcsc,
                "JLC class": jlc_class,
                "Qty": str(quantity),
                "Unit cost (USD)": f"{cost:.3f}",
                "Ext cost (USD)": f"{quantity * cost:.3f}",
                "Description": desc,
            }
        )
    return expected


def test_checked_in_bom_exactly_matches_populated_rev_c_components(
    esp32tap_dir: Path,
    design: SimpleNamespace,
) -> None:
    with (esp32tap_dir / "bom" / "BOM.csv").open(
        newline="",
        encoding="utf-8",
    ) as bom_file:
        actual = list(csv.DictReader(bom_file))

    assert actual == _expected_bom_rows(design)
    references = {reference for row in actual for reference in row["Designator"].split(",")}
    assert references == {
        reference
        for reference, component in design.COMPONENTS.items()
        if reference not in design.DNP and component[4] != "none"
    }
    assert "D2" not in references
    assert references.isdisjoint(design.DNP)
    assert not any(reference.startswith(("TP", "MH")) for reference in references)
    by_reference = {reference: row for row in actual for reference in row["Designator"].split(",")}
    assert {
        ref: (
            by_reference[ref]["Comment"],
            by_reference[ref]["Footprint"],
            by_reference[ref]["LCSC Part #"],
            by_reference[ref]["JLC class"],
        )
        for ref in ("J1", "J2", "SW1", "SW2", "U1")
    } == {
        "J1": (
            "441440003",
            "RJ45-SMD_441440003",
            "C585890",
            "Extended",
        ),
        "J2": (
            "441440003",
            "RJ45-SMD_441440003",
            "C585890",
            "Extended",
        ),
        "SW1": (
            "SKRPACE010",
            "SW_SPST_SKRPACE010",
            "C139797",
            "Extended",
        ),
        "SW2": (
            "SKRPACE010",
            "SW_SPST_SKRPACE010",
            "C139797",
            "Extended",
        ),
        "U1": (
            "ESP32-S3-WROOM-1-N8",
            "ESP32-S3-WROOM-1",
            "C2913198",
            "Extended",
        ),
    }


def test_checked_in_bom_uses_repository_line_endings(
    esp32tap_dir: Path,
) -> None:
    bom = (esp32tap_dir / "bom" / "BOM.csv").read_bytes()
    assert b"\r\n" not in bom
