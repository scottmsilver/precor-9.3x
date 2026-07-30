#!/usr/bin/env python3
"""Fast host-only contract tests for the immutable snapshot build driver."""

from __future__ import annotations

import hashlib
import json
import fcntl
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parent
SCRIPT = TOOLS / "build.sh"
MEMBERS = (
    "esp32tap.bin",
    "bootloader.bin",
    "partition-table.bin",
    "flash_args",
    "sdkconfig",
)


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_rejects_unknown_kind_before_docker() -> None:
    completed = subprocess.run(
        ["bash", str(SCRIPT)],
        env={"PATH": "/usr/bin:/bin", "ONLY": "debug"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 2
    assert "ONLY must be prod, qemu, or both" in completed.stderr


def test_exact_snapshot_gate_build_publish_order_is_explicit() -> None:
    text = source().split("def main() -> None:", 1)[1]
    operations = [
        "create_snapshot(",
        "_current_toolchain(",
        "verify_gate_input_completeness(",
        "run_docker(",
        "make_manifest(",
        "live_digest_without_staging_sdkconfigs(",
        "publish_generation_atomic(",
    ]
    positions = [text.index(operation) for operation in operations]
    assert positions == sorted(positions)


def test_container_uses_only_snapshot_source_and_stable_mounts() -> None:
    text = source()
    assert 'f"{snapshot_root}:/project:ro"' in text
    assert 'f"{target}:/target/{cache_kind}"' in text
    assert '"CARGO_TARGET_DIR=/target/{cache_kind}"' in text
    assert 'f"{staging}:/output"' in text
    assert '"--user", f"{os.getuid()}:{os.getgid()}"' in text
    assert f"{'{'}repo_root{'}'}:/project" not in text


def test_release_kinds_and_exact_members_are_fixed() -> None:
    text = source()
    assert '"PROFILE=release"' in text
    assert '("qemu-test", "net", "ble")' in text
    assert "BUNDLE_MEMBERS" in text
    assert "ONLY=both" in text


def test_physical_worktree_keys_lock_targets_and_cache() -> None:
    text = source()
    assert "lock_path(repo_root, \"production\")" in text
    assert "target_cache(repo_root, cache_kind)" in text
    assert 'f"esp32tap-cargo-{snapshot.worktree_key}"' in text
    assert "os.dup2(lock_fd, 9, inheritable=True)" in text
    assert "lock_fd=9" in text


def test_failure_cleanup_and_live_digest_guard_are_present() -> None:
    text = source()
    assert "snapshot.digest != live_digest_without_staging_sdkconfigs(" in text
    assert "return working_digest(repo_root)" in text
    assert "source inputs changed while the snapshot build was running" in text
    assert "remove_snapshot(snapshot.root, task_root)" in text
    assert "cleanup_staging(staging)" in text
    assert "finally:" in text


def _write(path: Path, text: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)


@pytest.fixture
def fake_worktree(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    root = tmp_path / "repo"
    rs = root / "hardware" / "Esp32Tap" / "firmware" / "esp32_rs"
    tools = rs / "tools"
    tools.mkdir(parents=True)
    shutil.copy2(SCRIPT, tools / "build.sh")
    for name in ("artifact_inputs.py", "artifact_provenance.py"):
        shutil.copy2(TOOLS / name, tools / name)
    event_log = tmp_path / "events.jsonl"
    gate_failure = tmp_path / "gate-failure"
    for gate in (
        "check_unsafe_budget.py",
        "check_case_parity.py",
        "check_pins.py",
        "check_wdt_chain.py",
    ):
        _write(
            tools / gate,
            (
                "import json\n"
                "from pathlib import Path\n"
                f"log = Path({str(event_log)!r})\n"
                "with log.open('a', encoding='utf-8') as stream:\n"
                f"    stream.write(json.dumps({{'event':'gate','name':{gate!r},"
                "'path':__file__}) + '\\n')\n"
                f"if Path({str(gate_failure)!r}).exists():\n"
                "    raise SystemExit(19)\n"
            ),
        )
    toolchain_common = {
        "image_id": "sha256:" + "a" * 64,
        "recipe_sha256": "b" * 64,
        "image_tag": "esp32tap-rust:test",
        "idf_commit": "c" * 40,
        "rustc_verbose": "rustc 1.90.0-dev",
        "target": "xtensa-esp32s3-espidf",
        "linker_version": "ldproxy 0.3.4",
        "esptool_version": "esptool.py v4.9.0",
        "component_lock_sha256": "d" * 64,
        "profile": "release",
    }
    _write(
        tools / "build_image.sh",
        (
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            f"log = Path({str(event_log)!r})\n"
            "kind = sys.argv[-1]\n"
            "with log.open('a', encoding='utf-8') as stream:\n"
            "    stream.write(json.dumps({'event':'check','kind':kind,"
            "'path':__file__}) + '\\n')\n"
            f"value = {toolchain_common!r}\n"
            "value['features'] = [] if kind == 'production' else "
            "['ble','net','qemu-test']\n"
            "print(json.dumps(value, sort_keys=True, separators=(',',':')))\n"
        ),
        executable=True,
    )
    _write(rs / "Dockerfile", "FROM scratch\n")
    _write(rs / ".dockerignore", "*\n")
    _write(rs / "esp32tap" / "components_esp32s3.lock", "lock\n")
    source_file = rs / "esp32tap" / "src" / "lib.rs"
    _write(source_file, "A\n")

    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git", "-C", str(root),
            "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid",
            "commit", "-qm", "fixture",
        ],
        check=True,
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.jsonl"
    _write(
        fake_bin / "docker",
        """#!/usr/bin/env python3
import json, os, signal, sys, time
from pathlib import Path
argv = sys.argv[1:]
with Path(os.environ["FAKE_DOCKER_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(argv) + "\\n")
if os.environ.get("FAKE_DOCKER_BARRIER"):
    barrier = Path(os.environ["FAKE_DOCKER_BARRIER"])
    if os.environ.get("FAKE_DOCKER_RESIST_TERM"):
        signal.signal(signal.SIGTERM, lambda _signum, _frame: None)
    barrier.with_suffix(".ready").write_text("ready", encoding="utf-8")
    while not barrier.with_suffix(".release").exists():
        time.sleep(0.01)
if os.environ.get("FAKE_DOCKER_MUTATE"):
    path = Path(os.environ["FAKE_DOCKER_MUTATE"])
    current = path.read_text(encoding="utf-8")
    path.write_text(("B" if current[0] != "B" else "A") + current[1:], encoding="utf-8")
if os.environ.get("FAKE_DOCKER_FAIL"):
    raise SystemExit(17)
mounts = [argv[index + 1] for index, value in enumerate(argv) if value == "-v"]
output = Path(next(value.split(":", 1)[0] for value in mounts if value.endswith(":/output")))
for name in ("esp32tap.bin", "bootloader.bin", "partition-table.bin", "sdkconfig"):
    output.joinpath(name).write_bytes((name + "\\n").encode())
output.joinpath("flash_args").write_text("0x0 bootloader.bin\\n", encoding="utf-8")
""",
        executable=True,
    )
    yield root, event_log, docker_log, gate_failure

    key = hashlib.sha256(os.fsencode(str(root.resolve()))).hexdigest()[:12]
    shutil.rmtree(Path("/tmp") / f"esp32tap-target-{key}", ignore_errors=True)
    shutil.rmtree(Path("/tmp") / f"esp32tap-cargo-{key}", ignore_errors=True)


def _run(
    fixture: tuple[Path, Path, Path, Path],
    *,
    only: str = "prod",
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    root, _, docker_log, _ = fixture
    env = os.environ.copy()
    env.update(
        PATH=f"{docker_log.parent / 'fake-bin'}:{env['PATH']}",
        FAKE_DOCKER_LOG=str(docker_log),
        ONLY=only,
    )
    env.pop("PROFILE", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [
            "bash",
            str(
                root
                / "hardware"
                / "Esp32Tap"
                / "firmware"
                / "esp32_rs"
                / "tools"
                / "build.sh"
            ),
        ],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _events(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_fake_build_uses_snapshot_order_mounts_uid_and_manifest(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, event_log, docker_log, _ = fake_worktree
    completed = _run(fake_worktree)
    assert completed.returncode == 0, completed.stderr
    events = _events(event_log)
    assert [event["event"] for event in events] == ["check"] + ["gate"] * 4
    assert all("/tmp/esp32tap-snapshot-build." in event["path"] for event in events)
    docker = _events(docker_log)[0]
    assert docker[:2] == ["run", "--rm"]
    assert docker[docker.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert docker[docker.index("--entrypoint") + 1] == "bash"
    image_index = docker.index("sha256:" + "a" * 64)
    assert docker[image_index + 1] == "-lc"
    assert (
        "CARGO_WORKSPACE_DIR=/project/hardware/Esp32Tap/firmware/"
        "esp32_rs/esp32tap"
        in docker
    )
    mounts = [docker[index + 1] for index, value in enumerate(docker) if value == "-v"]
    assert any(
        value.startswith("/tmp/esp32tap-snapshot-build.")
        and value.endswith(":/project:ro")
        for value in mounts
    )
    assert not any(value.startswith(f"{root}:/project") for value in mounts)
    assert any(value.endswith(":/target/prod") for value in mounts)
    rs = root / "hardware/Esp32Tap/firmware/esp32_rs"
    assert (rs / "build").is_symlink()
    manifest = json.loads((rs / "build" / "artifact-manifest.json").read_text())
    assert manifest["kind"] == "production"
    assert manifest["toolchain"]["image_id"] == "sha256:" + "a" * 64
    assert set(manifest["toolchain"]) == {
        "image_id",
        "recipe_sha256",
        "image_tag",
        "idf_commit",
        "rustc_verbose",
        "target",
        "linker_version",
        "esptool_version",
        "component_lock_sha256",
        "profile",
        "features",
    }
    assert tuple(member["name"] for member in manifest["members"]) == MEMBERS
    assert not list(rs.glob(".snapshot-build-*"))


def test_same_size_edit_rebuilds_and_live_mutation_preserves_old(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, _, docker_log, _ = fake_worktree
    assert _run(fake_worktree).returncode == 0
    rs = root / "hardware/Esp32Tap/firmware/esp32_rs"
    first = os.readlink(rs / "build")
    live = rs / "esp32tap/src/lib.rs"
    live.write_text("B\n", encoding="utf-8")
    assert _run(fake_worktree).returncode == 0
    second = os.readlink(rs / "build")
    assert second != first
    completed = _run(
        fake_worktree,
        extra_env={"FAKE_DOCKER_MUTATE": str(live)},
    )
    assert completed.returncode != 0
    assert "source inputs changed" in completed.stderr
    assert os.readlink(rs / "build") == second
    assert len(_events(docker_log)) == 3


def test_gate_and_container_failure_preserve_generation_and_both_publish(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, _, _, gate_failure = fake_worktree
    assert _run(fake_worktree).returncode == 0
    rs = root / "hardware/Esp32Tap/firmware/esp32_rs"
    first = os.readlink(rs / "build")
    gate_failure.touch()
    assert _run(fake_worktree).returncode != 0
    assert os.readlink(rs / "build") == first
    gate_failure.unlink()
    assert _run(fake_worktree, extra_env={"FAKE_DOCKER_FAIL": "1"}).returncode != 0
    assert os.readlink(rs / "build") == first
    assert _run(fake_worktree, only="both").returncode == 0
    assert (rs / "build").is_symlink()
    assert (rs / "build_qemu_test").is_symlink()
    qemu_manifest = json.loads(
        (rs / "build_qemu_test" / "artifact-manifest.json").read_text()
    )
    assert qemu_manifest["toolchain"]["features"] == ["ble", "net", "qemu-test"]


def test_public_link_is_never_absent_while_new_build_waits(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, _, _, _ = fake_worktree
    assert _run(fake_worktree).returncode == 0
    rs = root / "hardware/Esp32Tap/firmware/esp32_rs"
    public = rs / "build"
    old = os.readlink(public)
    (rs / "esp32tap/src/lib.rs").write_text("B\n", encoding="utf-8")
    barrier = root.parent / "barrier"
    env = os.environ.copy()
    fake_bin = root.parent / "fake-bin"
    env.update(
        PATH=f"{fake_bin}:{env['PATH']}",
        FAKE_DOCKER_LOG=str(root.parent / "docker.jsonl"),
        FAKE_DOCKER_BARRIER=str(barrier),
        ONLY="prod",
    )
    env.pop("PROFILE", None)
    process = subprocess.Popen(
        ["bash", str(rs / "tools/build.sh")],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready = barrier.with_suffix(".ready")
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    observed = {os.readlink(public) for _ in range(100)}
    assert observed == {old}
    barrier.with_suffix(".release").touch()
    stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == 0, (stdout, stderr)
    new = os.readlink(public)
    assert new != old
    assert public.exists()


def test_independent_physical_worktrees_use_distinct_targets_and_locks(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, event_log, docker_log, gate_failure = fake_worktree
    second = root.parent / "second-repo"
    subprocess.run(["git", "clone", "-q", str(root), str(second)], check=True)
    second_fixture = (second, event_log, docker_log, gate_failure)
    try:
        assert _run(fake_worktree).returncode == 0
        assert _run(second_fixture).returncode == 0
        invocations = _events(docker_log)
        targets = []
        for invocation in invocations:
            mounts = [
                invocation[index + 1]
                for index, value in enumerate(invocation)
                if value == "-v"
            ]
            targets.append(
                next(value for value in mounts if value.endswith(":/target/prod"))
            )
        assert len(set(targets)) == 2
        rs_paths = [
            item / "hardware/Esp32Tap/firmware/esp32_rs"
            for item in (root, second)
        ]
        locks = [
            Path("/tmp")
            / (
                "esp32tap-build-"
                + hashlib.md5(
                    str(path.resolve()).encode(), usedforsecurity=False
                ).hexdigest()[:12]
                + ".lock"
            )
            for path in rs_paths
        ]
        assert locks[0] != locks[1]
        assert all(path.is_file() for path in locks)
    finally:
        key = hashlib.sha256(os.fsencode(str(second.resolve()))).hexdigest()[:12]
        shutil.rmtree(Path("/tmp") / f"esp32tap-target-{key}", ignore_errors=True)
        shutil.rmtree(Path("/tmp") / f"esp32tap-cargo-{key}", ignore_errors=True)


def test_predictable_lock_and_cache_symlinks_are_rejected(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, _, _, _ = fake_worktree
    victim = root.parent / "victim"
    victim.write_text("unchanged", encoding="utf-8")
    key = hashlib.sha256(os.fsencode(str(root.resolve()))).hexdigest()[:12]
    cache_root = Path("/tmp") / f"esp32tap-target-{key}"
    cache_root.symlink_to(victim)
    try:
        completed = _run(fake_worktree)
        assert completed.returncode != 0
        assert "cache path must be an owned physical directory" in completed.stderr
        assert victim.read_text(encoding="utf-8") == "unchanged"
    finally:
        cache_root.unlink(missing_ok=True)

    rs = root / "hardware/Esp32Tap/firmware/esp32_rs"
    lock = Path("/tmp") / (
        "esp32tap-build-"
        + hashlib.md5(
            str(rs.resolve()).encode(), usedforsecurity=False
        ).hexdigest()[:12]
        + ".lock"
    )
    lock.unlink(missing_ok=True)
    lock.symlink_to(victim)
    try:
        completed = _run(fake_worktree)
        assert completed.returncode != 0
        assert "cannot open build lock" in completed.stderr
        assert victim.read_text(encoding="utf-8") == "unchanged"
    finally:
        lock.unlink(missing_ok=True)


def test_term_cancels_child_cleans_resources_then_releases_lock(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, _, docker_log, _ = fake_worktree
    rs = root / "hardware/Esp32Tap/firmware/esp32_rs"
    barrier = root.parent / "cancel-barrier"
    env = os.environ.copy()
    env.update(
        PATH=f"{root.parent / 'fake-bin'}:{env['PATH']}",
        FAKE_DOCKER_LOG=str(docker_log),
        FAKE_DOCKER_BARRIER=str(barrier),
        FAKE_DOCKER_RESIST_TERM="1",
        ONLY="prod",
    )
    env.pop("PROFILE", None)
    process = subprocess.Popen(
        ["bash", str(rs / "tools/build.sh")],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready = barrier.with_suffix(".ready")
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    process.send_signal(signal.SIGTERM)
    process.send_signal(signal.SIGTERM)
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 128 + signal.SIGTERM, (stdout, stderr)
    assert not list(rs.glob(".snapshot-build-*"))
    snapshot_path = Path(_events(root.parent / "events.jsonl")[0]["path"])
    task_root = next(
        parent
        for parent in snapshot_path.parents
        if parent.name.startswith("esp32tap-snapshot-build.")
    )
    assert not task_root.exists()
    key = hashlib.sha256(os.fsencode(str(root.resolve()))).hexdigest()[:12]
    assert (Path("/tmp") / f"esp32tap-target-{key}").is_dir()

    lock = Path("/tmp") / (
        "esp32tap-build-"
        + hashlib.md5(
            str(rs.resolve()).encode(), usedforsecurity=False
        ).hexdigest()[:12]
        + ".lock"
    )
    descriptor = os.open(lock, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(descriptor)
