#!/usr/bin/env python3
"""Host-only contract tests for the provenance-bound Docker image wrapper."""

from __future__ import annotations

import ast
import fcntl
import hashlib
import json
import os
import signal
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parent
ESP32_RS = TOOLS.parent
SCRIPT = TOOLS / "build_image.sh"
DOCKERIGNORE = ESP32_RS / ".dockerignore"
README = ESP32_RS / "README.md"
IMAGE_ID = "sha256:" + "a" * 64
IMAGE_TAG = "example/esp32tap:test"
PUBLICATION_LOCK = Path("/tmp") / "esp32tap-image-publication.lock"
LDPROXY_SOURCE = "registry+https://github.com/rust-lang/crates.io-index"
FAKE_LDPROXY_PROGRAM = """#!/usr/bin/env python3
import sys

print("ldproxy entered link mode", file=sys.stderr)
raise SystemExit(101)
"""
FAKE_LDPROXY_SHA256 = hashlib.sha256(FAKE_LDPROXY_PROGRAM.encode()).hexdigest()
COMMON = {
    "schema_version": 1,
    "idf_commit": "b" * 40,
    "rustc_verbose": "rustc 1.90.0-dev\nbinary: rustc\ncommit-hash: " + "c" * 40,
    "target": "xtensa-esp32s3-espidf",
    "linker_version": (
        f"ldproxy 0.3.4 ({LDPROXY_SOURCE}) sha256:{FAKE_LDPROXY_SHA256}"
    ),
    "esptool_version": "esptool.py v4.9.0",
}


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def cargo_install_metadata(
    package: str = "ldproxy", version: str = "0.3.4"
) -> dict[str, object]:
    return {
        "installs": {
            f"{package} {version} ({LDPROXY_SOURCE})": {
                "version_req": f"={version}",
                "bins": ["ldproxy"],
                "features": [],
                "all_features": False,
                "no_default_features": False,
                "profile": "release",
                "target": "x86_64-unknown-linux-gnu",
                "rustc": "rustc fake installer",
            }
        }
    }


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
    cargo_home = tmp_path / "cargo"
    cargo_bin = cargo_home / "bin"
    cargo_bin.mkdir(parents=True)
    ldproxy = cargo_bin / "ldproxy"
    ldproxy.write_text(FAKE_LDPROXY_PROGRAM, encoding="utf-8")
    ldproxy.chmod(0o755)
    (cargo_home / ".crates2.json").write_text(
        canonical(cargo_install_metadata()),
        encoding="utf-8",
    )
    log = tmp_path / "docker.jsonl"
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/usr/bin/env python3
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time

with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
argv = sys.argv[1:]
fail = os.environ.get("FAKE_DOCKER_FAIL", "")
if fail and argv and argv[0] == fail:
    print("forced fake-docker failure", file=sys.stderr)
    raise SystemExit(17)
if argv[:2] == ["rm", "-f"] and os.environ.get("FAKE_DOCKER_FAIL_CONTAINER_RM"):
    print("forced container removal failure", file=sys.stderr)
    raise SystemExit(18)
if argv and argv[0] == "build" and os.environ.get("FAKE_DOCKER_MUTATE_CONTEXT"):
    with open(os.path.join(os.environ["FAKE_LIVE_CONTEXT"], "Dockerfile"), "a", encoding="utf-8") as stream:
        stream.write("# concurrent edit\\n")
if argv and argv[0] == "build" and os.environ.get("FAKE_DOCKER_EDIT_RESTORE_LIVE"):
    live = os.path.join(os.environ["FAKE_LIVE_CONTEXT"], "Dockerfile")
    with open(live, encoding="utf-8") as stream:
        original = stream.read()
    with open(live, "w", encoding="utf-8") as stream:
        stream.write(original + "# transient edit\\n")
    with open(live, "w", encoding="utf-8") as stream:
        stream.write(original)
if argv and argv[0] == "build" and os.environ.get("FAKE_BUILD_BARRIER"):
    barrier = os.environ["FAKE_BUILD_BARRIER"]
    with open(barrier + ".ready", "w", encoding="utf-8") as stream:
        stream.write("ready")
    while not os.path.exists(barrier + ".release"):
        time.sleep(0.01)
if argv and argv[0] == "build" and os.environ.get("FAKE_BUILD_STARTED_MARKER"):
    with open(os.environ["FAKE_BUILD_STARTED_MARKER"], "w", encoding="utf-8") as stream:
        stream.write("started")
if argv and argv[0] == "build" and os.environ.get("FAKE_BUILD_SIGNAL_BARRIER"):
    barrier = os.environ["FAKE_BUILD_SIGNAL_BARRIER"]
    with open(barrier + ".pid", "w", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    with open(barrier + ".ready", "w", encoding="utf-8") as stream:
        stream.write("ready")
    while not os.path.exists(barrier + ".release"):
        time.sleep(0.01)
if argv and argv[0] == "build" and os.environ.get("FAKE_TERM_RESISTANT_BARRIER"):
    barrier = os.environ["FAKE_TERM_RESISTANT_BARRIER"]
    def resist_term(_signum, _frame):
        with open(barrier + ".term", "w", encoding="utf-8") as stream:
            stream.write("term")
    signal.signal(signal.SIGTERM, resist_term)
    with open(barrier + ".pid", "w", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    with open(barrier + ".ready", "w", encoding="utf-8") as stream:
        stream.write("ready")
    while not os.path.exists(barrier + ".release"):
        time.sleep(0.01)

state_path = os.environ["FAKE_DOCKER_STATE"]

def daemon_reference(reference):
    aliases = os.environ.get("FAKE_DOCKER_EQUIVALENT_ALIASES", "").split(",")
    if aliases != [""] and reference in aliases:
        return aliases[0]
    return reference

def mutate(callback):
    with open(state_path + ".lock", "a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(state_path, encoding="utf-8") as stream:
                state = json.load(stream)
        except FileNotFoundError:
            state = {"refs": {}, "events": {}}
        result = callback(state)
        temporary = state_path + "." + str(os.getpid())
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(state, stream, sort_keys=True)
        os.replace(temporary, state_path)
        return result

def image_for(state, reference):
    reference = daemon_reference(reference)
    if reference in state["refs"]:
        return state["refs"][reference]
    for image in state["refs"].values():
        if image["Id"] == reference:
            return image
    default = json.loads(os.environ["FAKE_DOCKER_INSPECT"])[0]
    if (
        reference == daemon_reference(os.environ["RUST_IMAGE"])
        and os.environ.get("FAKE_DOCKER_FINAL_MISSING")
    ):
        return None
    if (
        reference == daemon_reference(os.environ["RUST_IMAGE"])
        or reference == default["Id"]
    ):
        return default
    return None

if argv[:2] == ["image", "inspect"]:
    reference = argv[2]
    image = mutate(lambda state: image_for(state, reference))
    if image is None:
        print("No such image", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps([image], sort_keys=True, separators=(",", ":")))
elif argv and argv[0] == "run":
    if os.environ.get("FAKE_RUN_SIGNAL_BARRIER"):
        barrier = os.environ["FAKE_RUN_SIGNAL_BARRIER"]
        with open(barrier + ".pid", "w", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
        with open(barrier + ".ready", "w", encoding="utf-8") as stream:
            stream.write("ready")
        while not os.path.exists(barrier + ".release"):
            time.sleep(0.01)
    if os.environ.get("FAKE_DOCKER_EXEC_PROBE"):
        child_env = os.environ.copy()
        for index, value in enumerate(argv):
            if value == "-e" and index + 1 < len(argv):
                key, setting = argv[index + 1].split("=", 1)
                child_env[key] = setting
        completed = subprocess.run(
            [sys.executable, "-c", argv[-1]],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
            check=False,
        )
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        raise SystemExit(completed.returncode)
    print(os.environ.get("FAKE_DOCKER_PROBE", "{}"))
elif argv and argv[0] == "commit":
    candidate = argv[-1]
    candidate_id = "sha256:" + hashlib.sha256(candidate.encode()).hexdigest()
    labels = {} if os.environ.get("FAKE_DOCKER_LABEL_NOOP") else {
        "org.treddy.esp32tap.recipe-sha256": os.environ["FAKE_EXPECT_RECIPE"],
        "org.treddy.esp32tap.toolchain-json": os.environ["FAKE_EXPECT_ATTESTATION"],
    }
    image = {"Id": candidate_id, "Config": {"Labels": labels}}
    mutate(lambda state: state["refs"].__setitem__(candidate, image))
    if os.environ.get("FAKE_DOCKER_COMMIT_TIMEOUT"):
        time.sleep(30)
    print(candidate_id)
elif argv and argv[0] == "tag":
    source, destination = argv[1:]
    destination = daemon_reference(destination)
    def promote(state):
        image = image_for(state, source)
        if image is None:
            raise SystemExit(1)
        state["refs"][destination] = image
        first = not state["events"].get("tagged")
        state["events"]["tagged"] = True
        return first
    first = mutate(promote)
    if os.environ.get("FAKE_TAG_SIGNAL_BARRIER"):
        barrier = os.environ["FAKE_TAG_SIGNAL_BARRIER"]
        with open(barrier + ".pid", "w", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
        with open(barrier + ".ready", "w", encoding="utf-8") as stream:
            stream.write("ready")
        while not os.path.exists(barrier + ".release"):
            time.sleep(0.01)
    if first and os.environ.get("FAKE_DOCKER_TAG_TIMEOUT"):
        time.sleep(30)
    delay = float(os.environ.get("FAKE_DOCKER_TAG_DELAY", "0"))
    if delay:
        def enter(state):
            if state["events"].get("tag_active"):
                state["events"]["tag_overlap"] = True
            state["events"]["tag_active"] = True
        mutate(enter)
        time.sleep(delay)
        mutate(lambda state: state["events"].__setitem__("tag_active", False))
elif argv[:2] == ["image", "rm"]:
    for reference in argv[2:]:
        if "candidate" in reference and os.environ.get("FAKE_CANDIDATE_RM_SIGNAL_BARRIER"):
            barrier = os.environ["FAKE_CANDIDATE_RM_SIGNAL_BARRIER"]
            with open(barrier + ".pid", "w", encoding="utf-8") as stream:
                stream.write(str(os.getpid()))
            with open(barrier + ".ready", "w", encoding="utf-8") as stream:
                stream.write("ready")
            while not os.path.exists(barrier + ".release"):
                time.sleep(0.01)
        if "candidate" in reference and os.environ.get("FAKE_DOCKER_FAIL_CANDIDATE_RM"):
            print("forced candidate removal failure", file=sys.stderr)
            raise SystemExit(19)
        if "stage" in reference and os.environ.get("FAKE_DOCKER_FAIL_STAGE_RM"):
            print("forced stage removal failure", file=sys.stderr)
            raise SystemExit(20)
        mutate(
            lambda state, ref=daemon_reference(reference): state["refs"].pop(ref, None)
        )
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    probe_tool = fake_bin / "probe-tool"
    probe_tool.write_text(
        """#!/usr/bin/env python3
import os
import subprocess
import sys

name = os.path.basename(sys.argv[0])
attack = os.environ.get("FAKE_PROBE_ATTACK", "")
if name == "git" and attack == "noisy":
    os.write(1, b"x" * (2 * 1024 * 1024))
    raise SystemExit(0)
if name == "git" and attack == "closed-fd-sleeper":
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    print("b" * 40)
    raise SystemExit(0)
values = {
    "git": "b" * 40,
    "rustc": "rustc 1.90.0-dev\\nbinary: rustc\\ncommit-hash: " + "c" * 40,
    "ldproxy": "ldproxy 0.3.4",
}
print(values[name])
""",
        encoding="utf-8",
    )
    probe_tool.chmod(0o755)
    for name in ("git", "rustc", "ldproxy"):
        (fake_bin / name).symlink_to(probe_tool)
    (fake_bin / "esptool.py").write_text(
        'print("esptool.py v4.9.0")\n', encoding="utf-8"
    )
    return fake_bin, log


def run_environment(
    context: Path,
    fake_docker: tuple[Path, Path],
    labels: dict[str, str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    fake_bin, log = fake_docker
    cargo_home = fake_bin.parent / "cargo"
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
    return {
        **os.environ,
        "PATH": f"{cargo_home / 'bin'}:{fake_bin}:{os.environ['PATH']}",
        "CARGO_HOME": str(cargo_home),
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_DOCKER_STATE": str(log.with_name("docker-state.json")),
        "FAKE_DOCKER_INSPECT": canonical(inspect),
        "FAKE_DOCKER_PROBE": canonical(COMMON),
        "FAKE_LIVE_CONTEXT": str(context),
        "FAKE_EXPECT_RECIPE": recipe(context),
        "FAKE_EXPECT_ATTESTATION": canonical(attestation(context)),
        "RUST_IMAGE": IMAGE_TAG,
        "BUILD_IMAGE_DOCKER_TIMEOUT": "5",
        "BUILD_IMAGE_PROBE_COMMAND_TIMEOUT": "1",
        "PYTHONPATH": f"{fake_bin}:{os.environ.get('PYTHONPATH', '')}",
        **(extra_env or {}),
    }


def run(
    context: Path,
    fake_docker: tuple[Path, Path],
    *args: str,
    labels: dict[str, str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = run_environment(context, fake_docker, labels, extra_env)
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


def docker_state(log: Path) -> dict:
    state = log.with_name("docker-state.json")
    if not state.exists():
        return {"refs": {}, "events": {}}
    return json.loads(state.read_text(encoding="utf-8"))


def wait_for(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")


def wait_for_state(log: Path, predicate, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = docker_state(log)
        if predicate(state):
            return state
        time.sleep(0.01)
    raise AssertionError("timed out waiting for fake Docker state")


def second_context(context: Path, destination: Path) -> Path:
    shutil.copytree(context, destination)
    return destination


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_process_gone(pid: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_exists(pid):
            return
        time.sleep(0.01)
    raise AssertionError(f"process {pid} survived cancellation")


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
    candidate_tag = commits[0][-1]
    assert candidate_tag != IMAGE_TAG
    promotions = [call for call in calls if call[0] == "tag"]
    assert len(promotions) == 1
    assert promotions[0][-1] == IMAGE_TAG
    assert promotions[0][-2].startswith("sha256:")
    candidate_inspects = [
        call for call in calls if call[:2] == ["image", "inspect"] and call[2] == candidate_tag
    ]
    assert len(candidate_inspects) == 1
    removals = [call for call in calls if call[:2] == ["image", "rm"]]
    assert any(candidate_tag in call for call in removals)
    build_call = next(call for call in calls if call[0] == "build")
    stage_tag = build_call[build_call.index("--tag") + 1]
    assert any(stage_tag in call for call in removals)
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


def test_build_uses_private_exact_context(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    completed = run(context, fake_docker)
    assert completed.returncode == 0, completed.stderr
    build = next(call for call in docker_calls(fake_docker[1]) if call[0] == "build")
    assert Path(build[-1]).resolve() != context.resolve()
    assert "esp32tap-image-build." in build[-1]


def test_transient_live_edit_and_restore_cannot_enter_private_context(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    before = recipe(context)
    completed = run(
        context,
        fake_docker,
        extra_env={"FAKE_DOCKER_EDIT_RESTORE_LIVE": "1"},
    )
    assert completed.returncode == 0, completed.stderr
    assert recipe(context) == before
    final = docker_state(fake_docker[1])["refs"][IMAGE_TAG]
    assert final["Config"]["Labels"][
        "org.treddy.esp32tap.recipe-sha256"
    ] == before


@pytest.mark.parametrize("attack", ["noisy", "closed-fd-sleeper"])
def test_probe_bounds_output_and_reaps_pipe_holding_descendants(
    context: Path,
    fake_docker: tuple[Path, Path],
    attack: str,
) -> None:
    started = time.monotonic()
    completed = run(
        context,
        fake_docker,
        extra_env={
            "FAKE_DOCKER_EXEC_PROBE": "1",
            "FAKE_PROBE_ATTACK": attack,
            "BUILD_IMAGE_PROBE_COMMAND_TIMEOUT": "1",
        },
    )
    elapsed = time.monotonic() - started
    assert completed.returncode != 0
    assert elapsed < 3
    assert "toolchain probe" in completed.stderr
    assert "exceeds" in completed.stderr or "timed out" in completed.stderr


def test_probe_attests_cargo_metadata_without_invoking_ldproxy(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    completed = run(
        context,
        fake_docker,
        extra_env={"FAKE_DOCKER_EXEC_PROBE": "1"},
    )
    assert completed.returncode == 0, completed.stderr
    final = docker_state(fake_docker[1])["refs"][IMAGE_TAG]
    label = json.loads(
        final["Config"]["Labels"]["org.treddy.esp32tap.toolchain-json"]
    )
    assert label["linker_version"] == COMMON["linker_version"]


@pytest.mark.parametrize(
    ("package", "version"),
    [("not-ldproxy", "0.3.4"), ("ldproxy", "0.3.5")],
)
def test_probe_rejects_mismatched_ldproxy_cargo_metadata(
    context: Path,
    fake_docker: tuple[Path, Path],
    package: str,
    version: str,
) -> None:
    cargo_metadata = fake_docker[0].parent / "cargo" / ".crates2.json"
    cargo_metadata.write_text(
        canonical(cargo_install_metadata(package, version)),
        encoding="utf-8",
    )
    completed = run(
        context,
        fake_docker,
        extra_env={"FAKE_DOCKER_EXEC_PROBE": "1"},
    )
    assert completed.returncode != 0
    assert "ldproxy" in completed.stderr
    assert not any(call[0] == "commit" for call in docker_calls(fake_docker[1]))


def test_probe_rejects_path_spoof_of_cargo_installed_ldproxy(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    fake_bin = fake_docker[0]
    cargo_bin = fake_bin.parent / "cargo" / "bin"
    completed = run(
        context,
        fake_docker,
        extra_env={
            "FAKE_DOCKER_EXEC_PROBE": "1",
            "PATH": f"{fake_bin}:{cargo_bin}:{os.environ['PATH']}",
        },
    )
    assert completed.returncode != 0
    assert "ldproxy" in completed.stderr
    assert not any(call[0] == "commit" for call in docker_calls(fake_docker[1]))


def test_probe_rejects_unverified_ldproxy_version_string_before_commit(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    spoofed = {**COMMON, "linker_version": "ldproxy 0.3.4"}
    completed = run(
        context,
        fake_docker,
        extra_env={"FAKE_DOCKER_PROBE": canonical(spoofed)},
    )
    assert completed.returncode != 0
    assert "ldproxy" in completed.stderr
    assert not any(call[0] == "commit" for call in docker_calls(fake_docker[1]))


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


def test_precommit_probe_and_container_cleanup_failures_preserve_final(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    completed = run(
        context,
        fake_docker,
        extra_env={
            "FAKE_DOCKER_PROBE": "not json",
            "FAKE_DOCKER_FAIL_CONTAINER_RM": "1",
        },
    )
    assert completed.returncode != 0
    assert "cleanup also failed" in completed.stderr
    calls = docker_calls(fake_docker[1])
    assert not any(call[0] == "commit" for call in calls)
    assert not any(call[0] == "tag" and call[-1] == IMAGE_TAG for call in calls)
    assert docker_state(fake_docker[1])["refs"].get(IMAGE_TAG) is None


def test_commit_daemon_success_after_client_timeout_cleans_candidate_and_preserves_final(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    completed = run(
        context,
        fake_docker,
        extra_env={
            "FAKE_DOCKER_COMMIT_TIMEOUT": "1",
            "BUILD_IMAGE_DOCKER_TIMEOUT": "1",
        },
    )
    assert completed.returncode != 0
    state = docker_state(fake_docker[1])
    assert state["refs"].get(IMAGE_TAG, {"Id": IMAGE_ID})["Id"] == IMAGE_ID
    assert not any("candidate" in reference for reference in state["refs"])


def test_commit_that_ignores_labels_never_promotes_candidate(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    completed = run(
        context,
        fake_docker,
        extra_env={"FAKE_DOCKER_LABEL_NOOP": "1"},
    )
    assert completed.returncode != 0
    calls = docker_calls(fake_docker[1])
    assert not any(call[0] == "tag" and call[-1] == IMAGE_TAG for call in calls)
    assert docker_state(fake_docker[1])["refs"].get(IMAGE_TAG) is None


@pytest.mark.parametrize(
    "failure_env",
    ["FAKE_DOCKER_FAIL_CONTAINER_RM", "FAKE_DOCKER_FAIL_STAGE_RM"],
)
def test_required_prepublication_cleanup_failure_preserves_prior_final(
    context: Path,
    fake_docker: tuple[Path, Path],
    failure_env: str,
) -> None:
    completed = run(
        context,
        fake_docker,
        extra_env={failure_env: "1"},
    )
    assert completed.returncode != 0
    assert not any(
        call[0] == "tag" and call[-1] == IMAGE_TAG
        for call in docker_calls(fake_docker[1])
    )
    assert docker_state(fake_docker[1])["refs"].get(IMAGE_TAG) is None


def test_postpublication_candidate_cleanup_failure_is_warning_only(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    completed = run(
        context,
        fake_docker,
        extra_env={"FAKE_DOCKER_FAIL_CANDIDATE_RM": "1"},
    )
    assert completed.returncode == 0, completed.stderr
    assert "best-effort cleanup" in completed.stderr
    assert docker_state(fake_docker[1])["refs"][IMAGE_TAG]["Id"].startswith("sha256:")


def test_ambiguous_promotion_timeout_restores_prior_final_id(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    completed = run(
        context,
        fake_docker,
        extra_env={
            "FAKE_DOCKER_TAG_TIMEOUT": "1",
            "BUILD_IMAGE_DOCKER_TIMEOUT": "1",
        },
    )
    assert completed.returncode != 0
    assert docker_state(fake_docker[1])["refs"][IMAGE_TAG]["Id"] == IMAGE_ID
    promotions = [
        call
        for call in docker_calls(fake_docker[1])
        if call[0] == "tag" and call[-1] == IMAGE_TAG
    ]
    assert len(promotions) >= 2


def test_ambiguous_first_promotion_removes_final_when_no_prior_tag(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    completed = run(
        context,
        fake_docker,
        extra_env={
            "FAKE_DOCKER_FINAL_MISSING": "1",
            "FAKE_DOCKER_TAG_TIMEOUT": "1",
            "BUILD_IMAGE_DOCKER_TIMEOUT": "1",
        },
    )
    assert completed.returncode != 0
    assert IMAGE_TAG not in docker_state(fake_docker[1])["refs"]


def test_suite_never_mutates_global_publication_lock() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    mutators = {
        "chmod",
        "hardlink_to",
        "mkdir",
        "rename",
        "replace",
        "symlink_to",
        "touch",
        "unlink",
        "write_bytes",
        "write_text",
    }
    unsafe_lines = [
        node.lineno
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "PUBLICATION_LOCK"
            and node.func.attr in mutators
        )
    ]
    assert unsafe_lines == []


def test_publication_refuses_preplaced_symlink_lock(
    context: Path, fake_docker: tuple[Path, Path], tmp_path: Path
) -> None:
    def lock_identity(path: Path) -> tuple[int, int, int, int] | None:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink)

    image_tag = f"example/esp32tap:symlink-{tmp_path.name}"
    production_identity = lock_identity(PUBLICATION_LOCK)
    fixture_lock = tmp_path / "fixture-publication.lock"
    fixture_script = context / "tools" / "build_image.sh"
    original = fixture_script.read_text(encoding="utf-8")
    lock_declaration = (
        'publication_lock_path = Path("/tmp/esp32tap-image-publication.lock")'
    )
    assert original.count(lock_declaration) == 1
    fixture_script.write_text(
        original.replace(
            lock_declaration,
            f"publication_lock_path = Path({str(fixture_lock)!r})",
        ),
        encoding="utf-8",
    )
    target = tmp_path / "attacker-target"
    target.write_text("", encoding="utf-8")
    fixture_lock.symlink_to(target)
    try:
        completed = run(
            context,
            fake_docker,
            extra_env={"RUST_IMAGE": image_tag},
        )
        assert completed.returncode != 0
        assert not any(
            call[0] == "tag" and call[-1] == image_tag
            for call in docker_calls(fake_docker[1])
        )
    finally:
        fixture_lock.unlink(missing_ok=True)
        assert PUBLICATION_LOCK == Path("/tmp/esp32tap-image-publication.lock")
        assert lock_identity(PUBLICATION_LOCK) == production_identity


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
    assert not any(call[0] == "tag" and call[-1] == IMAGE_TAG for call in calls)
    assert not any("candidate" in reference for reference in docker_state(fake_docker[1])["refs"])


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


def test_image_lock_orders_complete_cross_worktree_build_lifecycles(
    context: Path, fake_docker: tuple[Path, Path], tmp_path: Path
) -> None:
    newer = second_context(context, tmp_path / "newer-lifecycle")
    (newer / "Dockerfile").write_text(
        "FROM scratch\n# newer serialized recipe\n", encoding="utf-8"
    )
    barrier = tmp_path / "old-build"
    newer_started = tmp_path / "newer-build.started"
    old_env = run_environment(
        context,
        fake_docker,
        extra_env={"FAKE_BUILD_BARRIER": str(barrier)},
    )
    old = subprocess.Popen(
        [str(context / "tools" / "build_image.sh")],
        cwd=context,
        env=old_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    newer_process: subprocess.Popen[str] | None = None
    try:
        wait_for(Path(str(barrier) + ".ready"))
        newer_process = subprocess.Popen(
            [str(newer / "tools" / "build_image.sh")],
            cwd=newer,
            env=run_environment(
                newer,
                fake_docker,
                extra_env={"FAKE_BUILD_STARTED_MARKER": str(newer_started)},
            ),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.2)
        assert not newer_started.exists()
        Path(str(barrier) + ".release").write_text("go", encoding="utf-8")
        old_result = old.communicate(timeout=8)
        assert old.returncode == 0, old_result
        wait_for(newer_started)
        newer_result = newer_process.communicate(timeout=8)
        assert newer_process.returncode == 0, newer_result
        final = docker_state(fake_docker[1])["refs"][IMAGE_TAG]
        assert final["Config"]["Labels"][
            "org.treddy.esp32tap.recipe-sha256"
        ] == recipe(newer)
    finally:
        Path(str(barrier) + ".release").write_text("cleanup", encoding="utf-8")
        old.kill()
        old.wait()
        if newer_process is not None:
            newer_process.kill()
            newer_process.wait()


def test_publication_lock_serializes_same_image_across_worktrees(
    context: Path, fake_docker: tuple[Path, Path], tmp_path: Path
) -> None:
    other = second_context(context, tmp_path / "other-worktree")
    environments = [
        run_environment(
            item,
            fake_docker,
            extra_env={"FAKE_DOCKER_TAG_DELAY": "0.3"},
        )
        for item in (context, other)
    ]
    processes = [
        subprocess.Popen(
            [str(item / "tools" / "build_image.sh")],
            cwd=item,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for item, environment in zip((context, other), environments, strict=True)
    ]
    results = [process.communicate(timeout=10) for process in processes]
    assert [process.returncode for process in processes] == [0, 0], results
    state = docker_state(fake_docker[1])
    assert not state["events"].get("tag_overlap", False)


def test_ambiguous_older_rollback_cannot_clobber_newer_worktree_publication(
    context: Path, fake_docker: tuple[Path, Path], tmp_path: Path
) -> None:
    newer = second_context(context, tmp_path / "newer-worktree")
    (newer / "Dockerfile").write_text(
        "FROM scratch\n# newer cross-worktree recipe\n", encoding="utf-8"
    )
    old_env = run_environment(
        context,
        fake_docker,
        extra_env={
            "FAKE_DOCKER_TAG_TIMEOUT": "1",
            "BUILD_IMAGE_DOCKER_TIMEOUT": "1",
        },
    )
    old = subprocess.Popen(
        [str(context / "tools" / "build_image.sh")],
        cwd=context,
        env=old_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        wait_for_state(
            fake_docker[1],
            lambda state: state["events"].get("tagged", False),
        )
        new_result = run(newer, fake_docker)
        assert new_result.returncode == 0, new_result.stderr
        old.communicate(timeout=8)
        assert old.returncode != 0
        final = docker_state(fake_docker[1])["refs"][IMAGE_TAG]
        assert final["Config"]["Labels"][
            "org.treddy.esp32tap.recipe-sha256"
        ] == recipe(newer)
    finally:
        old.kill()
        old.wait()


def test_equivalent_aliases_share_lifecycle_lock_and_cannot_clobber_publication(
    context: Path, fake_docker: tuple[Path, Path], tmp_path: Path
) -> None:
    short = "esp32tap-rust:build"
    qualified = "docker.io/library/esp32tap-rust:build"
    aliases = f"{short},{qualified}"
    newer = second_context(context, tmp_path / "newer-alias-worktree")
    (newer / "Dockerfile").write_text(
        "FROM scratch\n# newer alias recipe\n", encoding="utf-8"
    )
    newer_started = tmp_path / "newer-alias-build.started"
    old = subprocess.Popen(
        [str(context / "tools" / "build_image.sh")],
        cwd=context,
        env=run_environment(
            context,
            fake_docker,
            extra_env={
                "RUST_IMAGE": short,
                "FAKE_DOCKER_EQUIVALENT_ALIASES": aliases,
                "FAKE_DOCKER_TAG_TIMEOUT": "1",
                "BUILD_IMAGE_DOCKER_TIMEOUT": "1",
            },
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    new_process: subprocess.Popen[str] | None = None
    try:
        wait_for_state(
            fake_docker[1],
            lambda state: state["events"].get("tagged", False),
        )
        new_process = subprocess.Popen(
            [str(newer / "tools" / "build_image.sh")],
            cwd=newer,
            env=run_environment(
                newer,
                fake_docker,
                extra_env={
                    "RUST_IMAGE": qualified,
                    "FAKE_DOCKER_EQUIVALENT_ALIASES": aliases,
                    "FAKE_BUILD_STARTED_MARKER": str(newer_started),
                },
            ),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        wait_for(newer_started)
        lifecycle_overlap = old.poll() is None
        old_result = old.communicate(timeout=8)
        new_result = new_process.communicate(timeout=8)
        assert old.returncode != 0, old_result
        assert new_process.returncode == 0, new_result
        assert not lifecycle_overlap

        state = docker_state(fake_docker[1])
        final = state["refs"][short]
        assert final["Config"]["Labels"][
            "org.treddy.esp32tap.recipe-sha256"
        ] == recipe(newer)
    finally:
        old.kill()
        old.wait()
        if new_process is not None:
            new_process.kill()
            new_process.wait()


@pytest.mark.parametrize(
    ("barrier_env", "expects_container_cleanup", "cancellation_signal"),
    [
        ("FAKE_BUILD_SIGNAL_BARRIER", False, signal.SIGHUP),
        ("FAKE_BUILD_SIGNAL_BARRIER", False, signal.SIGINT),
        ("FAKE_BUILD_SIGNAL_BARRIER", False, signal.SIGTERM),
        ("FAKE_RUN_SIGNAL_BARRIER", True, signal.SIGTERM),
    ],
)
def test_termination_during_build_or_probe_reaps_child_and_preserves_final(
    context: Path,
    fake_docker: tuple[Path, Path],
    tmp_path: Path,
    barrier_env: str,
    expects_container_cleanup: bool,
    cancellation_signal: signal.Signals,
) -> None:
    barrier = tmp_path / barrier_env.lower()
    process = subprocess.Popen(
        [str(context / "tools" / "build_image.sh")],
        cwd=context,
        env=run_environment(
            context,
            fake_docker,
            extra_env={barrier_env: str(barrier)},
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    child_pid = -1
    try:
        wait_for(Path(str(barrier) + ".ready"))
        child_pid = int(Path(str(barrier) + ".pid").read_text(encoding="utf-8"))
        os.kill(process.pid, cancellation_signal)
        time.sleep(0.05)
        Path(str(barrier) + ".release").write_text("cleanup", encoding="utf-8")
        result = process.communicate(timeout=8)
        assert process.returncode == 128 + cancellation_signal, result
        wait_process_gone(child_pid)
        calls = docker_calls(fake_docker[1])
        build_call = next(call for call in calls if call[0] == "build")
        assert not Path(build_call[-1]).exists()
        assert any(call[:2] == ["image", "rm"] for call in calls)
        assert any(call[:2] == ["rm", "-f"] for call in calls) is expects_container_cleanup
        assert not any(call[0] == "commit" for call in calls)
        assert not any(call[0] == "tag" and call[-1] == IMAGE_TAG for call in calls)
        assert docker_state(fake_docker[1])["refs"].get(IMAGE_TAG) is None
        followup = run(context, fake_docker)
        assert followup.returncode == 0, followup.stderr
    finally:
        Path(str(barrier) + ".release").write_text("cleanup", encoding="utf-8")
        process.kill()
        process.wait()
        if child_pid > 0:
            wait_process_gone(child_pid)


def test_duplicate_termination_during_reaping_cannot_strand_resistant_child(
    context: Path, fake_docker: tuple[Path, Path], tmp_path: Path
) -> None:
    barrier = tmp_path / "term-resistant-child"
    process = subprocess.Popen(
        [str(context / "tools" / "build_image.sh")],
        cwd=context,
        env=run_environment(
            context,
            fake_docker,
            extra_env={"FAKE_TERM_RESISTANT_BARRIER": str(barrier)},
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    child_pid = -1
    try:
        wait_for(Path(str(barrier) + ".ready"))
        child_pid = int(Path(str(barrier) + ".pid").read_text(encoding="utf-8"))
        os.kill(process.pid, signal.SIGTERM)
        wait_for(Path(str(barrier) + ".term"))
        os.kill(process.pid, signal.SIGTERM)
        os.kill(process.pid, signal.SIGTERM)
        result = process.communicate(timeout=8)
        assert process.returncode == 128 + signal.SIGTERM, result
        wait_process_gone(child_pid)

        calls = docker_calls(fake_docker[1])
        build_call = next(call for call in calls if call[0] == "build")
        assert not Path(build_call[-1]).exists()
        assert any(call[:2] == ["image", "rm"] for call in calls)
        assert not any(call[0] == "commit" for call in calls)
        assert docker_state(fake_docker[1])["refs"].get(IMAGE_TAG) is None
    finally:
        Path(str(barrier) + ".release").write_text("cleanup", encoding="utf-8")
        process.kill()
        process.wait()
        if child_pid > 0:
            wait_process_gone(child_pid)


def test_termination_is_deferred_across_verified_promotion_then_releases_lock(
    context: Path, fake_docker: tuple[Path, Path], tmp_path: Path
) -> None:
    barrier = tmp_path / "tag-signal"
    process = subprocess.Popen(
        [str(context / "tools" / "build_image.sh")],
        cwd=context,
        env=run_environment(
            context,
            fake_docker,
            extra_env={"FAKE_TAG_SIGNAL_BARRIER": str(barrier)},
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    child_pid = -1
    try:
        wait_for(Path(str(barrier) + ".ready"))
        child_pid = int(Path(str(barrier) + ".pid").read_text(encoding="utf-8"))
        os.kill(process.pid, signal.SIGTERM)
        time.sleep(0.1)
        assert process.poll() is None
        Path(str(barrier) + ".release").write_text("finish", encoding="utf-8")
        result = process.communicate(timeout=8)
        assert process.returncode == 128 + signal.SIGTERM, result
        wait_process_gone(child_pid)
        final = docker_state(fake_docker[1])["refs"][IMAGE_TAG]
        assert final["Config"]["Labels"][
            "org.treddy.esp32tap.recipe-sha256"
        ] == recipe(context)
        followup = run(context, fake_docker)
        assert followup.returncode == 0, followup.stderr
    finally:
        Path(str(barrier) + ".release").write_text("cleanup", encoding="utf-8")
        process.kill()
        process.wait()
        if child_pid > 0:
            wait_process_gone(child_pid)


@pytest.mark.parametrize("cleanup_fails", [False, True], ids=["clean", "warning"])
def test_termination_during_cleanup_finishes_cleanup_then_releases_lock(
    context: Path,
    fake_docker: tuple[Path, Path],
    tmp_path: Path,
    cleanup_fails: bool,
) -> None:
    barrier = tmp_path / "candidate-cleanup-signal"
    process = subprocess.Popen(
        [str(context / "tools" / "build_image.sh")],
        cwd=context,
        env=run_environment(
            context,
            fake_docker,
            extra_env={
                "FAKE_CANDIDATE_RM_SIGNAL_BARRIER": str(barrier),
                **(
                    {"FAKE_DOCKER_FAIL_CANDIDATE_RM": "1"}
                    if cleanup_fails
                    else {}
                ),
            },
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    child_pid = -1
    try:
        wait_for(Path(str(barrier) + ".ready"))
        child_pid = int(Path(str(barrier) + ".pid").read_text(encoding="utf-8"))
        os.kill(process.pid, signal.SIGTERM)
        time.sleep(0.05)
        os.kill(process.pid, signal.SIGHUP)
        os.kill(process.pid, signal.SIGTERM)
        time.sleep(0.1)
        assert process.poll() is None
        Path(str(barrier) + ".release").write_text("finish", encoding="utf-8")
        result = process.communicate(timeout=8)
        assert process.returncode == 128 + signal.SIGTERM, result
        assert ("best-effort cleanup warning" in result[1]) is cleanup_fails
        wait_process_gone(child_pid)

        calls = docker_calls(fake_docker[1])
        build_call = next(call for call in calls if call[0] == "build")
        assert not Path(build_call[-1]).exists()
        assert any(call[:2] == ["rm", "-f"] for call in calls)
        assert any(
            call[:2] == ["image", "rm"] and any("stage" in item for item in call)
            for call in calls
        )
        candidate_remains = any(
            "candidate" in reference
            for reference in docker_state(fake_docker[1])["refs"]
        )
        assert candidate_remains is cleanup_fails
        final = docker_state(fake_docker[1])["refs"][IMAGE_TAG]
        assert final["Config"]["Labels"][
            "org.treddy.esp32tap.recipe-sha256"
        ] == recipe(context)

        followup = run(context, fake_docker)
        assert followup.returncode == 0, followup.stderr
    finally:
        Path(str(barrier) + ".release").write_text("cleanup", encoding="utf-8")
        process.kill()
        process.wait()
        if child_pid > 0:
            wait_process_gone(child_pid)


def test_termination_after_cleanup_and_lock_release_interrupts_final_output(
    context: Path, fake_docker: tuple[Path, Path]
) -> None:
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    filled = 0
    try:
        while True:
            try:
                filled += os.write(write_fd, b"x" * 64 * 1024)
            except BlockingIOError:
                break
        assert filled > 0
        os.set_blocking(write_fd, True)

        process = subprocess.Popen(
            [str(context / "tools" / "build_image.sh")],
            cwd=context,
            env=run_environment(
                context,
                fake_docker,
                extra_env={"PYTHONUNBUFFERED": "1"},
            ),
            text=True,
            stdout=write_fd,
            stderr=subprocess.PIPE,
        )
    finally:
        os.close(write_fd)

    lock_fd = -1
    try:
        wait_for_state(
            fake_docker[1],
            lambda state: (
                IMAGE_TAG in state["refs"]
                and not any("candidate" in ref for ref in state["refs"])
            ),
        )
        lock_fd = os.open(PUBLICATION_LOCK, os.O_RDWR)
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise AssertionError("publication lock remained held after cleanup")
                time.sleep(0.01)

        os.kill(process.pid, signal.SIGTERM)
        remaining = filled
        while remaining:
            block = os.read(read_fd, min(remaining, 64 * 1024))
            assert block
            remaining -= len(block)
        result = process.communicate(timeout=8)
        assert process.returncode == 128 + signal.SIGTERM, result

        calls = docker_calls(fake_docker[1])
        build_call = next(call for call in calls if call[0] == "build")
        assert not Path(build_call[-1]).exists()
        assert any(call[:2] == ["rm", "-f"] for call in calls)
        state = docker_state(fake_docker[1])
        assert not any(
            "candidate" in ref or "stage" in ref for ref in state["refs"]
        )
        assert state["refs"][IMAGE_TAG]["Config"]["Labels"][
            "org.treddy.esp32tap.recipe-sha256"
        ] == recipe(context)
    finally:
        if lock_fd >= 0:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        process.kill()
        process.wait()
        os.close(read_fd)


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
    assert "`ldproxy` has no version-reporting CLI mode" in text
    assert "`$CARGO_HOME/.crates2.json`" in text
    assert "SHA-256 of `$CARGO_HOME/bin/ldproxy` without executing it" in prose
