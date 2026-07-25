#!/usr/bin/env python3
"""Regenerate Esp32Tap artifacts in isolation and compare every declared byte."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SYSTEM_PYTHON = Path("/usr/bin/python3")
sys.dont_write_bytecode = True
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from export_fab import EXPECTED_FAB_FILES, FabExportError, isolated_kicad_project  # noqa: E402

SOURCE_PATHS = (
    Path("tools/design.py"),
    Path("tools/gen_footprints.py"),
    Path("tools/footprint_sources/RJ45-SMD_441440003.kicad_mod"),
    Path("tools/footprint_sources/ESP32-S3-WROOM-1.kicad_mod"),
    Path("tools/gen_sch.py"),
    Path("tools/gen_pcb.py"),
    Path("tools/power_intent.py"),
    Path("tools/gen_docs.py"),
    Path("tools/export_fab.py"),
    Path("tools/inspect_kicad.py"),
    Path("kicad/Esp32Tap.kicad_pro"),
    Path("kicad/Esp32Tap.kicad_dru"),
)
GENERATED_PATHS = (
    Path("NETLIST.md"),
    Path("bom/BOM.csv"),
    Path("bom/CPL-positions.csv"),
    Path("kicad/Esp32Tap.kicad_sch"),
    Path("kicad/Esp32Tap.kicad_pcb"),
    Path("kicad/esp32tap.kicad_sym"),
    Path("kicad/sym-lib-table"),
    Path("kicad/fp-lib-table"),
    Path("kicad/RJ45_SMD.pretty/RJ45-SMD_441440003.kicad_mod"),
    Path("kicad/Button_Switch_SMD.pretty/SW_SPST_SKRPACE010.kicad_mod"),
    Path("kicad/RF_Module.pretty/ESP32-S3-WROOM-1.kicad_mod"),
    Path("kicad/erc.rpt"),
    Path("kicad/drc.rpt"),
    Path("kicad/Esp32Tap-gerbers.zip"),
    *(Path("kicad/gerbers") / filename for filename in sorted(EXPECTED_FAB_FILES)),
)
NORMALIZED_REPORT_DATE = "1970-01-01T00:00:00"


class ReproError(RuntimeError):
    """A generator, report, or byte-for-byte comparison failed."""


def _validate_declarations() -> None:
    sources = set(SOURCE_PATHS)
    generated = set(GENERATED_PATHS)
    if sources & generated:
        raise ReproError(f"source/generated declarations overlap: {sorted(sources & generated)}")
    for path in sources | generated:
        if path.is_absolute() or ".." in path.parts:
            raise ReproError(f"unsafe declared path: {path}")


def _run(command: list[str], *, cwd: Path, label: str) -> None:
    try:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReproError(f"{label} could not run: {error}") from error
    if completed.returncode:
        raise ReproError(
            f"{label} failed with exit code {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )


def normalize_report(kind: str, report: str) -> str:
    """Replace only the volatile timestamp line in a KiCad check report."""
    if kind == "erc":
        pattern = r"(?m)^ERC report \([^,\r\n]+,\s*Encoding UTF8\)$"
        if len(re.findall(pattern, report)) != 1:
            raise ReproError("ERC report header/timestamp must appear exactly once")
        return re.sub(
            pattern,
            f"ERC report ({NORMALIZED_REPORT_DATE}, Encoding UTF8)",
            report,
            count=1,
        )
    if kind == "drc":
        title = r"(?m)^\*\* Drc report for Esp32Tap\.kicad_pcb \*\*$"
        pattern = r"(?m)^\*\* Created on [^*\r\n]+ \*\*$"
        if len(re.findall(title, report)) != 1 or len(re.findall(pattern, report)) != 1:
            raise ReproError("DRC report title/header timestamp must each appear exactly once")
        return re.sub(
            pattern,
            f"** Created on {NORMALIZED_REPORT_DATE} **",
            report,
            count=1,
        )
    raise ReproError(f"unknown report kind: {kind}")


def validate_report(kind: str, report: str) -> None:
    """Require a parseable zero-violation summary."""
    if kind == "erc":
        summaries = re.findall(
            r"\*\* ERC messages:\s+(\d+)\s+Errors\s+" r"(\d+)\s+Warnings\s+(\d+)",
            report,
        )
        if len(summaries) != 1:
            raise ReproError("ERC summary must appear exactly once and be well formed")
        if any(int(value) for value in summaries[0]):
            raise ReproError(f"ERC violations are nonzero: {summaries[0]}")
        return
    if kind == "drc":
        patterns = {
            "DRC": r"\*\* Found\s+(\d+)\s+DRC violations\s+\*\*",
            "unconnected": r"\*\* Found\s+(\d+)\s+unconnected pads\s+\*\*",
            "footprint": r"\*\* Found\s+(\d+)\s+Footprint errors\s+\*\*",
        }
        counts: dict[str, int] = {}
        for label, pattern in patterns.items():
            matches = re.findall(pattern, report, flags=re.IGNORECASE)
            if len(matches) != 1:
                raise ReproError("DRC summary " f"{label} count must appear exactly once")
            counts[label] = int(matches[0])
        if any(counts.values()):
            raise ReproError(f"DRC violations are nonzero: {counts}")
        return
    raise ReproError(f"unknown report kind: {kind}")


def _report_command(kind: str, source: Path, output: Path) -> list[str]:
    if kind == "erc":
        return [
            "kicad-cli",
            "sch",
            "erc",
            "--severity-all",
            "--exit-code-violations",
            "--output",
            str(output),
            str(source),
        ]
    if kind == "drc":
        return [
            "kicad-cli",
            "pcb",
            "drc",
            "--all-track-errors",
            "--schematic-parity",
            "--severity-all",
            "--exit-code-violations",
            "--output",
            str(output),
            str(source),
        ]
    raise ReproError(f"unknown report kind: {kind}")


def render_report(root: Path, kind: str) -> str:
    """Run KiCad into a temporary file and return normalized checked output."""
    filename = "Esp32Tap.kicad_sch" if kind == "erc" else "Esp32Tap.kicad_pcb"
    source = root / "kicad" / filename
    try:
        with isolated_kicad_project(source) as isolated_source:
            output = isolated_source.parent / f"{kind}.rpt"
            _run(
                _report_command(kind, isolated_source, output),
                cwd=isolated_source.parent,
                label=f"KiCad {kind.upper()}",
            )
            try:
                report = output.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise ReproError(f"cannot read {kind.upper()} report: {error}") from error
    except FabExportError as error:
        raise ReproError(f"cannot isolate KiCad {kind.upper()} project: {error}") from error
    normalized = normalize_report(kind, report)
    validate_report(kind, normalized)
    return normalized


def write_report(root: Path, kind: str) -> None:
    """Atomically replace one checked-in zero-violation report."""
    report = render_report(root, kind)
    destination = root / "kicad" / f"{kind}.rpt"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{kind}.rpt.",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(report)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _copy_sources(source_root: Path, isolated_root: Path) -> None:
    _validate_declarations()
    resolved_root = source_root.resolve()
    for relative in SOURCE_PATHS:
        source = source_root / relative
        if source.is_symlink():
            raise ReproError(f"declared source cannot be a symlink: {relative}")
        if not source.is_file():
            raise ReproError(f"declared source is missing: {relative}")
        resolved_source = source.resolve()
        if not resolved_source.is_relative_to(resolved_root):
            raise ReproError(f"declared source resolves outside root: {relative}")
        destination = isolated_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _regenerate(isolated_root: Path) -> None:
    default_python = Path(sys.executable)
    if not SYSTEM_PYTHON.is_file():
        raise ReproError(f"system Python is missing: {SYSTEM_PYTHON}")
    commands = (
        (
            [str(default_python), "tools/gen_footprints.py"],
            "footprint generation",
        ),
        (
            [str(default_python), "tools/gen_sch.py"],
            "schematic generation",
        ),
        (
            [str(SYSTEM_PYTHON), "tools/gen_pcb.py"],
            "PCB generation",
        ),
        (
            [str(SYSTEM_PYTHON), "tools/gen_docs.py"],
            "NETLIST/BOM/CPL generation",
        ),
    )
    for command, label in commands:
        _run(command, cwd=isolated_root, label=label)
    write_report(isolated_root, "erc")
    write_report(isolated_root, "drc")
    _run(
        [str(default_python), "tools/export_fab.py"],
        cwd=isolated_root,
        label="fabrication export",
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare_generated(expected_root: Path, actual_root: Path) -> None:
    """Compare every declared generated path byte-for-byte."""
    problems: list[str] = []
    allowed_files = set(SOURCE_PATHS) | set(GENERATED_PATHS)
    allowed_directories = {parent for relative in allowed_files for parent in relative.parents if parent != Path(".")}
    entries = list(actual_root.rglob("*"))
    symlinks = sorted(path.relative_to(actual_root) for path in entries if path.is_symlink())
    if symlinks:
        problems.append("isolated regeneration emitted symlinks: " + ", ".join(str(path) for path in symlinks))
    undeclared = sorted(
        path.relative_to(actual_root)
        for path in entries
        if path.relative_to(actual_root) not in allowed_files | allowed_directories
    )
    if undeclared:
        problems.append(
            "isolated regeneration emitted undeclared entries: " + ", ".join(str(path) for path in undeclared)
        )
    wrong_types = sorted(
        path.relative_to(actual_root)
        for path in entries
        if not path.is_symlink()
        and (
            (path.relative_to(actual_root) in allowed_files and not path.is_file())
            or (path.relative_to(actual_root) in allowed_directories and not path.is_dir())
        )
    )
    if wrong_types:
        problems.append(
            "isolated regeneration emitted wrong entry types: " + ", ".join(str(path) for path in wrong_types)
        )
    for relative in GENERATED_PATHS:
        expected = expected_root / relative
        actual = actual_root / relative
        if expected.is_symlink() or not expected.is_file():
            problems.append(f"{relative}: checked-in file missing")
            continue
        if actual.is_symlink():
            problems.append(f"{relative}: regenerated file is a symlink")
            continue
        if not actual.is_file():
            problems.append(f"{relative}: regenerated file missing")
            continue
        expected_hash = _digest(expected)
        actual_hash = _digest(actual)
        if expected_hash != actual_hash:
            problems.append(f"{relative}: differs " f"(checked-in={expected_hash}, regenerated={actual_hash})")
    if problems:
        raise ReproError("generated artifacts are stale or non-reproducible:\n" + "\n".join(problems))


def reproduce_and_compare(root: Path = ROOT) -> None:
    """Copy only declared sources, regenerate in /tmp, and compare."""
    root = root.resolve()
    with tempfile.TemporaryDirectory(prefix="esp32tap-repro-") as temporary:
        isolated_root = Path(temporary) / "Esp32Tap"
        isolated_root.mkdir()
        _copy_sources(root, isolated_root)
        _regenerate(isolated_root)
        compare_generated(root, isolated_root)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--write-report",
        choices=("erc", "drc"),
        help="atomically regenerate one normalized KiCad report",
    )
    return parser


def main(arguments: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    try:
        root = args.root.resolve()
        if args.write_report:
            write_report(root, args.write_report)
            print(f"WROTE kicad/{args.write_report}.rpt (0 violations)")
        else:
            reproduce_and_compare(root)
            print(f"PASS: {len(GENERATED_PATHS)} generated artifacts " "are byte-reproducible")
        return 0
    except ReproError as error:
        print(f"repro_check: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
