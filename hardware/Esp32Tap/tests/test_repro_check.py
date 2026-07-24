from __future__ import annotations

import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="module")
def repro_tool(esp32tap_dir: Path) -> SimpleNamespace:
    path = esp32tap_dir / "tools" / "repro_check.py"
    assert path.is_file(), "tools/repro_check.py is required"
    return SimpleNamespace(
        **runpy.run_path(str(path), run_name="esp32tap_repro_test")
    )


def test_reproducer_declarations_are_relative_disjoint_and_complete(
    repro_tool: SimpleNamespace,
) -> None:
    sources = set(repro_tool.SOURCE_PATHS)
    generated = set(repro_tool.GENERATED_PATHS)

    assert sources
    assert generated
    assert not sources & generated
    assert all(not path.is_absolute() and ".." not in path.parts for path in sources)
    assert all(
        not path.is_absolute() and ".." not in path.parts
        for path in generated
    )
    assert {
        Path("tools/design.py"),
        Path("tools/gen_sch.py"),
        Path("tools/gen_pcb.py"),
        Path("tools/gen_docs.py"),
        Path("tools/export_fab.py"),
        Path("kicad/Esp32Tap.kicad_pro"),
        Path("kicad/Esp32Tap.kicad_dru"),
    } <= sources
    assert {
        Path("NETLIST.md"),
        Path("bom/BOM.csv"),
        Path("bom/CPL-positions.csv"),
        Path("kicad/Esp32Tap.kicad_sch"),
        Path("kicad/Esp32Tap.kicad_pcb"),
        Path("kicad/erc.rpt"),
        Path("kicad/drc.rpt"),
        Path("kicad/Esp32Tap-gerbers.zip"),
        Path("kicad/gerbers/Esp32Tap-In1_Cu.g1"),
        Path("kicad/gerbers/Esp32Tap-In2_Cu.g2"),
    } <= generated


def test_source_copy_rejects_declared_symlinks_outside_root(
    repro_tool: SimpleNamespace,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    isolated = tmp_path / "isolated"
    outside = tmp_path / "outside-design.py"
    source.mkdir()
    isolated.mkdir()
    outside.write_text("EXTERNAL = True\n", encoding="utf-8")
    for relative in repro_tool.SOURCE_PATHS:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"declared source {relative}\n", encoding="utf-8")
    design = source / "tools" / "design.py"
    design.unlink()
    design.symlink_to(outside)

    with pytest.raises(repro_tool.ReproError, match="symlink"):
        repro_tool._copy_sources(source, isolated)


@pytest.mark.parametrize(
    ("kind", "raw", "expected"),
    [
        (
            "erc",
            "ERC report (2026-07-24T00:18:39, Encoding UTF8)\n"
            "** ERC messages: 0  Errors 0  Warnings 0\n",
            "ERC report (1970-01-01T00:00:00, Encoding UTF8)\n"
            "** ERC messages: 0  Errors 0  Warnings 0\n",
        ),
        (
            "drc",
            "** Drc report for Esp32Tap.kicad_pcb **\n"
            "** Created on 2026-07-23T12:08:56 **\n"
            "** Found 0 DRC violations **\n"
            "** Found 0 unconnected pads **\n",
            "** Drc report for Esp32Tap.kicad_pcb **\n"
            "** Created on 1970-01-01T00:00:00 **\n"
            "** Found 0 DRC violations **\n"
            "** Found 0 unconnected pads **\n",
        ),
    ],
)
def test_report_timestamp_normalization_is_narrow_and_deterministic(
    repro_tool: SimpleNamespace,
    kind: str,
    raw: str,
    expected: str,
) -> None:
    assert repro_tool.normalize_report(kind, raw) == expected
    assert repro_tool.normalize_report(kind, expected) == expected


@pytest.mark.parametrize(
    ("kind", "report"),
    [
        (
            "erc",
            "** ERC messages: 0  Errors 0  Warnings 0\n",
        ),
        (
            "erc",
            "ERC report (2026-07-24T00:18:39, Encoding UTF8)\n"
            "ERC report (2026-07-24T00:18:40, Encoding UTF8)\n"
            "** ERC messages: 0  Errors 0  Warnings 0\n",
        ),
        (
            "drc",
            "** Drc report for Esp32Tap.kicad_pcb **\n"
            "** Found 0 DRC violations **\n"
            "** Found 0 unconnected pads **\n"
            "** Found 0 Footprint errors **\n",
        ),
        (
            "drc",
            "** Drc report for Esp32Tap.kicad_pcb **\n"
            "** Drc report for Esp32Tap.kicad_pcb **\n"
            "** Created on 2026-07-23T12:08:56 **\n"
            "** Found 0 DRC violations **\n"
            "** Found 0 unconnected pads **\n"
            "** Found 0 Footprint errors **\n",
        ),
        (
            "drc",
            "** Drc report for Esp32Tap.kicad_pcb **\n"
            "** Created on 2026-07-23T12:08:56 **\n"
            "** Created on 2026-07-23T12:08:57 **\n"
            "** Found 0 DRC violations **\n"
            "** Found 0 unconnected pads **\n"
            "** Found 0 Footprint errors **\n",
        ),
    ],
)
def test_report_normalization_requires_one_exact_header_and_timestamp(
    repro_tool: SimpleNamespace,
    kind: str,
    report: str,
) -> None:
    with pytest.raises(repro_tool.ReproError, match="header"):
        repro_tool.normalize_report(kind, report)


@pytest.mark.parametrize(
    ("kind", "report", "message"),
    [
        (
            "erc",
            "ERC report (1970-01-01T00:00:00, Encoding UTF8)\n"
            "** ERC messages: 1  Errors 0  Warnings 0\n",
            "ERC violations",
        ),
        (
            "erc",
            "not an ERC report\n",
            "ERC summary",
        ),
        (
            "erc",
            "** ERC messages: 0  Errors 0  Warnings 0\n"
            "** ERC messages: 1  Errors 0  Warnings 0\n",
            "ERC summary",
        ),
        (
            "drc",
            "** Found 0 DRC violations **\n"
            "** Found 2 unconnected pads **\n"
            "** Found 0 Footprint errors **\n",
            "DRC violations",
        ),
        (
            "drc",
            "** Found 0 DRC violations **\n"
            "** Found 0 unconnected pads **\n",
            "footprint",
        ),
        (
            "drc",
            "** Found 0 DRC violations **\n"
            "** Found 1 DRC violations **\n"
            "** Found 0 unconnected pads **\n"
            "** Found 0 Footprint errors **\n",
            "DRC summary",
        ),
        (
            "drc",
            "not a DRC report\n",
            "DRC summary",
        ),
    ],
)
def test_report_validation_fails_closed(
    repro_tool: SimpleNamespace,
    kind: str,
    report: str,
    message: str,
) -> None:
    with pytest.raises(repro_tool.ReproError, match=message):
        repro_tool.validate_report(kind, report)


@pytest.mark.parametrize("kind", ["erc", "drc"])
def test_report_render_uses_disposable_same_basename_project(
    repro_tool: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    root = tmp_path / "Esp32Tap"
    kicad_dir = root / "kicad"
    kicad_dir.mkdir(parents=True)
    suffix = "kicad_sch" if kind == "erc" else "kicad_pcb"
    source = kicad_dir / f"Esp32Tap.{suffix}"
    source.write_text(f"{kind} source\n", encoding="utf-8")
    for filename in ("Esp32Tap.kicad_pro", "Esp32Tap.kicad_dru"):
        (kicad_dir / filename).write_text(
            f"{filename} source\n",
            encoding="utf-8",
        )
    sentinel = kicad_dir / "Esp32Tap.kicad_prl"
    sentinel.write_bytes(b"user preferences must survive\n")
    seen_sources: list[Path] = []

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        label: str,
    ) -> None:
        isolated_source = Path(command[-1])
        output = Path(command[command.index("--output") + 1])
        assert isolated_source.name == source.name
        assert isolated_source.parent == cwd
        assert isolated_source != source
        assert not (cwd / "Esp32Tap.kicad_prl").exists()
        (cwd / "Esp32Tap.kicad_prl").write_bytes(b"KiCad sidecar\n")
        if kind == "erc":
            report = (
                "ERC report (2026-07-24T00:18:39, Encoding UTF8)\n"
                "** ERC messages: 0  Errors 0  Warnings 0\n"
            )
        else:
            report = (
                "** Drc report for Esp32Tap.kicad_pcb **\n"
                "** Created on 2026-07-23T12:08:56 **\n"
                "** Found 0 DRC violations **\n"
                "** Found 0 unconnected pads **\n"
                "** Found 0 Footprint errors **\n"
            )
        output.write_text(report, encoding="utf-8")
        seen_sources.append(isolated_source)

    monkeypatch.setitem(repro_tool.render_report.__globals__, "_run", fake_run)
    report = repro_tool.render_report(root, kind)

    repro_tool.validate_report(kind, report)
    assert sentinel.read_bytes() == b"user preferences must survive\n"
    assert len(seen_sources) == 1
    assert not seen_sources[0].parent.exists()


def test_reproducer_disables_bytecode_before_importing_fab_tool(
    repro_tool: SimpleNamespace,
) -> None:
    source = Path(repro_tool.__file__).read_text(encoding="utf-8")
    assert source.index("sys.dont_write_bytecode = True") < source.index(
        "from export_fab import"
    )


def test_compare_generated_reports_missing_extra_and_changed_files(
    repro_tool: SimpleNamespace,
    tmp_path: Path,
) -> None:
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    expected.mkdir()
    actual.mkdir()

    paths = tuple(repro_tool.GENERATED_PATHS)
    for path in paths:
        (expected / path).parent.mkdir(parents=True, exist_ok=True)
        (actual / path).parent.mkdir(parents=True, exist_ok=True)
        (expected / path).write_bytes(f"same:{path}\n".encode())
        (actual / path).write_bytes(f"same:{path}\n".encode())

    repro_tool.compare_generated(expected, actual)

    changed = paths[0]
    (actual / changed).write_text("changed\n", encoding="utf-8")
    with pytest.raises(repro_tool.ReproError, match=str(changed)):
        repro_tool.compare_generated(expected, actual)

    (actual / changed).write_bytes((expected / changed).read_bytes())
    missing = paths[1]
    (actual / missing).unlink()
    with pytest.raises(repro_tool.ReproError, match=str(missing)):
        repro_tool.compare_generated(expected, actual)

    (actual / missing).write_bytes((expected / missing).read_bytes())
    extra = actual / "kicad" / "NEW-UNDECLARED-FAB.gbr"
    extra.write_text("unexpected output\n", encoding="utf-8")
    with pytest.raises(repro_tool.ReproError, match="undeclared"):
        repro_tool.compare_generated(expected, actual)


@pytest.mark.parametrize("broken", [False, True])
def test_compare_generated_rejects_generated_symlinks(
    repro_tool: SimpleNamespace,
    tmp_path: Path,
    broken: bool,
) -> None:
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    expected.mkdir()
    actual.mkdir()
    paths = tuple(repro_tool.GENERATED_PATHS)
    for path in paths:
        (expected / path).parent.mkdir(parents=True, exist_ok=True)
        (actual / path).parent.mkdir(parents=True, exist_ok=True)
        (expected / path).write_bytes(f"same:{path}\n".encode())
        (actual / path).write_bytes(f"same:{path}\n".encode())

    target = actual / paths[0]
    target.unlink()
    if broken:
        target.symlink_to(actual / "missing-output")
    else:
        external = tmp_path / "external-output"
        external.write_bytes((expected / paths[0]).read_bytes())
        target.symlink_to(external)

    with pytest.raises(repro_tool.ReproError, match="symlink"):
        repro_tool.compare_generated(expected, actual)
