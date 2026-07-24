from __future__ import annotations

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


OPENSCAD_IMAGE = (
    "openscad/openscad@sha256:"
    "147e48525bec392bcf628d7a6d5ea4ccac71b16251952328f86e1061cbf47c37"
)


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
                "min": [100.0, 100.0],
                "max": [200.0, 155.0],
                "width_mm": 100.0,
                "height_mm": 55.0,
            },
            "footprints": {
                "J1": {
                    "at": [112.5, 108.0],
                    "pads": {
                        str(index): {
                            "at": [
                                112.5 if index % 2 else 115.04,
                                108.0 + 1.27 * (index - 1),
                            ]
                        }
                        for index in range(1, 9)
                    },
                },
                "J2": {
                    "at": [112.5, 137.0],
                    "pads": {
                        str(index): {
                            "at": [
                                112.5 if index % 2 else 115.04,
                                137.0 + 1.27 * (index - 1),
                            ]
                        }
                        for index in range(1, 9)
                    },
                },
                "J3": {"at": [196.2, 136.5], "pads": {}},
                "MH1": {"at": [102.9, 126.5], "pads": {}},
                "MH2": {"at": [197.0, 103.0], "pads": {}},
                "MH3": {"at": [197.0, 152.0], "pads": {}},
            },
            "antenna": {
                "reference": "U1",
                "physical_edge_y_mm": 93.7,
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


def test_scad_encodes_rev_b_air_gap_and_overlapping_posts(
    esp32tap_dir: Path,
) -> None:
    source = (
        esp32tap_dir / "enclosure" / "esp32tap_case.scad"
    ).read_text(encoding="utf-8")
    parameters = validate_enclosure.parse_scad_parameters(source)

    assert parameters["ant_air_gap"] == pytest.approx(15.0)
    assert parameters["post_d"] == pytest.approx(7.0)
    assert parameters["post_wall_overlap"] == pytest.approx(0.25)
    assert parameters["post_inset"] == pytest.approx(3.25)
    assert parameters["ant_x0"] == pytest.approx(69.0)
    assert parameters["ant_x1"] == pytest.approx(87.0)
    assert parameters["j1_yc"] == pytest.approx(12.445)
    assert parameters["j2_yc"] == pytest.approx(41.445)
    assert "post_inset = post_d / 2 - post_wall_overlap;" in source
    assert source.count("wall+post_inset") == 4
    assert "d = post_d + 0.6" in source


def test_enclosure_dimensions_are_derived_from_rev_b_parameters(
    esp32tap_dir: Path,
) -> None:
    source = (
        esp32tap_dir / "enclosure" / "esp32tap_case.scad"
    ).read_text(encoding="utf-8")
    parameters = validate_enclosure.parse_scad_parameters(source)

    assert validate_enclosure.expected_dimensions(parameters) == {
        "interior_length_mm": pytest.approx(104.0),
        "interior_width_mm": pytest.approx(85.3),
        "outer_length_mm": pytest.approx(109.0),
        "outer_width_mm": pytest.approx(90.3),
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


def test_checked_in_mesh_matches_rev_b_outer_dimensions(
    esp32tap_dir: Path,
) -> None:
    enclosure = esp32tap_dir / "enclosure"
    source = (enclosure / "esp32tap_case.scad").read_text(encoding="utf-8")
    expected = validate_enclosure.expected_dimensions(
        validate_enclosure.parse_scad_parameters(source)
    )
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

    assert geometry["board_size_mm"] == pytest.approx([100.0, 55.0])
    assert geometry["rj45_centers_y_mm"] == pytest.approx([12.445, 41.445])
    assert geometry["usb_center_y_mm"] == pytest.approx(36.5)
    for actual, expected in zip(
        geometry["mounting_holes_mm"],
        [[2.9, 26.5], [97.0, 3.0], [97.0, 52.0]],
        strict=True,
    ):
        assert actual == pytest.approx(expected)
    assert geometry["antenna_overhang_mm"] == pytest.approx(6.3)
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
    source = (
        esp32tap_dir / "enclosure" / "esp32tap_case.scad"
    ).read_text(encoding="utf-8")
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
        "mh = [[2.9, 26.5], [97.0, 3.0], [97.0, 52.0]];",
        "mh = [[2.9, 26.5], [96.0, 3.0], [97.0, 52.0]];",
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
    source = (
        esp32tap_dir / "enclosure" / "esp32tap_case.scad"
    ).read_text(encoding="utf-8")
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
        "wall + by0 + yc - (rj45_w/2 + 0.5)",
        "wall + by0 + yc + 4 - (rj45_w/2 + 0.5)",
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
    assert result["functional_geometry"][
        "antenna_inner_wall_to_edge_mm"
    ] == pytest.approx(15.0)


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
