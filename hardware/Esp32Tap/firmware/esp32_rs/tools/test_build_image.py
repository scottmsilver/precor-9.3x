#!/usr/bin/env python3
"""Host-only contract tests for the provenance-bound Docker image wrapper."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parent
ESP32_RS = TOOLS.parent
SCRIPT = TOOLS / "build_image.sh"
DOCKERIGNORE = ESP32_RS / ".dockerignore"
README = ESP32_RS / "README.md"
IMAGE_ID = "sha256:" + "a" * 64
IMAGE_TAG = "example/esp32tap:test"
COMMON = {
    "schema_version": 1,
    "idf_commit": "b" * 40,
    "rustc_verbose": "rustc 1.90.0-dev\nbinary: rustc\ncommit-hash: " + "c" * 40,
    "target": "xtensa-esp32s3-espidf",
    "linker_version": "ldproxy 0.3.4",
    "esptool_version": "esptool.py v4.9.0",
}


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def attestation(context: Path) -> dict[str, object]:
    return {
        **COMMON,
        "component_lock_sha256": hashlib.sha256(
            (context / "esp32tap" / "components_esp32s3.lock").read_bytes()
        ).hexdigest(),
    }


@pytest.fixture
def context(tmp_path: Path) -> Path:
    root = tmp_path / "esp32_rs"
    (root / "tools").mkdir(parents=True)
    (root / "esp32tap").mkdir()
    shutil.copyfile(SCRIPT, root / "tools" / "build_image.sh")
    (root / "tools" / "build_image.sh").chmod(0o755)
    shutil.copyfile(DOCKERIGNORE, root / ".dockerignore")
    (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (root / "esp32tap" / "components_esp32s3.lock").write_bytes(
        b"managed-component-lock\n"
    )
    return root


@pytest.fixture
def fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.jsonl"
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
argv = sys.argv[1:]
fail = os.environ.get("FAKE_DOCKER_FAIL", "")
if fail and argv and argv[0] == fail:
    print("forced fake-docker failure", file=sys.stderr)
    raise SystemExit(17)
if argv and argv[0] == "build" and os.environ.get("FAKE_DOCKER_MUTATE_CONTEXT"):
    with open(os.path.join(argv[-1], "Dockerfile"), "a", encoding="utf-8") as stream:
        stream.write("# concurrent edit\\n")
if argv[:2] == ["image", "inspect"]:
    print(os.environ["FAKE_DOCKER_INSPECT"])
elif argv and argv[0] == "run":
    print(os.environ.get("FAKE_DOCKER_PROBE", "{}"))
elif argv and argv[0] == "commit":
    print("sha256:" + "d" * 64)
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return fake_bin, log


def run(
    context: Path,
    fake_docker: tuple[Path, Path],
    *args: str,
    labels: dict[str, str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    fake_bin, log = fake_docker
    inspect = [
        {
            "Id": IMAGE_ID,
            "Config": {
                "Labels": labels
                if labels is not None
                else {
                    "org.treddy.esp32tap.recipe-sha256": recipe(context),
                    "org.treddy.esp32tap.toolchain-json": canonical(
                        attestation(context)
                    ),
                }
            },
        }
    ]
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_DOCKER_INSPECT": canonical(inspect),
        "FAKE_DOCKER_PROBE": canonical(COMMON),
        "RUST_IMAGE": IMAGE_TAG,
        "BUILD_IMAGE_DOCKER_TIMEOUT": "5",
        **(extra_env or {}),
    }
    return subprocess.run(
        [str(context / "tools" / "build_image.sh"), *args],
        cwd=context,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def recipe(context: Path) -> str:
    completed = subprocess.run(
        [str(context / "tools" / "build_image.sh"), "--recipe"],
        cwd=context,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def docker_calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_recipe_frames_allowed_path_mode_and_content(context: Path) -> None:
    original = recipe(context)
    (context / "Dockerfile").write_text("FROM scratch\n# changed\n", encoding="utf-8")
    assert recipe(context) != original

    content_digest = recipe(context)
    (context / "Dockerfile").chmod(0o755)
    assert recipe(context) != content_digest

    mode_digest = recipe(context)
    (context / ".dockerignore").write_text(
        "**\n!Dockerfile\n!.dockerignore\n# exact policy changed\n",
        encoding="utf-8",
    )
    assert recipe(context) != mode_digest


def test_recipe_ignores_build_targets_and_caches(context: Path) -> None:
    original = recipe(context)
    for relative in (
        "build/esp32tap.bin",
        "build_qemu_test/esp32tap.bin",
        "esp32tap/target/debug/object",
        ".cache/download",
    ):
        path = context / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(os.urandom(31))
    assert recipe(context) == original


def test_dockerignore_is_exact_deny_by_default_policy() -> None:
    assert DOCKERIGNORE.read_text(encoding="utf-8").splitlines() == [
        "**",
        "!Dockerfile",
        "!.dockerignore",
    ]


def test_check_emits_complete_canonical_production_toolchain(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    completed = run(context, fake_docker, "--check", "--kind", "production")
    assert completed.returncode == 0, completed.stderr
    expected = {
        **{key: value for key, value in COMMON.items() if key != "schema_version"},
        "image_id": IMAGE_ID,
        "recipe_sha256": recipe(context),
        "image_tag": IMAGE_TAG,
        "component_lock_sha256": attestation(context)["component_lock_sha256"],
        "profile": "release",
        "features": [],
    }
    assert completed.stdout == canonical(expected) + "\n"
    assert docker_calls(fake_docker[1]) == [["image", "inspect", IMAGE_TAG]]


def test_check_emits_kind_specific_qemu_toolchain(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    completed = run(
        context,
        fake_docker,
        "--check",
        "--kind",
        "qemu-test",
        extra_env={"PROFILE": "bench-release"},
    )
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)
    assert value["profile"] == "release"
    assert value["features"] == ["ble", "net", "qemu-test"]
    assert docker_calls(fake_docker[1]) == [["image", "inspect", IMAGE_TAG]]


@pytest.mark.parametrize(
    "image_tag",
    [".bad", "_bad", "/bad", ":bad", "@bad", "+bad", "-" + "bad", "a" * 16385],
)
def test_rejects_image_tags_outside_toolchain_schema_without_docker(
    context: Path,
    fake_docker: tuple[Path, Path],
    image_tag: str,
) -> None:
    completed = run(
        context,
        fake_docker,
        "--check",
        "--kind",
        "production",
        extra_env={"RUST_IMAGE": image_tag},
    )
    assert completed.returncode == 2
    assert "invalid RUST_IMAGE tag" in completed.stderr
    assert docker_calls(fake_docker[1]) == []


@pytest.mark.parametrize(
    "labels",
    [
        {},
        {"org.treddy.esp32tap.recipe-sha256": "0" * 64},
        {
            "org.treddy.esp32tap.recipe-sha256": "CURRENT",
            "org.treddy.esp32tap.toolchain-json": "{",
        },
        {
            "org.treddy.esp32tap.recipe-sha256": "CURRENT",
            "org.treddy.esp32tap.toolchain-json": "PRETTY",
        },
    ],
)
def test_check_rejects_missing_stale_malformed_or_noncanonical_labels(
    context: Path,
    fake_docker: tuple[Path, Path],
    labels: dict[str, str],
) -> None:
    labels = {
        key: (
            recipe(context)
            if value == "CURRENT"
            else json.dumps(attestation(context), indent=2)
            if value == "PRETTY"
            else value
        )
        for key, value in labels.items()
    }
    completed = run(
        context, fake_docker, "--check", "--kind", "production", labels=labels
    )
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert docker_calls(fake_docker[1]) == [["image", "inspect", IMAGE_TAG]]


@pytest.mark.parametrize(
    "mutation",
    ["mutable-id", "extra-field", "invalid-commit"],
)
def test_check_rejects_mutable_id_or_invalid_attestation(
    context: Path,
    fake_docker: tuple[Path, Path],
    mutation: str,
) -> None:
    value = {
        "Id": IMAGE_ID,
        "Config": {
            "Labels": {
                "org.treddy.esp32tap.recipe-sha256": recipe(context),
                "org.treddy.esp32tap.toolchain-json": canonical(
                    attestation(context)
                ),
            }
        },
    }
    if mutation == "mutable-id":
        value["Id"] = "example/esp32tap:mutable"
    else:
        changed = attestation(context)
        if mutation == "extra-field":
            changed["unexpected"] = "field"
        else:
            changed["idf_commit"] = "not-a-commit"
        value["Config"]["Labels"]["org.treddy.esp32tap.toolchain-json"] = (
            canonical(changed)
        )
    completed = run(
        context,
        fake_docker,
        "--check",
        "--kind",
        "production",
        extra_env={"FAKE_DOCKER_INSPECT": canonical([value])},
    )
    assert completed.returncode != 0
    assert docker_calls(fake_docker[1]) == [["image", "inspect", IMAGE_TAG]]


def test_check_rejects_boolean_schema_version(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    invalid = attestation(context)
    invalid["schema_version"] = True
    labels = {
        "org.treddy.esp32tap.recipe-sha256": recipe(context),
        "org.treddy.esp32tap.toolchain-json": canonical(invalid),
    }
    completed = run(
        context, fake_docker, "--check", "--kind", "production", labels=labels
    )
    assert completed.returncode != 0
    assert "schema" in completed.stderr
    assert docker_calls(fake_docker[1]) == [["image", "inspect", IMAGE_TAG]]


@pytest.mark.parametrize(
    "args",
    [
        ("--check",),
        ("--check", "--kind", "unknown"),
        ("--recipe", "extra"),
        ("--kind", "production", "--check"),
    ],
)
def test_rejects_ambiguous_cli_without_invoking_docker(
    context: Path, fake_docker: tuple[Path, Path], args: tuple[str, ...]
) -> None:
    completed = run(context, fake_docker, *args)
    assert completed.returncode != 0
    assert docker_calls(fake_docker[1]) == []


def test_default_build_probes_once_then_commits_labels(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    completed = run(context, fake_docker)
    assert completed.returncode == 0, completed.stderr
    calls = docker_calls(fake_docker[1])
    assert [call[0] for call in calls].count("run") == 1
    assert [call[0] for call in calls].count("build") == 1
    commits = [call for call in calls if call[0] == "commit"]
    assert len(commits) == 1
    assert commits[0][-1] == IMAGE_TAG
    changes = [
        commits[0][index + 1]
        for index, value in enumerate(commits[0])
        if value == "--change"
    ]
    assert any(
        "org.treddy.esp32tap.recipe-sha256" in value
        and recipe(context) in value
        for value in changes
    )
    assert any(
        "org.treddy.esp32tap.toolchain-json" in value
        and '\\"component_lock_sha256\\"' in value
        for value in changes
    )
    assert "eval " not in (context / "tools" / "build_image.sh").read_text(
        encoding="utf-8"
    )


def test_failed_probe_does_not_commit_over_final_tag(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    completed = run(
        context,
        fake_docker,
        extra_env={"FAKE_DOCKER_PROBE": "not json"},
    )
    assert completed.returncode != 0
    calls = docker_calls(fake_docker[1])
    assert not any(call[0] == "commit" for call in calls)
    assert any(call[:2] == ["rm", "-f"] for call in calls)
    assert any(call[:2] == ["image", "rm"] for call in calls)


def test_boolean_probe_schema_version_never_reaches_final_tag(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    invalid = {**COMMON, "schema_version": True}
    completed = run(
        context,
        fake_docker,
        extra_env={"FAKE_DOCKER_PROBE": canonical(invalid)},
    )
    assert completed.returncode != 0
    assert "schema" in completed.stderr
    calls = docker_calls(fake_docker[1])
    assert not any(call[0] == "commit" for call in calls)


def test_context_mutation_during_candidate_build_prevents_publication(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    completed = run(
        context,
        fake_docker,
        extra_env={"FAKE_DOCKER_MUTATE_CONTEXT": "1"},
    )
    assert completed.returncode != 0
    assert "changed during image build" in completed.stderr
    calls = docker_calls(fake_docker[1])
    assert not any(call[0] == "commit" for call in calls)


def test_consecutive_builds_use_unique_candidate_resources(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    assert run(context, fake_docker).returncode == 0
    assert run(context, fake_docker).returncode == 0
    calls = docker_calls(fake_docker[1])
    builds = [call for call in calls if call[0] == "build"]
    runs = [call for call in calls if call[0] == "run"]
    assert builds[0][builds[0].index("--tag") + 1] != builds[1][builds[1].index("--tag") + 1]
    assert runs[0][runs[0].index("--name") + 1] != runs[1][runs[1].index("--name") + 1]


def test_script_is_executable_and_tracked_as_100755() -> None:
    assert stat.S_IMODE(SCRIPT.stat().st_mode) == 0o755
    completed = subprocess.run(
        ["git", "ls-files", "--stage", str(SCRIPT.relative_to(ESP32_RS.parents[3]))],
        cwd=ESP32_RS.parents[3],
        text=True,
        capture_output=True,
        check=True,
    )
    assert completed.stdout.split(maxsplit=1)[0] == "100755"


def test_readme_documents_tracked_partition_and_attested_image_workflow() -> None:
    text = README.read_text(encoding="utf-8")
    prose = " ".join(text.split())
    assert "partitions_esp32tap.csv       tracked partition-table source" in text
    assert "partitions_esp32tap.csv    ->" not in text
    assert (
        "`build_qemu_test/` remains a tracked legacy bundle until the clean-build"
        in prose
    )
    assert "tools/build_image.sh              # build and label the pinned image" in text
    assert "tools/build_image.sh --check --kind production" in text
    assert "tools/build_image.sh --check --kind qemu-test" in text
    assert "`--check` performs one `docker image inspect` and never starts a container" in text
    assert "the recipe SHA-256 is not the Docker image ID" in text
