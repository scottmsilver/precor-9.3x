from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from enclosure import validate_enclosure

OPENSCAD_IMAGE = "openscad/openscad@sha256:" "147e48525bec392bcf628d7a6d5ea4ccac71b16251952328f86e1061cbf47c37"


def _edge_incidence(mesh: trimesh.Trimesh) -> list[int]:
    counts = [0] * len(mesh.edges_unique)
    for unique_index in mesh.edges_unique_inverse:
        counts[int(unique_index)] += 1
    return counts


def _sample_report() -> dict[str, object]:
    return {
        "schema_version": 1,
        "board": {
            "outline": {
                "min": [100.0, 97.0],
                "max": [195.0, 155.0],
                "width_mm": 95.0,
                "height_mm": 58.0,
            },
            "footprints": {
                # Rev D: J1 and J2 are both the Molex 441440003 RJ45 jack
                # (LCSC C585890) — identical part, so identical
                # fabrication_body_bbox size (only the board-Y offset
                # differs). Values are the real inspect_kicad.py output for
                # the generated Rev D board.
                "J1": {
                    "at": [108.0, 115.0],
                    "fabrication_body_bbox": {
                        "min": [102.73, 107.21],
                        "max": [119.9, 122.69],
                    },
                    "pads": {str(index): {"at": [102.1, 119.45 - 1.27 * (index - 1)]} for index in range(1, 9)},
                },
                "J2": {
                    "at": [108.0, 137.0],
                    "fabrication_body_bbox": {
                        "min": [102.73, 129.21],
                        "max": [119.9, 144.69],
                    },
                    "pads": {str(index): {"at": [102.1, 141.45 - 1.27 * (index - 1)]} for index in range(1, 9)},
                },
                "J3": {"at": [191.2, 136.5], "pads": {}},
                "SW1": {"at": [142.0, 104.0], "pads": {}},
                "SW2": {"at": [191.0, 117.0], "pads": {}},
                "MH1": {"at": [120.0, 103.0], "pads": {}},
                "MH2": {"at": [148.0, 103.0], "pads": {}},
                "MH3": {"at": [192.0, 152.0], "pads": {}},
            },
            "antenna": {
                "reference": "U1",
                "physical_edge_y_mm": 100.3,
                "span_x_mm": [169.0, 187.0],
            },
        },
    }


def _isolated_enclosure_project(
    esp32tap_dir: Path,
    tmp_path: Path,
) -> tuple[Path, Path]:
    project = tmp_path / "Esp32Tap"
    enclosure = project / "enclosure"
    enclosure.mkdir(parents=True)
    for filename in (
        "esp32tap_case.scad",
        "esp32tap_base.stl",
        "esp32tap_lid.stl",
    ):
        shutil.copyfile(
            esp32tap_dir / "enclosure" / filename,
            enclosure / filename,
        )
    inspection = project / "inspection.json"
    inspection.write_text(json.dumps(_sample_report()), encoding="utf-8")
    return project, inspection


def test_scad_encodes_rev_c_air_gap_and_overlapping_posts(
    esp32tap_dir: Path,
) -> None:
    source = (esp32tap_dir / "enclosure" / "esp32tap_case.scad").read_text(encoding="utf-8")
    parameters = validate_enclosure.parse_scad_parameters(source)

    assert parameters["ant_air_gap"] == pytest.approx(15.0)
    assert parameters["post_d"] == pytest.approx(7.0)
    assert parameters["post_wall_overlap"] == pytest.approx(0.25)
    assert parameters["post_inset"] == pytest.approx(3.25)
    assert parameters["ant_x0"] == pytest.approx(69.0)
    assert parameters["ant_x1"] == pytest.approx(87.0)
    assert parameters["j1_yc"] == pytest.approx(18.0)
    assert parameters["j2_yc"] == pytest.approx(40.0)
    assert "post_inset = post_d / 2 - post_wall_overlap;" in source
    assert source.count("wall+post_inset") == 4
    assert "d = post_d + 0.6" in source


def test_enclosure_dimensions_are_derived_from_rev_c_parameters(
    esp32tap_dir: Path,
) -> None:
    source = (esp32tap_dir / "enclosure" / "esp32tap_case.scad").read_text(encoding="utf-8")
    parameters = validate_enclosure.parse_scad_parameters(source)

    assert validate_enclosure.expected_dimensions(parameters) == {
        "interior_length_mm": pytest.approx(99.0),
        "interior_width_mm": pytest.approx(78.7),
        "outer_length_mm": pytest.approx(104.0),
        "outer_width_mm": pytest.approx(83.7),
        "base_height_mm": pytest.approx(23.6),
        "antenna_void_mm": pytest.approx(15.0),
        "post_wall_overlap_mm": pytest.approx(0.25),
        "lid_post_relief_mm": pytest.approx(7.6),
    }


@pytest.mark.parametrize("filename", ["esp32tap_base.stl", "esp32tap_lid.stl"])
def test_checked_in_mesh_is_one_valid_manifold_volume(
    esp32tap_dir: Path,
    filename: str,
) -> None:
    path = esp32tap_dir / "enclosure" / filename
    loaded = trimesh.load_mesh(path, process=True)
    assert isinstance(loaded, trimesh.Trimesh), filename
    loaded.merge_vertices(digits_vertex=6)
    assert len(loaded.split(only_watertight=False)) == 1, filename
    assert loaded.volume > 0, filename
    assert loaded.is_watertight, filename
    assert loaded.is_winding_consistent, filename
    assert set(_edge_incidence(loaded)) == {2}, filename


def test_checked_in_mesh_matches_rev_c_outer_dimensions(
    esp32tap_dir: Path,
) -> None:
    enclosure = esp32tap_dir / "enclosure"
    source = (enclosure / "esp32tap_case.scad").read_text(encoding="utf-8")
    expected = validate_enclosure.expected_dimensions(validate_enclosure.parse_scad_parameters(source))
    base = trimesh.load_mesh(enclosure / "esp32tap_base.stl", process=True)
    lid = trimesh.load_mesh(enclosure / "esp32tap_lid.stl", process=True)

    assert isinstance(base, trimesh.Trimesh)
    assert isinstance(lid, trimesh.Trimesh)
    assert base.bounds[1][0] == pytest.approx(
        expected["outer_length_mm"] + 8.0,
        abs=0.02,
    )
    assert base.bounds[1][1] == pytest.approx(
        expected["outer_width_mm"],
        abs=0.02,
    )
    assert base.bounds[1][2] == pytest.approx(
        expected["base_height_mm"],
        abs=0.02,
    )
    assert lid.extents[0] == pytest.approx(
        expected["outer_length_mm"],
        abs=0.02,
    )
    assert lid.extents[1] == pytest.approx(
        expected["outer_width_mm"],
        abs=0.02,
    )


def test_board_geometry_derives_connector_centers_independently() -> None:
    geometry = validate_enclosure.derive_board_geometry(_sample_report())

    assert geometry["board_size_mm"] == pytest.approx([95.0, 58.0])
    assert geometry["rj45_centers_y_mm"] == pytest.approx([18.0, 40.0])
    assert geometry["usb_center_y_mm"] == pytest.approx(39.5)
    for actual, expected in zip(
        geometry["mounting_holes_mm"],
        [[20.0, 6.0], [48.0, 6.0], [92.0, 55.0]],
        strict=True,
    ):
        assert actual == pytest.approx(expected)
    assert geometry["antenna_overhang_mm"] == pytest.approx(-3.3)
    assert geometry["antenna_span_x_mm"] == pytest.approx([69.0, 87.0])


def test_inspector_antenna_schema_rejects_legacy_edge_alias() -> None:
    report = _sample_report()
    board = report["board"]
    assert isinstance(board, dict)
    antenna = board["antenna"]
    assert isinstance(antenna, dict)
    antenna["edge_y_mm"] = antenna.pop("physical_edge_y_mm")

    with pytest.raises(
        validate_enclosure.ValidationError,
        match="physical_edge_y_mm",
    ):
        validate_enclosure.derive_board_geometry(report)


def test_validator_rejects_stale_rj45_and_tangent_post_geometry(
    esp32tap_dir: Path,
) -> None:
    source = (esp32tap_dir / "enclosure" / "esp32tap_case.scad").read_text(encoding="utf-8")
    parameters = validate_enclosure.parse_scad_parameters(source)
    geometry = validate_enclosure.derive_board_geometry(_sample_report())

    stale_connector = dict(parameters, j1_yc=3.555)
    with pytest.raises(
        validate_enclosure.ValidationError,
        match="J1 center",
    ):
        validate_enclosure.validate_fit(source, stale_connector, geometry)

    tangent_post = dict(
        parameters,
        post_wall_overlap=0.0,
        post_inset=parameters["post_d"] / 2,
    )
    with pytest.raises(validate_enclosure.ValidationError):
        validate_enclosure.validate_fit(source, tangent_post, geometry)

    stale_mounting_holes = source.replace(
        "mh = [[20.0, 6.0], [48.0, 6.0], [92.0, 55.0]];",
        "mh = [[20.0, 6.0], [47.0, 6.0], [92.0, 55.0]];",
    )
    assert stale_mounting_holes != source
    with pytest.raises(
        validate_enclosure.ValidationError,
        match="MH2 SCAD",
    ):
        validate_enclosure.validate_fit(
            stale_mounting_holes,
            parameters,
            geometry,
        )


def test_validator_rejects_same_bounds_solid_box_impostor(
    esp32tap_dir: Path,
    tmp_path: Path,
) -> None:
    project, inspection = _isolated_enclosure_project(
        esp32tap_dir,
        tmp_path,
    )
    impostor = trimesh.creation.box(extents=[123.0, 90.3, 23.6])
    impostor.apply_translation([55.5, 45.15, 11.8])
    impostor.export(project / "enclosure" / "esp32tap_base.stl")

    with pytest.raises(
        validate_enclosure.ValidationError,
        match="canonical render|cavity",
    ):
        validate_enclosure.validate(project, inspection)


def test_functional_probes_reject_solid_box_as_missing_cavity(
    esp32tap_dir: Path,
    tmp_path: Path,
) -> None:
    source = (esp32tap_dir / "enclosure" / "esp32tap_case.scad").read_text(encoding="utf-8")
    parameters = validate_enclosure.parse_scad_parameters(source)
    geometry = validate_enclosure.derive_board_geometry(_sample_report())
    impostor_path = tmp_path / "solid-box.stl"
    impostor = trimesh.creation.box(extents=[123.0, 90.3, 23.6])
    impostor.apply_translation([55.5, 45.15, 11.8])
    impostor.export(impostor_path)

    with pytest.raises(
        validate_enclosure.ValidationError,
        match="main cavity",
    ):
        validate_enclosure.validate_functional_geometry(
            impostor_path,
            esp32tap_dir / "enclosure" / "esp32tap_lid.stl",
            parameters,
            geometry,
        )


def test_validator_rejects_active_scad_geometry_not_present_in_mesh(
    esp32tap_dir: Path,
    tmp_path: Path,
) -> None:
    project, inspection = _isolated_enclosure_project(
        esp32tap_dir,
        tmp_path,
    )
    source_path = project / "enclosure" / "esp32tap_case.scad"
    source = source_path.read_text(encoding="utf-8")
    changed = source.replace(
        "wall + by0 + yc - aperture_w/2",
        "wall + by0 + yc + 4 - aperture_w/2",
    )
    assert changed != source
    source_path.write_text(changed, encoding="utf-8")

    with pytest.raises(
        validate_enclosure.ValidationError,
        match="canonical render",
    ):
        validate_enclosure.validate(project, inspection)


def test_sample_inspector_and_checked_enclosure_validate_together(
    esp32tap_dir: Path,
    tmp_path: Path,
) -> None:
    inspection = tmp_path / "inspection.json"
    inspection.write_text(json.dumps(_sample_report()), encoding="utf-8")

    result = validate_enclosure.validate(esp32tap_dir, inspection)

    assert result["status"] == "PASS"
    assert result["functional_geometry"]["probe_count"] >= 70
    assert result["functional_geometry"]["antenna_inner_wall_to_edge_mm"] == pytest.approx(15.0)


def test_actual_board_and_enclosure_validate_together(
    esp32tap_dir: Path,
) -> None:
    inspector = esp32tap_dir / "tools" / "inspect_kicad.py"
    if not inspector.is_file():
        pytest.skip("Task 4 inspector has not landed on this branch yet")
    completed = subprocess.run(
        [
            "python3",
            str(esp32tap_dir / "enclosure" / "validate_enclosure.py"),
            "--project-dir",
            str(esp32tap_dir),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "PASS"
    assert result["openscad_image"] == OPENSCAD_IMAGE
    assert math.isclose(result["antenna_void_mm"], 15.0, abs_tol=0.01)


def test_rev_c_geometry_is_derived_from_inspector_report() -> None:
    geometry = validate_enclosure.derive_board_geometry(_sample_report())

    assert geometry["board_size_mm"] == pytest.approx([95.0, 58.0])
    for actual, expected in zip(
        geometry["connector_centers_mm"],
        [[8.0, 18.0], [8.0, 40.0]],
        strict=True,
    ):
        assert actual == pytest.approx(expected)
    assert geometry["connector_body_widths_mm"] == pytest.approx([15.48, 15.48])
    assert geometry["usb_center_mm"] == pytest.approx([91.2, 39.5])
    for actual, expected in zip(
        geometry["switch_centers_mm"],
        [[42.0, 7.0], [91.0, 20.0]],
        strict=True,
    ):
        assert actual == pytest.approx(expected)
    assert geometry["antenna_overhang_mm"] == pytest.approx(-3.3)
    assert geometry["antenna_span_x_mm"] == pytest.approx([69.0, 87.0])


def test_rev_d_source_encodes_rj45_aperture_and_service_contract(
    esp32tap_dir: Path,
) -> None:
    # Rev D: J1 and J2 are the identical Molex 441440003 RJ45 jack; there
    # is no mechanical keying between console and motor any more (see
    # validate_rj45_aperture), so this only checks the shared aperture/
    # service-envelope contract, not per-connector housing/key geometry.
    source = (esp32tap_dir / "enclosure" / "esp32tap_case.scad").read_text(encoding="utf-8")
    parameters = validate_enclosure.parse_scad_parameters(source)

    assert parameters["board_l"] == pytest.approx(95.0)
    assert parameters["board_w"] == pytest.approx(58.0)
    assert parameters["ant_overhang"] == pytest.approx(-3.3)
    assert parameters["j1_yc"] == pytest.approx(18.0)
    assert parameters["j2_yc"] == pytest.approx(40.0)
    assert parameters["rj45_body_w"] == pytest.approx(15.48)
    assert parameters["rj45_body_depth"] == pytest.approx(17.17)
    assert parameters["rj45_body_h"] == pytest.approx(13.4)
    assert parameters["aperture_w"] == pytest.approx(16.0)
    assert parameters["aperture_h"] == pytest.approx(14.0)
    assert parameters["cable_exit_direction"] == pytest.approx(-1.0)
    assert parameters["cable_bend_radius"] == pytest.approx(18.0)
    assert parameters["latch_clearance"] >= 6.0
    assert parameters["snap_clearance"] == pytest.approx(0.3)
    assert "module rj45_wall_aperture" in source
    assert "module rj45_plug_service_envelope" in source
    assert "cable_exit_direction" in source
    assert "module snap_latch" in source


def test_rj45_aperture_clears_the_jack_body_by_0p2mm() -> None:
    result = validate_enclosure.validate_rj45_aperture(
        body_width=15.48,
        aperture_width=16.0,
        aperture_height=14.0,
        body_height=13.4,
    )

    assert result["width_clearance_mm"] == pytest.approx(0.26)


def test_rj45_aperture_rejects_insufficient_width_clearance() -> None:
    with pytest.raises(
        validate_enclosure.ValidationError,
        match="0.2 mm clearance",
    ):
        validate_enclosure.validate_rj45_aperture(
            body_width=15.9,
            aperture_width=16.0,
            aperture_height=14.0,
            body_height=13.4,
        )


def test_enclosure_evidence_remains_model_only_and_physical_open(
    esp32tap_dir: Path,
) -> None:
    model = json.loads((esp32tap_dir / "evidence" / "model.json").read_text(encoding="utf-8"))
    physical = json.loads((esp32tap_dir / "evidence" / "physical.json").read_text(encoding="utf-8"))

    claims = {assertion["claim"] for assertion in model["assertions"]}
    assert "Rev C enclosure CAD fit and RJ45 aperture geometry" in claims
    assertion = next(
        item for item in model["assertions"] if item["claim"] == "Rev C enclosure CAD fit and RJ45 aperture geometry"
    )
    artifact_path = esp32tap_dir / "evidence" / assertion["artifact_path"]
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert assertion["artifact_sha256"] == hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    assert artifact["board_size_mm"] == [95.0, 58.0, 1.6]
    assert artifact["antenna_void_mm"] == pytest.approx(15.0)
    assert artifact["rj45_jack"]["mpn"] == "441440003"
    assert artifact["rj45_jack"]["lcsc"] == "C585890"
    for relative, expected_hash in artifact["artifacts"].items():
        assert hashlib.sha256((esp32tap_dir / relative).read_bytes()).hexdigest() == (expected_hash)
    assert physical["status"] == "NOT_MEASURED"
    assert model["status"] == "MODELED"
