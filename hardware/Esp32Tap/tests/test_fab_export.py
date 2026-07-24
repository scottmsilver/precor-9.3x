from __future__ import annotations

import json
import os
import re
import runpy
import time
import zipfile
import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


EXPECTED_FAB_FILES = {
    "Esp32Tap-F_Cu.gtl",
    "Esp32Tap-In1_Cu.g1",
    "Esp32Tap-In2_Cu.g2",
    "Esp32Tap-B_Cu.gbl",
    "Esp32Tap-F_Mask.gts",
    "Esp32Tap-B_Mask.gbs",
    "Esp32Tap-F_Paste.gtp",
    "Esp32Tap-B_Paste.gbp",
    "Esp32Tap-F_Silkscreen.gto",
    "Esp32Tap-B_Silkscreen.gbo",
    "Esp32Tap-Edge_Cuts.gm1",
    "Esp32Tap-job.gbrjob",
    "Esp32Tap.drl",
}
GERBER_FUNCTIONS = {
    "Esp32Tap-F_Cu.gtl": "Copper,L1,Top",
    "Esp32Tap-In1_Cu.g1": "Copper,L2,Inr",
    "Esp32Tap-In2_Cu.g2": "Copper,L3,Inr",
    "Esp32Tap-B_Cu.gbl": "Copper,L4,Bot",
    "Esp32Tap-F_Mask.gts": "Soldermask,Top",
    "Esp32Tap-B_Mask.gbs": "Soldermask,Bot",
    "Esp32Tap-F_Paste.gtp": "Paste,Top",
    "Esp32Tap-B_Paste.gbp": "Paste,Bot",
    "Esp32Tap-F_Silkscreen.gto": "Legend,Top",
    "Esp32Tap-B_Silkscreen.gbo": "Legend,Bot",
    "Esp32Tap-Edge_Cuts.gm1": "Profile,NP",
}
GERBER_POLARITIES = {
    filename: (
        None
        if filename == "Esp32Tap-Edge_Cuts.gm1"
        else (
            "Negative"
            if filename in {"Esp32Tap-F_Mask.gts", "Esp32Tap-B_Mask.gbs"}
            else "Positive"
        )
    )
    for filename in GERBER_FUNCTIONS
}
JOB_FUNCTIONS = {
    "Esp32Tap-F_Cu.gtl": "Copper,L1,Top",
    "Esp32Tap-In1_Cu.g1": "Copper,L2,Inr",
    "Esp32Tap-In2_Cu.g2": "Copper,L3,Inr",
    "Esp32Tap-B_Cu.gbl": "Copper,L4,Bot",
    "Esp32Tap-F_Mask.gts": "SolderMask,Top",
    "Esp32Tap-B_Mask.gbs": "SolderMask,Bot",
    "Esp32Tap-F_Paste.gtp": "SolderPaste,Top",
    "Esp32Tap-B_Paste.gbp": "SolderPaste,Bot",
    "Esp32Tap-F_Silkscreen.gto": "Legend,Top",
    "Esp32Tap-B_Silkscreen.gbo": "Legend,Bot",
    "Esp32Tap-Edge_Cuts.gm1": "Profile",
}
JOB_POLARITIES = {
    filename: (
        "Negative"
        if filename in {"Esp32Tap-F_Mask.gts", "Esp32Tap-B_Mask.gbs"}
        else "Positive"
    )
    for filename in JOB_FUNCTIONS
}
GENERAL_SPECS = {
    "Size": {"X": 100.1, "Y": 55.1},
    "LayerNumber": 4,
    "BoardThickness": 1.59,
    "Finish": "ENIG",
    "ImpedanceControlled": True,
}
DESIGN_COMPONENT_REFERENCES = tuple(
    sorted(
        runpy.run_path(
            str(Path(__file__).parents[1] / "tools" / "design.py"),
            run_name="esp32tap_fab_design_fixture",
        )["COMPONENTS"]
    )
)


@pytest.fixture(scope="module")
def fab_tool(esp32tap_dir: Path) -> SimpleNamespace:
    path = esp32tap_dir / "tools" / "export_fab.py"
    assert path.is_file(), "tools/export_fab.py is required"
    return SimpleNamespace(
        **runpy.run_path(str(path), run_name="esp32tap_fab_test")
    )


def _write_valid_stage(directory: Path) -> None:
    directory.mkdir(parents=True)
    for filename, function in GERBER_FUNCTIONS.items():
        polarity = GERBER_POLARITIES[filename]
        if filename in {
            "Esp32Tap-B_Paste.gbp",
            "Esp32Tap-B_Silkscreen.gbo",
        }:
            artwork = []
        elif filename == "Esp32Tap-Edge_Cuts.gm1":
            artwork = [
                "%ADD10C,0.100000*%",
                "D10*",
                "X100000000Y-100000000D02*",
                "X200000000Y-100000000D01*",
                "X100000000Y-155000000D02*",
                "X100000000Y-100000000D01*",
                "X200000000Y-100000000D02*",
                "X200000000Y-155000000D01*",
                "X200000000Y-155000000D02*",
                "X100000000Y-155000000D01*",
            ]
        elif filename == "Esp32Tap-F_Silkscreen.gto":
            artwork = [
                "%ADD10C,0.200000*%",
                "%ADD11C,0.160000*%",
                "D10*",
                "X100000000Y-100000000D02*",
                "X101000000Y-100000000D01*",
            ]
            for index, reference in enumerate(DESIGN_COMPONENT_REFERENCES):
                artwork.extend(
                    [
                        f"%TO.C,{reference}*%",
                        *(["D11*"] if index == 0 else []),
                        f"X{110000000 + index}Y-110000000D02*",
                        f"X{111000000 + index}Y-110000000D01*",
                    ]
                )
            artwork.append("%TD*%")
        else:
            artwork = [
                "%ADD10C,0.100000*%",
                "D10*",
                "X100000000Y-100000000D03*",
            ]
        (directory / filename).write_text(
            "\n".join(
                [
                    "%TF.GenerationSoftware,KiCad,Pcbnew,10.0.1*%",
                    "%TF.CreationDate,2026-07-24T00:00:00-07:00*%",
                    f"%TF.FileFunction,{function}*%",
                    *(
                        [f"%TF.FilePolarity,{polarity}*%"]
                        if polarity is not None
                        else []
                    ),
                    "%FSLAX46Y46*%",
                    (
                        "G04 Created by KiCad (PCBNEW 10.0.1) "
                        "date 2026-07-24 00:00:00*"
                    ),
                    "%MOMM*%",
                    "%LPD*%",
                    *artwork,
                    "M02*",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    job_files = [
        {
            "Path": filename,
            "FileFunction": function,
            "FilePolarity": JOB_POLARITIES[filename],
        }
        for filename, function in JOB_FUNCTIONS.items()
    ]
    (directory / "Esp32Tap-job.gbrjob").write_text(
        json.dumps(
            {
                "Header": {
                    "GenerationSoftware": {
                        "Vendor": "KiCad",
                        "Application": "Pcbnew",
                        "Version": "10.0.1",
                    },
                    "CreationDate": "2026-07-24T00:00:00-07:00",
                },
                "GeneralSpecs": GENERAL_SPECS,
                "FilesAttributes": job_files,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (directory / "Esp32Tap.drl").write_text(
        "\n".join(
            [
                "M48",
                "; DRILL file KiCad 10.0.1 date 2026-07-24T00:00:00",
                "; FORMAT={-:-/ absolute / metric / decimal}",
                "; #@! TF.CreationDate,2026-07-24T00:00:00-07:00",
                "; #@! TF.GenerationSoftware,Kicad,Pcbnew,10.0.1",
                "; #@! TF.FileFunction,MixedPlating,1,4",
                "FMAT,2",
                "METRIC",
                "; #@! TA.AperFunction,Plated,PTH,ComponentDrill",
                "T1C0.600",
                "; #@! TA.AperFunction,NonPlated,NPTH,ComponentDrill",
                "T2C2.700",
                "%",
                "G90",
                "T1",
                "X110.0Y-110.0",
                "T2",
                "X120.0Y-120.0",
                "M30",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _archive_payloads(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {
            info.filename: archive.read(info)
            for info in archive.infolist()
        }


def test_exact_rev_b_four_layer_member_set_is_locked(
    fab_tool: SimpleNamespace,
) -> None:
    assert fab_tool.EXPECTED_FAB_FILES == EXPECTED_FAB_FILES
    assert fab_tool.GERBER_FUNCTIONS == GERBER_FUNCTIONS
    assert fab_tool.GERBER_POLARITIES == GERBER_POLARITIES
    assert fab_tool.JOB_FUNCTIONS == JOB_FUNCTIONS
    assert fab_tool.JOB_POLARITIES == JOB_POLARITIES
    assert fab_tool.REQUIRED_GENERAL_SPECS == GENERAL_SPECS


def test_normalization_removes_timestamps_and_component_silkscreen(
    fab_tool: SimpleNamespace,
    tmp_path: Path,
) -> None:
    stage = tmp_path / "stage"
    _write_valid_stage(stage)
    originals = {
        path.name: path.read_text(encoding="utf-8")
        for path in stage.iterdir()
    }

    fab_tool.normalize_stage(stage)
    normalized = {
        path.name: path.read_text(encoding="utf-8")
        for path in stage.iterdir()
    }

    assert normalized != originals
    assert all("2026-07-24T00:00:00-07:00" not in text for text in normalized.values())
    assert all(
        "2026-07-24 00:00:00" not in text
        for text in normalized.values()
    )
    assert fab_tool.NORMALIZED_ISO_DATE in normalized["Esp32Tap-F_Cu.gtl"]
    assert fab_tool.NORMALIZED_ISO_DATE in normalized["Esp32Tap.drl"]
    job = json.loads(normalized["Esp32Tap-job.gbrjob"])
    assert job["Header"]["CreationDate"] == fab_tool.NORMALIZED_ISO_DATE
    assert {
        item["Path"] for item in job["FilesAttributes"]
    } == set(GERBER_FUNCTIONS)
    assert (
        "DRILL file KiCad 10.0.1 date 1970-01-01T00:00:00"
        in normalized["Esp32Tap.drl"]
    )
    front_silkscreen = normalized["Esp32Tap-F_Silkscreen.gto"]
    assert "%TO.C," not in front_silkscreen
    assert "%TD*%" not in front_silkscreen
    assert "%ADD11C,0.160000*%" not in front_silkscreen
    assert "%ADD10C,0.200000*%" in front_silkscreen
    assert "X100000000Y-100000000D02*" in front_silkscreen
    assert "X101000000Y-100000000D01*" in front_silkscreen
    assert "X110000000Y-110000000D02*" not in front_silkscreen
    fab_tool.validate_stage(stage, require_normalized=True)


def test_different_export_times_normalize_to_identical_stage_bytes(
    fab_tool: SimpleNamespace,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_valid_stage(first)
    _write_valid_stage(second)
    for path in second.iterdir():
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "2026-07-24T00:00:00-07:00",
            "2031-12-31T23:59:58+14:00",
        ).replace(
            "2026-07-24T00:00:00",
            "2031-12-31T23:59:58",
        ).replace(
            "2026-07-24 00:00:00",
            "2031-12-31 23:59:58",
        )
        path.write_text(text, encoding="utf-8")

    fab_tool.normalize_stage(first)
    fab_tool.normalize_stage(second)

    assert {
        path.name: path.read_bytes()
        for path in first.iterdir()
    } == {
        path.name: path.read_bytes()
        for path in second.iterdir()
    }


@pytest.mark.parametrize(
    ("layer", "mutation"),
    (
        ("front", "component-suffix"),
        ("front", "attribute-termination"),
        ("front", "extra-aperture"),
        ("back", "component-attribute"),
        ("back", "extra-aperture"),
        ("back", "artwork"),
    ),
)
def test_normalized_stage_rejects_nonminimal_legend(
    fab_tool: SimpleNamespace,
    tmp_path: Path,
    layer: str,
    mutation: str,
) -> None:
    stage = tmp_path / f"{layer}-{mutation}"
    _write_valid_stage(stage)
    fab_tool.normalize_stage(stage)
    silkscreen = stage / (
        "Esp32Tap-F_Silkscreen.gto"
        if layer == "front"
        else "Esp32Tap-B_Silkscreen.gbo"
    )
    payload = silkscreen.read_text(encoding="utf-8")
    if mutation == "component-suffix":
        payload = payload.replace(
            "M02*",
            "%TO.C,C1*%\n"
            "D10*\n"
            "X120000000Y-120000000D01*\n"
            "%TD*%\n"
            "M02*",
        )
    elif mutation == "component-attribute":
        payload = payload.replace("M02*", "%TO.C,C1*%\nM02*")
    elif mutation == "attribute-termination":
        payload = payload.replace("M02*", "%TD*%\nM02*")
    elif mutation == "extra-aperture":
        payload = payload.replace(
            "%LPD*%",
            "%LPD*%\n%ADD11C,0.160000*%",
        )
    else:
        payload = payload.replace(
            "M02*",
            "%ADD11C,0.200000*%\n"
            "D11*\n"
            "X120000000Y-120000000D03*\n"
            "M02*",
        )
    silkscreen.write_text(payload, encoding="utf-8")

    with pytest.raises(
        fab_tool.FabExportError,
        match=r"fabrication (?:front|back) legend",
    ):
        fab_tool.validate_stage(stage, require_normalized=True)


def test_raw_stage_accepts_component_legend_before_transform(
    fab_tool: SimpleNamespace,
    tmp_path: Path,
) -> None:
    stage = tmp_path / "raw"
    _write_valid_stage(stage)

    fab_tool.validate_stage(stage)


def test_normalization_fails_if_required_creation_date_is_missing(
    fab_tool: SimpleNamespace,
    tmp_path: Path,
) -> None:
    stage = tmp_path / "stage"
    _write_valid_stage(stage)
    gerber = stage / "Esp32Tap-F_Cu.gtl"
    gerber.write_text(
        gerber.read_text(encoding="utf-8").replace(
            "%TF.CreationDate,2026-07-24T00:00:00-07:00*%\n",
            "",
        ),
        encoding="utf-8",
    )

    with pytest.raises(fab_tool.FabExportError, match="CreationDate"):
        fab_tool.normalize_stage(stage)


def _component_silkscreen_payload() -> str:
    return "\n".join(
        [
            "%TF.FileFunction,Legend,Top*%",
            "%LPD*%",
            "G01*",
            "G04 APERTURE LIST*",
            "%ADD10C,0.200000*%",
            "%ADD11C,0.160000*%",
            "G04 APERTURE END LIST*",
            "D10*",
            "X100000000Y-100000000D02*",
            "X101000000Y-100000000D01*",
            "%TO.C,R1*%",
            "D11*",
            "X110000000Y-110000000D02*",
            "X111000000Y-110000000D01*",
            "%TO.C,C1*%",
            "X120000000Y-120000000D02*",
            "X121000000Y-120000000D01*",
            "%TD*%",
            "M02*",
            "",
        ]
    )


def test_component_silkscreen_strip_preserves_labels_and_prunes_aperture(
    fab_tool: SimpleNamespace,
    tmp_path: Path,
) -> None:
    silkscreen = tmp_path / "Esp32Tap-F_Silkscreen.gto"
    silkscreen.write_text(
        _component_silkscreen_payload(),
        encoding="utf-8",
    )

    fab_tool._strip_component_silkscreen(
        silkscreen,
        expected_references={"C1", "R1"},
    )

    stripped = silkscreen.read_text(encoding="utf-8")
    assert "%TO.C," not in stripped
    assert "%TD*%" not in stripped
    assert "%ADD11C,0.160000*%" not in stripped
    assert "%ADD10C,0.200000*%" in stripped
    assert "X100000000Y-100000000D02*" in stripped
    assert "X101000000Y-100000000D01*" in stripped
    assert "X110000000Y-110000000D02*" not in stripped
    assert stripped.rstrip().endswith("M02*")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing-component", "reference set"),
        ("duplicate-component", "duplicate component"),
        ("malformed-component", "malformed component"),
        ("interleaved-termination", "terminal TD"),
        ("missing-termination", "terminal TD"),
        ("geometry-after-termination", "terminal TD"),
        ("label-aperture-interleaved", "board-label aperture"),
        ("component-without-artwork", "no artwork"),
    ],
)
def test_component_silkscreen_strip_fails_closed(
    fab_tool: SimpleNamespace,
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    payload = _component_silkscreen_payload()
    if mutation == "missing-component":
        payload = payload.replace(
            "%TO.C,C1*%\n"
            "X120000000Y-120000000D02*\n"
            "X121000000Y-120000000D01*\n",
            "",
        )
    elif mutation == "duplicate-component":
        payload = payload.replace("%TO.C,C1*%", "%TO.C,R1*%")
    elif mutation == "malformed-component":
        payload = payload.replace("%TO.C,C1*%", "%TO.C*%")
    elif mutation == "interleaved-termination":
        payload = payload.replace(
            "%TO.C,C1*%",
            "%TD*%\n%TO.C,C1*%",
        )
    elif mutation == "missing-termination":
        payload = payload.replace("%TD*%\n", "")
    elif mutation == "geometry-after-termination":
        payload = payload.replace(
            "%TD*%\nM02*",
            "%TD*%\n"
            "D10*\n"
            "X130000000Y-130000000D01*\n"
            "M02*",
        )
    elif mutation == "label-aperture-interleaved":
        payload = payload.replace(
            "%TO.C,C1*%",
            "D10*\n"
            "X115000000Y-115000000D01*\n"
            "%TO.C,C1*%",
        )
    else:
        payload = payload.replace(
            "%TO.C,C1*%\n"
            "X120000000Y-120000000D02*\n"
            "X121000000Y-120000000D01*\n",
            "%TO.C,C1*%\n",
        )
    silkscreen = tmp_path / f"{mutation}.gto"
    silkscreen.write_text(payload, encoding="utf-8")

    with pytest.raises(fab_tool.FabExportError, match=message):
        fab_tool._strip_component_silkscreen(
            silkscreen,
            expected_references={"C1", "R1"},
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing-inner", "member set"),
        ("extra-rev-a", "member set"),
        ("wrong-function", "FileFunction"),
        ("extra-function", "FileFunction"),
        ("empty-function", "FileFunction"),
        ("wrong-polarity", "FilePolarity"),
        ("missing-polarity", "FilePolarity"),
        ("duplicate-polarity", "FilePolarity"),
        ("missing-lpd", "LPD"),
        ("duplicate-lpd", "LPD"),
        ("wrong-drill-span", "drill FileFunction"),
        ("extra-drill-function", "drill FileFunction"),
        ("gerber-trailing-garbage", "end marker"),
        ("gerber-terminal-camouflage", "end marker"),
        ("drill-trailing-garbage", "complete Excellon"),
        ("drill-terminal-camouflage", "complete Excellon"),
        ("expected-member-symlink", "member set"),
        ("broken-symlink", "member set"),
        ("job-omits-inner", "Gerber job"),
        ("job-duplicate-entry", "Gerber job"),
        ("job-wrong-polarity", "Gerber job"),
        ("job-extra-attribute", "Gerber job"),
        ("job-duplicate-key", "duplicate JSON key"),
        ("job-nonfinite-json", "non-standard JSON constant"),
        ("job-omits-layer-count", "GeneralSpecs"),
        ("job-wrong-size", "GeneralSpecs"),
        ("job-wrong-thickness", "GeneralSpecs"),
        ("job-wrong-finish", "GeneralSpecs"),
        ("job-wrong-impedance", "GeneralSpecs"),
        ("geometry-free-copper", "artwork"),
        ("bare-d01-is-not-artwork", "artwork"),
        ("open-edge-profile", "profile"),
        ("extra-profile-flash", "profile"),
        ("extra-profile-aperture", "profile"),
        ("profile-region", "profile"),
        ("combined-profile-line", "profile"),
        ("combined-profile-arc", "profile"),
        ("profile-step-repeat", "transform"),
        ("profile-transform", "transform"),
        ("legacy-image-polarity", "transform"),
        ("drill-no-tools", "drill"),
        ("drill-no-plated-hit", "Plated"),
        ("drill-no-npth-hit", "NonPlated"),
        ("drill-incremental", "absolute metric decimal"),
        ("drill-inch", "absolute metric decimal"),
        ("drill-inch-suffixed", "absolute metric decimal"),
        ("drill-ici", "absolute metric decimal"),
    ],
)
def test_stage_validation_fails_closed(
    fab_tool: SimpleNamespace,
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    stage = tmp_path / mutation
    _write_valid_stage(stage)
    if mutation == "missing-inner":
        (stage / "Esp32Tap-In2_Cu.g2").unlink()
    elif mutation == "extra-rev-a":
        (stage / "Esp32Tap-old-B_Cu.gbl").write_text(
            "stale\n",
            encoding="utf-8",
        )
    elif mutation == "wrong-function":
        inner = stage / "Esp32Tap-In1_Cu.g1"
        inner.write_text(
            inner.read_text(encoding="utf-8").replace(
                "Copper,L2,Inr",
                "Copper,L1,Top",
            ),
            encoding="utf-8",
        )
    elif mutation == "extra-function":
        inner = stage / "Esp32Tap-In1_Cu.g1"
        inner.write_text(
            inner.read_text(encoding="utf-8").replace(
                "%TF.FileFunction,Copper,L2,Inr*%",
                "%TF.FileFunction,Copper,L1,Top*%\n"
                "%TF.FileFunction,Copper,L2,Inr*%",
            ),
            encoding="utf-8",
        )
    elif mutation == "empty-function":
        inner = stage / "Esp32Tap-In1_Cu.g1"
        inner.write_text(
            inner.read_text(encoding="utf-8").replace(
                "%TF.FileFunction,Copper,L2,Inr*%",
                "%TF.FileFunction,*%\n"
                "%TF.FileFunction,Copper,L2,Inr*%",
            ),
            encoding="utf-8",
        )
    elif mutation == "wrong-polarity":
        mask = stage / "Esp32Tap-F_Mask.gts"
        mask.write_text(
            mask.read_text(encoding="utf-8").replace(
                "%TF.FilePolarity,Negative*%",
                "%TF.FilePolarity,Positive*%",
            ),
            encoding="utf-8",
        )
    elif mutation == "missing-polarity":
        copper = stage / "Esp32Tap-F_Cu.gtl"
        copper.write_text(
            copper.read_text(encoding="utf-8").replace(
                "%TF.FilePolarity,Positive*%\n",
                "",
            ),
            encoding="utf-8",
        )
    elif mutation == "duplicate-polarity":
        copper = stage / "Esp32Tap-F_Cu.gtl"
        copper.write_text(
            copper.read_text(encoding="utf-8").replace(
                "%TF.FilePolarity,Positive*%",
                "%TF.FilePolarity,Positive*%\n"
                "%TF.FilePolarity,Positive*%",
            ),
            encoding="utf-8",
        )
    elif mutation == "missing-lpd":
        copper = stage / "Esp32Tap-F_Cu.gtl"
        copper.write_text(
            copper.read_text(encoding="utf-8").replace("%LPD*%\n", ""),
            encoding="utf-8",
        )
    elif mutation == "duplicate-lpd":
        copper = stage / "Esp32Tap-F_Cu.gtl"
        copper.write_text(
            copper.read_text(encoding="utf-8").replace(
                "%LPD*%",
                "%LPD*%\n%LPD*%",
            ),
            encoding="utf-8",
        )
    elif mutation == "wrong-drill-span":
        drill = stage / "Esp32Tap.drl"
        drill.write_text(
            drill.read_text(encoding="utf-8").replace(
                "MixedPlating,1,4",
                "MixedPlating,1,2",
            ),
            encoding="utf-8",
        )
    elif mutation == "extra-drill-function":
        drill = stage / "Esp32Tap.drl"
        drill.write_text(
            drill.read_text(encoding="utf-8").replace(
                "; #@! TF.FileFunction,MixedPlating,1,4",
                "; #@! TF.FileFunction,MixedPlating,1,2\n"
                "; #@! TF.FileFunction,MixedPlating,1,4",
            ),
            encoding="utf-8",
        )
    elif mutation == "gerber-trailing-garbage":
        gerber = stage / "Esp32Tap-F_Cu.gtl"
        gerber.write_text(
            gerber.read_text(encoding="utf-8") + "G04 AFTER END*\n",
            encoding="utf-8",
        )
    elif mutation == "gerber-terminal-camouflage":
        gerber = stage / "Esp32Tap-F_Cu.gtl"
        gerber.write_text(
            gerber.read_text(encoding="utf-8") + "G04 AFTER M02*\n",
            encoding="utf-8",
        )
    elif mutation == "drill-trailing-garbage":
        drill = stage / "Esp32Tap.drl"
        drill.write_text(
            drill.read_text(encoding="utf-8") + "X000001Y000001\n",
            encoding="utf-8",
        )
    elif mutation == "drill-terminal-camouflage":
        drill = stage / "Esp32Tap.drl"
        drill.write_text(
            drill.read_text(encoding="utf-8") + "XM30\n",
            encoding="utf-8",
        )
    elif mutation == "expected-member-symlink":
        gerber = stage / "Esp32Tap-F_Cu.gtl"
        external = tmp_path / "external.gtl"
        external.write_bytes(gerber.read_bytes())
        gerber.unlink()
        gerber.symlink_to(external)
    elif mutation == "broken-symlink":
        (stage / "undeclared.gtl").symlink_to(tmp_path / "missing.gtl")
    elif mutation == "job-omits-inner":
        job_path = stage / "Esp32Tap-job.gbrjob"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job["FilesAttributes"] = [
            item
            for item in job["FilesAttributes"]
            if item["Path"] != "Esp32Tap-In2_Cu.g2"
        ]
        job_path.write_text(json.dumps(job), encoding="utf-8")
    elif mutation == "job-duplicate-entry":
        job_path = stage / "Esp32Tap-job.gbrjob"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job["FilesAttributes"].append(dict(job["FilesAttributes"][0]))
        job_path.write_text(json.dumps(job), encoding="utf-8")
    elif mutation == "job-wrong-polarity":
        job_path = stage / "Esp32Tap-job.gbrjob"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job["FilesAttributes"][0]["FilePolarity"] = "Negative"
        job_path.write_text(json.dumps(job), encoding="utf-8")
    elif mutation == "job-extra-attribute":
        job_path = stage / "Esp32Tap-job.gbrjob"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job["FilesAttributes"][0]["Unexpected"] = "ambiguous"
        job_path.write_text(json.dumps(job), encoding="utf-8")
    elif mutation == "job-duplicate-key":
        job_path = stage / "Esp32Tap-job.gbrjob"
        job_path.write_text(
            job_path.read_text(encoding="utf-8").replace(
                '"BoardThickness": 1.59',
                '"BoardThickness": 1.59, "BoardThickness": 9.99',
            ),
            encoding="utf-8",
        )
    elif mutation == "job-nonfinite-json":
        job_path = stage / "Esp32Tap-job.gbrjob"
        job_path.write_text(
            job_path.read_text(encoding="utf-8").replace(
                "{\n",
                '{"Adversarial": NaN,\n',
                1,
            ),
            encoding="utf-8",
        )
    elif mutation == "job-omits-layer-count":
        job_path = stage / "Esp32Tap-job.gbrjob"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        del job["GeneralSpecs"]["LayerNumber"]
        job_path.write_text(json.dumps(job), encoding="utf-8")
    elif mutation.startswith("job-wrong-"):
        job_path = stage / "Esp32Tap-job.gbrjob"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        field, value = {
            "job-wrong-size": ("Size", {"X": 100.1, "Y": 54.9}),
            "job-wrong-thickness": ("BoardThickness", 1.6),
            "job-wrong-finish": ("Finish", "HASL"),
            "job-wrong-impedance": ("ImpedanceControlled", False),
        }[mutation]
        job["GeneralSpecs"][field] = value
        job_path.write_text(json.dumps(job), encoding="utf-8")
    elif mutation == "geometry-free-copper":
        copper = stage / "Esp32Tap-F_Cu.gtl"
        copper.write_text(
            re.sub(
                r"(?m)^X[-0-9]+Y[-0-9]+D03\*\n",
                "",
                copper.read_text(encoding="utf-8"),
            ),
            encoding="utf-8",
        )
    elif mutation == "bare-d01-is-not-artwork":
        copper = stage / "Esp32Tap-F_Cu.gtl"
        copper.write_text(
            re.sub(
                r"(?m)^X[-0-9]+Y[-0-9]+D03\*\n",
                "D01*\n",
                copper.read_text(encoding="utf-8"),
            ),
            encoding="utf-8",
        )
    elif mutation == "open-edge-profile":
        profile = stage / "Esp32Tap-Edge_Cuts.gm1"
        profile.write_text(
            profile.read_text(encoding="utf-8").replace(
                "X100000000Y-155000000D01*\nM02*",
                "M02*",
            ),
            encoding="utf-8",
        )
    elif mutation == "extra-profile-flash":
        profile = stage / "Esp32Tap-Edge_Cuts.gm1"
        profile.write_text(
            profile.read_text(encoding="utf-8").replace(
                "M02*",
                "X150000000Y-127500000D03*\nM02*",
            ),
            encoding="utf-8",
        )
    elif mutation == "extra-profile-aperture":
        profile = stage / "Esp32Tap-Edge_Cuts.gm1"
        profile.write_text(
            profile.read_text(encoding="utf-8").replace(
                "%ADD10C,0.100000*%",
                "%ADD10C,0.100000*%\n%ADD11C,1.000000*%",
            ),
            encoding="utf-8",
        )
    elif mutation == "profile-region":
        profile = stage / "Esp32Tap-Edge_Cuts.gm1"
        profile.write_text(
            profile.read_text(encoding="utf-8").replace(
                "M02*",
                "G36*\nG37*\nM02*",
            ),
            encoding="utf-8",
        )
    elif mutation == "combined-profile-arc":
        profile = stage / "Esp32Tap-Edge_Cuts.gm1"
        profile.write_text(
            profile.read_text(encoding="utf-8").replace(
                "M02*",
                "G02X150000000Y-127500000I1000000J0D01*\nM02*",
            ),
            encoding="utf-8",
        )
    elif mutation == "combined-profile-line":
        profile = stage / "Esp32Tap-Edge_Cuts.gm1"
        profile.write_text(
            profile.read_text(encoding="utf-8").replace(
                "M02*",
                "G01X150000000Y-127500000D01*\nM02*",
            ),
            encoding="utf-8",
        )
    elif mutation == "profile-step-repeat":
        profile = stage / "Esp32Tap-Edge_Cuts.gm1"
        profile.write_text(
            profile.read_text(encoding="utf-8").replace(
                "D10*",
                "%SRX2Y1I110.000000J0.000000*%\nD10*",
            ).replace(
                "M02*",
                "%SR*%\nM02*",
            ),
            encoding="utf-8",
        )
    elif mutation == "profile-transform":
        profile = stage / "Esp32Tap-Edge_Cuts.gm1"
        profile.write_text(
            profile.read_text(encoding="utf-8").replace(
                "D10*",
                "%LS2.000000*%\nD10*",
            ),
            encoding="utf-8",
        )
    elif mutation == "legacy-image-polarity":
        copper = stage / "Esp32Tap-F_Cu.gtl"
        copper.write_text(
            copper.read_text(encoding="utf-8").replace(
                "%LPD*%",
                "%IPNEG*%\n%LPD*%",
            ),
            encoding="utf-8",
        )
    elif mutation == "drill-no-tools":
        drill = stage / "Esp32Tap.drl"
        drill.write_text(
            re.sub(
                r"(?m)^T[12]C[0-9.]+\n",
                "",
                drill.read_text(encoding="utf-8"),
            ),
            encoding="utf-8",
        )
    elif mutation == "drill-no-plated-hit":
        drill = stage / "Esp32Tap.drl"
        drill.write_text(
            drill.read_text(encoding="utf-8").replace(
                "T1\nX110.0Y-110.0\n",
                "",
            ),
            encoding="utf-8",
        )
    elif mutation == "drill-no-npth-hit":
        drill = stage / "Esp32Tap.drl"
        drill.write_text(
            drill.read_text(encoding="utf-8").replace(
                "T2\nX120.0Y-120.0\n",
                "",
            ),
            encoding="utf-8",
        )
    elif mutation == "drill-incremental":
        drill = stage / "Esp32Tap.drl"
        drill.write_text(
            drill.read_text(encoding="utf-8").replace(
                "G90",
                "G90\nG91",
            ),
            encoding="utf-8",
        )
    elif mutation == "drill-inch":
        drill = stage / "Esp32Tap.drl"
        drill.write_text(
            drill.read_text(encoding="utf-8").replace(
                "METRIC",
                "METRIC\nINCH",
            ),
            encoding="utf-8",
        )
    elif mutation == "drill-inch-suffixed":
        drill = stage / "Esp32Tap.drl"
        drill.write_text(
            drill.read_text(encoding="utf-8").replace(
                "METRIC",
                "METRIC\nINCH,LZ",
            ),
            encoding="utf-8",
        )
    else:
        drill = stage / "Esp32Tap.drl"
        drill.write_text(
            drill.read_text(encoding="utf-8").replace(
                "G90",
                "G90\nICI,ON",
            ),
            encoding="utf-8",
        )

    with pytest.raises(fab_tool.FabExportError, match=message):
        fab_tool.validate_stage(stage)


def test_archive_is_byte_reproducible_and_metadata_is_fixed(
    fab_tool: SimpleNamespace,
    tmp_path: Path,
) -> None:
    first_stage = tmp_path / "first"
    second_stage = tmp_path / "second"
    _write_valid_stage(first_stage)
    _write_valid_stage(second_stage)
    fab_tool.normalize_stage(first_stage)
    fab_tool.normalize_stage(second_stage)

    now = time.time()
    for index, path in enumerate(sorted(second_stage.iterdir())):
        os.utime(path, (now + index * 100, now + index * 100))

    first_archive = tmp_path / "first.zip"
    second_archive = tmp_path / "second.zip"
    fab_tool.write_deterministic_archive(first_stage, first_archive)
    fab_tool.write_deterministic_archive(second_stage, second_archive)

    assert first_archive.read_bytes() == second_archive.read_bytes()
    assert set(_archive_payloads(first_archive)) == EXPECTED_FAB_FILES
    with zipfile.ZipFile(first_archive) as archive:
        assert all(
            info.date_time == fab_tool.ZIP_TIMESTAMP
            for info in archive.infolist()
        )
        assert [info.filename for info in archive.infolist()] == sorted(
            EXPECTED_FAB_FILES
        )


def test_publish_validates_before_replacing_checked_in_artifacts(
    fab_tool: SimpleNamespace,
    tmp_path: Path,
) -> None:
    kicad_dir = tmp_path / "kicad"
    destination = kicad_dir / "gerbers"
    destination.mkdir(parents=True)
    sentinel = destination / "user-sentinel.txt"
    sentinel.write_text("preserve on failure\n", encoding="utf-8")
    archive = kicad_dir / "Esp32Tap-gerbers.zip"
    archive.write_bytes(b"old archive")

    invalid = tmp_path / "invalid"
    _write_valid_stage(invalid)
    (invalid / "Esp32Tap-In1_Cu.g1").unlink()
    with pytest.raises(fab_tool.FabExportError):
        fab_tool.publish_stage(invalid, destination, archive)
    assert sentinel.read_text(encoding="utf-8") == "preserve on failure\n"
    assert archive.read_bytes() == b"old archive"

    valid = tmp_path / "valid"
    _write_valid_stage(valid)
    fab_tool.normalize_stage(valid)
    fab_tool.publish_stage(valid, destination, archive)

    assert {path.name for path in destination.iterdir()} == EXPECTED_FAB_FILES
    assert not sentinel.exists()
    assert set(_archive_payloads(archive)) == EXPECTED_FAB_FILES


def test_publish_rolls_back_directory_and_archive_together(
    fab_tool: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kicad_dir = tmp_path / "kicad"
    destination = kicad_dir / "gerbers"
    destination.mkdir(parents=True)
    sentinel = destination / "old.txt"
    sentinel.write_text("old directory\n", encoding="utf-8")
    archive = kicad_dir / "Esp32Tap-gerbers.zip"
    archive.write_bytes(b"old archive")
    stage = tmp_path / "valid"
    _write_valid_stage(stage)
    fab_tool.normalize_stage(stage)

    real_replace = os.replace

    def replace_then_fail(source: Any, target: Any) -> None:
        real_replace(source, target)
        if Path(target) == archive and Path(source).name == archive.name:
            raise OSError("injected post-replace archive failure")

    monkeypatch.setattr(fab_tool.os, "replace", replace_then_fail)
    with pytest.raises(
        fab_tool.FabExportError,
        match="post-replace archive failure",
    ):
        fab_tool.publish_stage(stage, destination, archive)

    assert {path.name for path in destination.iterdir()} == {"old.txt"}
    assert sentinel.read_text(encoding="utf-8") == "old directory\n"
    assert archive.read_bytes() == b"old archive"


def test_runner_rejects_failed_kicad_commands(
    fab_tool: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Failed:
        returncode = 7
        stdout = "partial output"
        stderr = "plot failed"

    def fail(*args: Any, **kwargs: Any) -> Failed:
        return Failed()

    monkeypatch.setattr(fab_tool.subprocess, "run", fail)
    with pytest.raises(
        fab_tool.FabExportError,
        match=r"KiCad Gerber export failed.*exit code 7",
    ):
        fab_tool.run_kicad(
            ["kicad-cli", "pcb", "export", "gerbers"],
            "KiCad Gerber export",
            cwd=tmp_path,
        )


def test_netlist_export_uses_disposable_same_basename_project(
    fab_tool: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Esp32Tap"
    kicad_dir = root / "kicad"
    kicad_dir.mkdir(parents=True)
    schematic = kicad_dir / "Esp32Tap.kicad_sch"
    schematic.write_text("schematic source\n", encoding="utf-8")
    (kicad_dir / "Esp32Tap.kicad_pro").write_text(
        "project source\n",
        encoding="utf-8",
    )
    sentinel = kicad_dir / "Esp32Tap.kicad_prl"
    sentinel.write_bytes(b"user preferences must survive\n")
    seen_sources: list[Path] = []

    def fake_run(
        command: list[str],
        label: str,
        *,
        cwd: Path,
    ) -> None:
        assert label == "KiCad schematic netlist export"
        source = Path(command[-1])
        output = Path(command[command.index("--output") + 1])
        assert source.name == schematic.name
        assert source.parent == cwd
        assert source != schematic
        assert (source.parent / "Esp32Tap.kicad_pro").is_file()
        assert not (source.parent / "Esp32Tap.kicad_prl").exists()
        (source.parent / "Esp32Tap.kicad_prl").write_bytes(b"KiCad sidecar\n")
        output.write_text(
            "<export><components><comp ref=\"U1\">"
            "<value>PART</value><footprint>Lib:FP</footprint>"
            "<property name=\"LCSC\" value=\"C1\"/>"
            "<property name=\"JLC Class\" value=\"Basic\"/>"
            "</comp></components></export>",
            encoding="utf-8",
        )
        seen_sources.append(source)

    monkeypatch.setitem(
        fab_tool._load_schematic_records.__globals__,
        "run_kicad",
        fake_run,
    )
    records = fab_tool._load_schematic_records(root)

    assert records["U1"]["lcsc"] == "C1"
    assert sentinel.read_bytes() == b"user preferences must survive\n"
    assert len(seen_sources) == 1
    assert not seen_sources[0].parent.exists()


def test_fab_export_uses_disposable_same_basename_project(
    fab_tool: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kicad_dir = tmp_path / "kicad"
    kicad_dir.mkdir()
    board = kicad_dir / "Esp32Tap.kicad_pcb"
    board.write_text("board source\n", encoding="utf-8")
    (kicad_dir / "Esp32Tap.kicad_pro").write_text(
        "project source\n",
        encoding="utf-8",
    )
    (kicad_dir / "Esp32Tap.kicad_dru").write_text(
        "rules source\n",
        encoding="utf-8",
    )
    sentinel = kicad_dir / "Esp32Tap.kicad_prl"
    sentinel.write_bytes(b"user preferences must survive\n")
    stage = tmp_path / "stage"
    seen_sources: list[Path] = []

    def fake_run(
        command: list[str],
        label: str,
        *,
        cwd: Path,
    ) -> None:
        assert label in {"KiCad Gerber export", "KiCad drill export"}
        source = Path(command[-1])
        assert source.name == board.name
        assert source.parent == cwd
        assert source != board
        assert (source.parent / "Esp32Tap.kicad_pro").is_file()
        assert (source.parent / "Esp32Tap.kicad_dru").is_file()
        (source.parent / "Esp32Tap.kicad_prl").write_bytes(b"KiCad sidecar\n")
        seen_sources.append(source)

    globals_dict = fab_tool.export_to_stage.__globals__
    monkeypatch.setitem(globals_dict, "run_kicad", fake_run)
    monkeypatch.setitem(
        globals_dict,
        "validate_stage",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setitem(
        globals_dict,
        "normalize_stage",
        lambda *args, **kwargs: None,
    )
    fab_tool.export_to_stage(board, stage, kicad_cli="kicad-cli")

    assert sentinel.read_bytes() == b"user preferences must survive\n"
    assert len(seen_sources) == 2
    assert seen_sources[0] == seen_sources[1]
    assert not seen_sources[0].parent.exists()


def test_cli_publish_paths_are_confined_to_the_board_kicad_directory(
    fab_tool: SimpleNamespace,
    tmp_path: Path,
) -> None:
    kicad_dir = tmp_path / "kicad"
    kicad_dir.mkdir()
    board = kicad_dir / "Esp32Tap.kicad_pcb"
    board.write_text("board source\n", encoding="utf-8")
    destination = kicad_dir / "gerbers"
    archive = kicad_dir / "Esp32Tap-gerbers.zip"

    fab_tool.validate_publish_paths(board, destination, archive)

    with pytest.raises(fab_tool.FabExportError, match="output directory"):
        fab_tool.validate_publish_paths(board, kicad_dir, archive)
    with pytest.raises(fab_tool.FabExportError, match="archive"):
        fab_tool.validate_publish_paths(board, destination, board)


def test_cli_publish_paths_reject_symlink_and_hardlink_aliases(
    fab_tool: SimpleNamespace,
    tmp_path: Path,
) -> None:
    kicad_dir = tmp_path / "kicad"
    kicad_dir.mkdir()
    board = kicad_dir / "Esp32Tap.kicad_pcb"
    board.write_text("board source\n", encoding="utf-8")
    destination = kicad_dir / "gerbers"
    archive = kicad_dir / "Esp32Tap-gerbers.zip"

    archive.symlink_to(board.name)
    with pytest.raises(fab_tool.FabExportError, match="symlink"):
        fab_tool.validate_publish_paths(board, destination, archive)
    archive.unlink()

    os.link(board, archive)
    with pytest.raises(fab_tool.FabExportError, match="hardlink"):
        fab_tool.validate_publish_paths(board, destination, archive)


def _assembly_fixture() -> dict[str, Any]:
    components = {
        "U1": (
            "PART-A",
            "Lib",
            "Footprint_A",
            "C100",
            "Extended",
            1.25,
            "assembled part",
            {"1": "A"},
        ),
        "C1": (
            "DNP-C",
            "Lib",
            "Footprint_C",
            "C200",
            "Basic",
            0.01,
            "unfitted tuning part",
            {"1": "A"},
        ),
        "TP1": (
            "TEST",
            "TestPoint",
            "TP",
            "",
            "none",
            0.0,
            "test point",
            {"1": "A"},
        ),
    }
    schematic = {
        reference: {
            "value": component[0],
            "footprint": f"{component[1]}:{component[2]}",
            "lcsc": component[3],
            "jlc_class": component[4],
        }
        for reference, component in components.items()
    }
    footprints = {
        reference: {
            "footprint": f"{component[1]}:{component[2]}",
            "at": [110.0 + index, 120.0 + index],
            "layer": "F.Cu",
            "rotation_deg": 270.0,
            "dnp": reference == "C1",
            "excluded_from_bom": (
                reference == "C1" or component[4] == "none"
            ),
            "board_only": False,
        }
        for index, (reference, component) in enumerate(components.items())
    }
    footprints.update(
        {
            reference: {
                "footprint": "MountingHole:MH",
                "at": position,
                "dnp": False,
                "excluded_from_bom": True,
                "board_only": True,
            }
            for reference, position in {
                "MH1": [101.0, 101.0],
                "MH2": [199.0, 101.0],
                "MH3": [199.0, 154.0],
            }.items()
        }
    )
    return {
        "components": components,
        "dnp": {"C1"},
        "schematic": schematic,
        "board": {
            "outline": {"min": [100.0, 100.0], "max": [200.0, 155.0]},
            "footprints": footprints,
        },
        "bom_rows": [
            {
                "Comment": "PART-A",
                "Designator": "U1",
                "Footprint": "Footprint_A",
                "LCSC Part #": "C100",
                "JLC class": "Extended",
                "Qty": "1",
                "Unit cost (USD)": "1.250",
                "Ext cost (USD)": "1.250",
                "Description": "assembled part",
            }
        ],
        "cpl_rows": [
            {
                "Designator": "U1",
                "Val": "PART-A",
                "Package": "Footprint_A",
                "Mid X": "10.000mm",
                "Mid Y": "35.000mm",
                "Rotation": "-90",
                "Layer": "Top",
            }
        ],
    }


def test_assembly_parity_accepts_only_populated_exact_mappings(
    fab_tool: SimpleNamespace,
) -> None:
    fab_tool.validate_assembly_records(**_assembly_fixture())


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("dnp-in-bom", "BOM references"),
        ("wrong-lcsc", "LCSC"),
        ("missing-board-ref", "PCB references"),
        ("wrong-cpl-position", "CPL U1 Mid X"),
        ("wrong-cpl-rotation", "CPL U1 Rotation"),
        ("fractional-board-rotation", "PCB U1 rotation"),
        ("wrong-board-layer", "PCB U1 layer"),
        ("dnp-flag-missing", "PCB C1 assembly flags"),
        ("dnp-not-excluded", "PCB C1 assembly flags"),
        ("populated-excluded", "PCB U1 assembly flags"),
        ("none-not-excluded", "PCB TP1 assembly flags"),
        ("design-board-only", "PCB U1 assembly flags"),
        ("mounting-hole-not-board-only", "PCB MH1 assembly flags"),
        ("extra-bom-column", "BOM columns"),
        ("extra-cpl-column", "CPL columns"),
    ],
)
def test_assembly_parity_fails_closed(
    fab_tool: SimpleNamespace,
    mutation: str,
    message: str,
) -> None:
    fixture = copy.deepcopy(_assembly_fixture())
    if mutation == "dnp-in-bom":
        row = dict(fixture["bom_rows"][0])
        row.update(
            {
                "Comment": "DNP-C",
                "Designator": "C1",
                "Footprint": "Footprint_C",
                "LCSC Part #": "C200",
                "JLC class": "Basic",
                "Unit cost (USD)": "0.010",
                "Ext cost (USD)": "0.010",
                "Description": "unfitted tuning part",
            }
        )
        fixture["bom_rows"].append(row)
    elif mutation == "wrong-lcsc":
        fixture["bom_rows"][0]["LCSC Part #"] = "C999"
    elif mutation == "missing-board-ref":
        fixture["board"]["footprints"].pop("U1")
    elif mutation == "wrong-cpl-position":
        fixture["cpl_rows"][0]["Mid X"] = "10.100mm"
    elif mutation == "wrong-cpl-rotation":
        fixture["cpl_rows"][0]["Rotation"] = "0"
    elif mutation == "fractional-board-rotation":
        fixture["board"]["footprints"]["U1"]["rotation_deg"] = 270.4
    elif mutation == "wrong-board-layer":
        fixture["board"]["footprints"]["U1"]["layer"] = "B.Cu"
    elif mutation == "dnp-flag-missing":
        fixture["board"]["footprints"]["C1"]["dnp"] = False
    elif mutation == "dnp-not-excluded":
        fixture["board"]["footprints"]["C1"]["excluded_from_bom"] = False
    elif mutation == "populated-excluded":
        fixture["board"]["footprints"]["U1"]["excluded_from_bom"] = True
    elif mutation == "none-not-excluded":
        fixture["board"]["footprints"]["TP1"]["excluded_from_bom"] = False
    elif mutation == "design-board-only":
        fixture["board"]["footprints"]["U1"]["board_only"] = True
    elif mutation == "mounting-hole-not-board-only":
        fixture["board"]["footprints"]["MH1"]["board_only"] = False
    elif mutation == "extra-bom-column":
        fixture["bom_rows"][0]["Supplier note"] = "silently ignored"
    else:
        fixture["cpl_rows"][0]["Feeder"] = "silently ignored"

    with pytest.raises(fab_tool.FabExportError, match=message):
        fab_tool.validate_assembly_records(**fixture)
