"""Regression tests for mandatory provenance at every artifact/QEMU entry."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import os
import signal
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent
ESP32_RS = TOOLS.parent
REPO_ROOT = ESP32_RS.parents[3]
IMPLEMENTATION_BASE = "edff022"
MEMBERS = (
    "esp32tap.bin",
    "bootloader.bin",
    "partition-table.bin",
    "flash_args",
    "sdkconfig",
)

sys.path.insert(0, str(TOOLS))
import artifact_provenance as provenance  # noqa: E402


def _load(name: str, path: Path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def legacy_qemu_bundle(tmp_path: Path) -> Path:
    """Materialize the five tracked pre-migration blobs, never live output."""
    bundle = tmp_path / "build_qemu_test"
    bundle.mkdir()
    prefix = "hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test"
    for member in MEMBERS:
        data = subprocess.run(
            ["git", "show", f"{IMPLEMENTATION_BASE}:{prefix}/{member}"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        ).stdout
        (bundle / member).write_bytes(data)
    assert {path.name for path in bundle.iterdir()} == set(MEMBERS)
    assert not (bundle / provenance.MANIFEST_NAME).exists()
    return bundle


def test_pre_migration_fixture_is_rejected_not_skipped(legacy_qemu_bundle: Path):
    toolchain = provenance.Toolchain(
        image_id="sha256:" + "1" * 64,
        recipe_sha256="2" * 64,
        image_tag="esp32tap-rust:test",
        idf_commit="3" * 40,
        rustc_verbose="rustc 1.0",
        target="xtensa-esp32s3-none-elf",
        linker_version="ld 1.0",
        esptool_version="esptool 1.0",
        component_lock_sha256="4" * 64,
        profile="qemu",
        features=("qemu-test",),
    )
    result = provenance.verify_locked(legacy_qemu_bundle, toolchain, "5" * 64)
    assert not result.ok
    assert result.code in {
        provenance.EXIT_MISSING,
        provenance.EXIT_INVALID,
    }


def test_harness_fails_stale_before_docker_and_keeps_both_leases(monkeypatch):
    module = _load(
        "task7_harness_conftest",
        TOOLS / "qemu_harness" / "conftest.py",
    )
    events: list[str] = []
    active: set[str] = set()

    @contextlib.contextmanager
    def lease(_root: Path, kind: str):
        active.add(kind)
        events.append(f"lock:{kind}")
        try:
            yield ESP32_RS / ("build" if kind == "production" else "build_qemu_test")
        finally:
            active.remove(kind)
            events.append(f"unlock:{kind}")

    def verify(_root: Path, kind: str, _bundle: Path):
        events.append(f"verify:{kind}")
        if kind == "qemu-test":
            return provenance.Result(provenance.EXIT_STALE, "fixture is stale")
        return provenance.Result(0, "current")

    monkeypatch.setattr(module, "shared_bundle", lease)
    monkeypatch.setattr(module, "_verify_current", verify)
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda _name: events.append("docker") or "/usr/bin/docker",
    )

    fixture = module._verified_bundles.__wrapped__()
    with pytest.raises(pytest.fail.Exception, match="stale"):
        next(fixture)
    assert "docker" not in events
    assert events[:4] == [
        "lock:production",
        "verify:production",
        "lock:qemu-test",
        "verify:qemu-test",
    ]
    assert not active


def test_harness_session_holds_both_locks_through_artifact_reads(monkeypatch):
    module = _load(
        "task7_harness_conftest_lifetime",
        TOOLS / "qemu_harness" / "conftest.py",
    )
    active: set[str] = set()

    @contextlib.contextmanager
    def lease(_root: Path, kind: str):
        active.add(kind)
        try:
            yield ESP32_RS / ("build" if kind == "production" else "build_qemu_test")
        finally:
            active.remove(kind)

    monkeypatch.setattr(module, "shared_bundle", lease)
    monkeypatch.setattr(
        module,
        "_verify_current",
        lambda *_args: provenance.Result(0, "current"),
    )
    fixture = module._verified_bundles.__wrapped__()
    bundles = next(fixture)
    assert active == {"production", "qemu-test"}
    assert module.default_build_bin.__wrapped__(bundles).name == "esp32tap.bin"
    assert module.test_build_bin.__wrapped__(bundles).name == "esp32tap.bin"
    with pytest.raises(StopIteration):
        next(fixture)
    assert not active


def test_scenarios_fixture_fails_missing_without_skipping(monkeypatch):
    module = _load(
        "task7_scenarios_conftest",
        TOOLS / "qemu_scenarios" / "conftest.py",
    )

    @contextlib.contextmanager
    def lease(_root: Path, _kind: str):
        yield ESP32_RS / "build_qemu_test"

    monkeypatch.setattr(module, "shared_bundle", lease)
    monkeypatch.setattr(
        module,
        "_verify_current",
        lambda *_args: provenance.Result(
            provenance.EXIT_MISSING, "artifact manifest is missing"
        ),
    )
    fixture = module._verified_test_bundle.__wrapped__()
    with pytest.raises(pytest.fail.Exception, match="manifest is missing"):
        next(fixture)


def test_qemu_session_verifies_under_lock_before_first_port(monkeypatch):
    module = _load(
        "task7_qemu_session",
        TOOLS / "qemu_harness" / "qemu_session.py",
    )
    events: list[str] = []

    @contextlib.contextmanager
    def lease(_root: Path, kind: str):
        events.append(f"lock:{kind}")
        try:
            yield ESP32_RS / "build_qemu_test"
        finally:
            events.append(f"unlock:{kind}")

    monkeypatch.setattr(module, "shared_bundle", lease)
    monkeypatch.setattr(
        module,
        "_verify_current",
        lambda *_args: (
            events.append("verify")
            or provenance.Result(provenance.EXIT_STALE, "stale input digest")
        ),
    )
    monkeypatch.setattr(
        module,
        "_lease_port",
        lambda: pytest.fail("port allocation happened before verification"),
    )
    session = module.QemuSession.__new__(module.QemuSession)
    session.esp32_dir = ESP32_RS
    session.repo_root = REPO_ROOT
    session.build_dir = "build_qemu_test"
    session.net = False
    session._leases = []
    session._bundle_lease = None
    session.close = lambda: session._release_bundle_lease()

    with pytest.raises(module.HarnessError, match="stale input digest"):
        session._start(0.01)
    assert events == ["lock:qemu-test", "verify", "unlock:qemu-test"]


def test_qemu_constructor_stale_failure_never_calls_docker_or_port(monkeypatch):
    module = _load(
        "task7_qemu_session_no_external",
        TOOLS / "qemu_harness" / "qemu_session.py",
    )
    events: list[str] = []

    @contextlib.contextmanager
    def lease(_root: Path, _kind: str):
        events.append("lock")
        try:
            yield ESP32_RS / "build_qemu_test"
        finally:
            events.append("unlock")

    monkeypatch.setattr(module, "shared_bundle", lease)
    monkeypatch.setattr(
        module,
        "_verify_current",
        lambda *_args: events.append("verify")
        or provenance.Result(provenance.EXIT_STALE, "stale"),
    )
    monkeypatch.setattr(
        module,
        "_lease_port",
        lambda: pytest.fail("port allocation happened after stale rejection"),
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: events.append("docker")
        or pytest.fail("Docker ran after stale rejection"),
    )
    with pytest.raises(module.HarnessError, match="stale"):
        module.QemuSession(ESP32_RS, "build_qemu_test")
    assert events == ["lock", "verify", "unlock"]


def test_qemu_session_holds_bundle_until_close(monkeypatch):
    module = _load(
        "task7_qemu_session_lifetime",
        TOOLS / "qemu_harness" / "qemu_session.py",
    )
    active = False

    @contextlib.contextmanager
    def lease(_root: Path, _kind: str):
        nonlocal active
        active = True
        try:
            yield ESP32_RS / "build_qemu_test"
        finally:
            active = False

    monkeypatch.setattr(module, "shared_bundle", lease)
    monkeypatch.setattr(
        module,
        "_verify_current",
        lambda *_args: provenance.Result(0, "current"),
    )
    session = module.QemuSession.__new__(module.QemuSession)
    session.esp32_dir = ESP32_RS
    session.repo_root = REPO_ROOT
    session.build_dir = "build_qemu_test"
    session._bundle_lease = None
    session._acquire_verified_bundle()
    assert active
    session._release_bundle_lease()
    assert not active


def test_missing_bundle_fails_before_toolchain_docker_inspect(
    monkeypatch, legacy_qemu_bundle: Path
):
    module = _load(
        "task7_qemu_session_preflight",
        TOOLS / "qemu_harness" / "qemu_session.py",
    )
    monkeypatch.setattr(
        module,
        "_full_verify_current",
        lambda *_args: pytest.fail("Docker image inspect ran before missing rejection"),
    )
    result = module._verify_current(
        REPO_ROOT,
        "qemu-test",
        legacy_qemu_bundle,
    )
    assert not result.ok


def test_constructor_failure_after_lock_releases_bundle_and_port(monkeypatch):
    module = _load(
        "task7_qemu_session_constructor_cleanup",
        TOOLS / "qemu_harness" / "qemu_session.py",
    )
    events: list[str] = []

    @contextlib.contextmanager
    def lease(_root: Path, _kind: str):
        events.append("bundle:lock")
        try:
            yield ESP32_RS / "build_qemu_test"
        finally:
            events.append("bundle:unlock")

    class PortLease:
        def close(self):
            events.append("port:close")

    calls = 0

    def port():
        nonlocal calls
        calls += 1
        if calls == 1:
            return 21234, PortLease()
        raise RuntimeError("second port failed")

    monkeypatch.setattr(module, "shared_bundle", lease)
    monkeypatch.setattr(
        module,
        "_verify_current",
        lambda *_args: provenance.Result(0, "current"),
    )
    monkeypatch.setattr(module, "_lease_port", port)
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: None)
    with pytest.raises(RuntimeError, match="second port failed"):
        module.QemuSession(ESP32_RS, "build_qemu_test")
    assert events == ["bundle:lock", "port:close", "bundle:unlock"]


def test_close_attempts_all_resources_and_releases_bundle_last(monkeypatch):
    module = _load(
        "task7_qemu_session_close",
        TOOLS / "qemu_harness" / "qemu_session.py",
    )
    events: list[str] = []

    class Action:
        def __init__(self, name: str, *, fail: bool = False):
            self.name = name
            self.fail = fail

        def close(self):
            events.append(self.name)
            if self.fail:
                raise RuntimeError(self.name)

        def set(self):
            events.append(self.name)

        def join(self, timeout: float):
            assert timeout == 5
            self.close()

    class Process:
        stdout = Action("stdout")

        def wait(self, timeout: float):
            assert timeout == 20
            events.append("wait")
            raise RuntimeError("wait")

        def kill(self):
            events.append("kill")

    @contextlib.contextmanager
    def bundle():
        yield ESP32_RS / "build_qemu_test"
        events.append("bundle")

    session = module.QemuSession.__new__(module.QemuSession)
    session._close_lock = __import__("threading").Lock()
    session._closed = False
    session._stop = Action("stop")
    session.stop_pacer = Action("pacer", fail=True).close
    session.name = "fake"
    session.proc = Process()
    session.sock0 = Action("sock0")
    session.sock1 = Action("sock1")
    session._threads = [Action("thread")]
    session._leases = [Action("port"), Action("build")]
    session._bundle_lease = bundle()
    session._bundle_lease.__enter__()
    session._bundle_path = ESP32_RS / "build_qemu_test"
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: events.append("docker")
        or (_ for _ in ()).throw(RuntimeError("docker")),
    )

    with pytest.raises(RuntimeError, match="pacer"):
        session.close()
    for expected in (
        "stop",
        "pacer",
        "docker",
        "wait",
        "sock0",
        "sock1",
        "thread",
        "port",
        "build",
        "bundle",
    ):
        assert expected in events
    assert events[-1] == "bundle"
    before = list(events)
    session.close()
    assert events == before


def test_shared_reader_blocks_writer_for_fixture_lifetime():
    lock = provenance.lock_path(REPO_ROOT, "qemu-test")
    with provenance.shared_bundle(REPO_ROOT, "qemu-test"):
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import fcntl,os,sys;"
                    "f=os.open(sys.argv[1],os.O_RDWR);"
                    "\ntry: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
                    "\nexcept BlockingIOError: sys.exit(0)"
                    "\nelse: sys.exit(1)"
                ),
                str(lock),
            ],
            check=False,
        )
        assert proc.returncode == 0


def test_exec_many_holds_two_inheritable_lock_descriptors(monkeypatch):
    toolchain = provenance.Toolchain(
        image_id="sha256:" + "1" * 64,
        recipe_sha256="2" * 64,
        image_tag="esp32tap-rust:test",
        idf_commit="3" * 40,
        rustc_verbose="rustc 1.0",
        target="xtensa-esp32s3-none-elf",
        linker_version="ld 1.0",
        esptool_version="esptool 1.0",
        component_lock_sha256="4" * 64,
        profile="qemu",
        features=("qemu-test",),
    )
    monkeypatch.setattr(provenance, "_current_input_digest", lambda _root: "5" * 64)
    monkeypatch.setattr(provenance, "_current_toolchain", lambda *_args: toolchain)
    monkeypatch.setattr(
        provenance,
        "verify_locked",
        lambda *_args: provenance.Result(0, "current"),
    )
    lock_info = provenance.lock_path(REPO_ROOT, "production").stat()

    def intercept(_program: str, _argv: list[str]):
        matching = []
        for name in os.listdir("/proc/self/fd"):
            try:
                fd = int(name)
                info = os.fstat(fd)
            except (OSError, ValueError):
                continue
            if (info.st_dev, info.st_ino) == (lock_info.st_dev, lock_info.st_ino):
                matching.append(fd)
        assert len(matching) == 2
        assert all(os.get_inheritable(fd) for fd in matching)
        raise provenance._ExecIntercept

    monkeypatch.setattr(provenance.os, "execvp", intercept)
    with pytest.raises(provenance._ExecIntercept):
        provenance.locked_exec_many(
            REPO_ROOT,
            ("production", "qemu-test"),
            ["true"],
        )


def test_devkit_cli_kind_dispatches_only_through_provenance_exec() -> None:
    calls: list[tuple[Path, str, list[str]]] = []

    def intercept(root: Path, kind: str, argv: list[str]) -> None:
        calls.append((root, kind, argv))
        raise provenance._ExecIntercept

    with pytest.raises(provenance._ExecIntercept):
        provenance.main(
            [
                "--repo-root",
                str(REPO_ROOT),
                "exec",
                "--kind",
                "devkit-bringup",
                "--",
                "true",
            ],
            exec_one=intercept,
        )
    assert calls == [(REPO_ROOT, "devkit-bringup", ["true"])]


def test_devkit_direct_verification_refuses_dirty_tree_before_toolchain_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    rs = root / "hardware/Esp32Tap/firmware/esp32_rs"
    rs.mkdir(parents=True)
    source = rs / "devkit_bringup.rs"
    source.write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    source.write_text("dirty\n", encoding="utf-8")
    monkeypatch.setattr(
        provenance,
        "_current_toolchain",
        lambda *_args, **_kwargs: pytest.fail(
            "Docker-backed toolchain check ran before dirty refusal"
        ),
    )

    result = provenance._verify_current(
        root, "devkit-bringup", rs / "build_devkit_bringup"
    )
    assert result.code == provenance.EXIT_INVALID
    assert "clean Git worktree" in result.message


@pytest.mark.parametrize(
    ("relative", "operation", "kinds"),
    (
        ("qemu_smoke.sh", "exec", ("production",)),
        ("run_harness.sh", "exec-many", ("production", "qemu-test")),
        ("qemu_harness/run.sh", "exec-many", ("production", "qemu-test")),
    ),
)
def test_shell_entrypoint_verifies_before_delegating(
    tmp_path: Path,
    relative: str,
    operation: str,
    kinds: tuple[str, ...],
):
    script = TOOLS / relative
    assert script.is_file() and not script.is_symlink()
    assert os.access(script, os.X_OK)
    assert stat.S_IMODE(script.stat().st_mode) == 0o755

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "python.log"
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$*" > "$TASK7_LOG"\nexit 21\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["TASK7_LOG"] = str(log)
    launcher = tmp_path / "entrypoint"
    launcher.symlink_to(script)
    hostile_cwd = tmp_path / "cwd with spaces"
    hostile_cwd.mkdir()
    result = subprocess.run(
        [str(launcher), "--sentinel", "--kind", "hostile value"],
        cwd=hostile_cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == provenance.EXIT_STALE
    invocation = log.read_text(encoding="utf-8")
    assert "artifact_provenance.py" in invocation
    assert operation in invocation
    for kind in kinds:
        assert f"--kind {kind}" in invocation
    assert "--sentinel" in invocation
    assert "hostile value" in invocation


def _directory_snapshot(path: Path) -> tuple[tuple[str, int, str], ...]:
    result = []
    for entry in sorted(path.iterdir()):
        info = entry.lstat()
        assert stat.S_ISREG(info.st_mode), entry
        result.append(
            (
                entry.name,
                stat.S_IMODE(info.st_mode),
                hashlib.sha256(entry.read_bytes()).hexdigest(),
            )
        )
    return tuple(result)


def _smoke_fixture(tmp_path: Path):
    fixture_root = tmp_path / "fixture root"
    rust_tools = (
        fixture_root / "hardware" / "Esp32Tap" / "firmware" / "esp32_rs" / "tools"
    )
    cpp_tools = rust_tools.parent.parent / "esp32" / "tools"
    rust_tools.mkdir(parents=True)
    cpp_tools.mkdir(parents=True)
    wrapper = rust_tools / "qemu_smoke.sh"
    shutil.copy2(TOOLS / "qemu_smoke.sh", wrapper)
    wrapper.chmod(0o755)
    (rust_tools / "artifact_provenance.py").write_text(
        "# intercepted by the fake python3\n",
        encoding="utf-8",
    )
    rust_build = rust_tools.parent / ".artifacts" / "prod" / ("a" * 64)
    rust_build.mkdir(parents=True)
    (rust_tools.parent / "build").symlink_to(Path(".artifacts") / "prod" / ("a" * 64))
    for index, member in enumerate(MEMBERS):
        artifact = rust_build / member
        artifact.write_bytes(f"{member}:{index}\n".encode())
        artifact.chmod(0o444)
    cpp_smoke = cpp_tools / "qemu_smoke.sh"
    cpp_smoke.write_text(
        "#!/usr/bin/env bash\n"
        'ESP32_DIR="$(cd "$(dirname "$0")/.." && pwd)"\n'
        'printf "temporary qemu flash\\n" >"$ESP32_DIR/build/qemu_flash.bin"\n'
        'printf "zero=%s\\nesp32_dir=%s\\nenv=%s\\n" "$0" "$ESP32_DIR" "$SMOKE_SENTINEL"\n'
        'printf "arg=<%s>\\n" "$@"\n'
        'sleep "${SMOKE_FAKE_DELAY:-0}"\n'
        'exit "${SMOKE_FAKE_EXIT}"\n',
        encoding="utf-8",
    )
    cpp_smoke.chmod(0o755)

    fake_bin = tmp_path / "fake bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        'while [ "$#" -gt 0 ] && [ "$1" != -- ]; do shift; done\n'
        '[ "$#" -gt 0 ] || exit 98\n'
        "shift\n"
        'exec "$@"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SMOKE_SENTINEL"] = "environment preserved"
    env["SMOKE_FAKE_EXIT"] = "37"
    return wrapper, rust_build, rust_tools.parent.parent, env


def test_smoke_sources_head_anchored_gate_in_private_build_context(
    tmp_path: Path,
):
    wrapper, rust_build, firmware_dir, env = _smoke_fixture(tmp_path)
    before = _directory_snapshot(rust_build)
    assert {row[0] for row in before} == set(MEMBERS)

    result = subprocess.run(
        [str(wrapper), "one argument", "", "--literal"],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 37
    fields = dict(
        line.split("=", 1)
        for line in result.stdout.splitlines()
        if line.startswith(("zero=", "esp32_dir="))
    )
    private_root = Path(fields["esp32_dir"])
    assert private_root.parent == firmware_dir.resolve()
    assert private_root.name.startswith(".esp32tap-smoke.")
    assert fields["zero"] == str(private_root / "tools" / "qemu_smoke.sh")
    assert "env=environment preserved" in result.stdout
    assert result.stdout.splitlines()[-3:] == [
        "arg=<one argument>",
        "arg=<>",
        "arg=<--literal>",
    ]
    assert _directory_snapshot(rust_build) == before
    assert not list(firmware_dir.glob(".esp32tap-smoke.*"))


def test_concurrent_smokes_use_distinct_private_builds_and_clean_them(
    tmp_path: Path,
):
    wrapper, rust_build, firmware_dir, env = _smoke_fixture(tmp_path)
    before = _directory_snapshot(rust_build)
    env["SMOKE_FAKE_EXIT"] = "0"
    env["SMOKE_FAKE_DELAY"] = "0.2"

    processes = [
        subprocess.Popen(
            [str(wrapper), f"run-{index}"],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(2)
    ]
    results = [process.communicate(timeout=5) for process in processes]

    assert [process.returncode for process in processes] == [0, 0]
    roots = []
    for stdout, stderr in results:
        assert stderr == ""
        line = next(
            line for line in stdout.splitlines() if line.startswith("esp32_dir=")
        )
        roots.append(Path(line.split("=", 1)[1]))
    assert roots[0] != roots[1]
    assert all(root.parent == firmware_dir.resolve() for root in roots)
    assert _directory_snapshot(rust_build) == before
    assert not list(firmware_dir.glob(".esp32tap-smoke.*"))


def test_interrupted_smoke_cleans_private_build(tmp_path: Path):
    wrapper, rust_build, firmware_dir, env = _smoke_fixture(tmp_path)
    before = _directory_snapshot(rust_build)
    env["SMOKE_FAKE_EXIT"] = "0"
    env["SMOKE_FAKE_DELAY"] = "30"
    process = subprocess.Popen(
        [str(wrapper), "interrupt"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert process.stdout is not None
    private_root = None
    for _ in range(4):
        line = process.stdout.readline().strip()
        if line.startswith("esp32_dir="):
            private_root = Path(line.split("=", 1)[1])
            break
    assert private_root is not None and private_root.is_dir()

    os.killpg(process.pid, signal.SIGTERM)
    process.communicate(timeout=5)

    assert process.returncode == 143
    assert not private_root.exists()
    assert _directory_snapshot(rust_build) == before
    assert not list(firmware_dir.glob(".esp32tap-smoke.*"))


def test_strengthened_harness_and_smoke_are_exactly_sha_pinned():
    verifier = _load("task7_verify_harness", TOOLS / "verify_harness_copy.py")
    for name in ("conftest.py", "qemu_session.py", "run.sh"):
        pinned, reason = verifier.ALLOWED_STRENGTHENING[name]
        assert (
            pinned
            == hashlib.sha256((TOOLS / "qemu_harness" / name).read_bytes()).hexdigest()
        )
        assert "provenance" in reason.lower()
    pinned, reason = verifier.SMOKE_STRENGTHENING
    assert pinned == hashlib.sha256((TOOLS / "qemu_smoke.sh").read_bytes()).hexdigest()
    assert "provenance" in reason.lower()


@pytest.fixture
def isolated_harness_verifier(tmp_path: Path, monkeypatch):
    """Copy every verifier input so adversarial mutations never touch the tree."""
    verifier = _load(
        "task7_verify_harness_isolated",
        TOOLS / "verify_harness_copy.py",
    )
    fixture_root = tmp_path / "fixture"
    copy_dir = fixture_root / "rust-tools" / "qemu_harness"
    live_dir = fixture_root / "cpp-tools" / "qemu_harness"
    copy_dir.parent.mkdir(parents=True)
    live_dir.parent.mkdir(parents=True)
    shutil.copytree(TOOLS / "qemu_harness", copy_dir)
    source_live = REPO_ROOT / "hardware/Esp32Tap/firmware/esp32/tools/qemu_harness"
    shutil.copytree(source_live, live_dir)

    rows = subprocess.run(
        [
            "git",
            "ls-tree",
            "-r",
            "HEAD",
            "hardware/Esp32Tap/firmware/esp32/tools/qemu_harness",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    modes = {}
    for row in rows:
        metadata, path = row.split("\t", 1)
        mode = metadata.split()[0]
        name = Path(path).name
        modes[name] = mode
        physical_mode = int(mode[-3:], 8)
        (copy_dir / name).chmod(physical_mode)
        (live_dir / name).chmod(physical_mode)
    names = sorted(modes)
    head_blobs = {name: (source_live / name).read_bytes() for name in names}

    rust_smoke = copy_dir.parent / "qemu_smoke.sh"
    shutil.copy2(TOOLS / "qemu_smoke.sh", rust_smoke)
    rust_smoke.chmod(0o755)
    smoke_rel = Path(verifier.SMOKE_REL)
    cpp_smoke = fixture_root / smoke_rel
    cpp_smoke.parent.mkdir(parents=True, exist_ok=True)
    source_cpp_smoke = REPO_ROOT / smoke_rel
    shutil.copy2(source_cpp_smoke, cpp_smoke)
    cpp_smoke.chmod(0o755)
    smoke_blob = source_cpp_smoke.read_bytes()

    monkeypatch.setattr(verifier, "HERE", copy_dir.parent)
    monkeypatch.setattr(verifier, "REPO_ROOT", fixture_root)
    monkeypatch.setattr(verifier, "COPY_DIR", copy_dir)
    monkeypatch.setattr(verifier, "LIVE_DIR", live_dir)
    monkeypatch.setattr(verifier, "head_files", lambda: names)
    monkeypatch.setattr(verifier, "head_blob", lambda name: head_blobs[name])
    monkeypatch.setattr(verifier, "head_blob_at", lambda _rel: smoke_blob)
    monkeypatch.setattr(verifier, "head_modes", lambda: modes, raising=False)
    monkeypatch.setattr(verifier, "head_mode_at", lambda _rel: "100755", raising=False)
    return verifier, {
        "copy_python": copy_dir / "conftest.py",
        "copy_shell": copy_dir / "run.sh",
        "live_python": live_dir / "conftest.py",
        "live_shell": live_dir / "run.sh",
        "rust_smoke": rust_smoke,
        "cpp_smoke": cpp_smoke,
    }


@pytest.mark.parametrize(
    "target_name",
    (
        "copy_python",
        "copy_shell",
        "live_python",
        "live_shell",
        "rust_smoke",
        "cpp_smoke",
    ),
)
@pytest.mark.parametrize("attack", ("symlink", "wrong_mode"))
def test_harness_verifier_rejects_type_and_mode_before_hashing(
    isolated_harness_verifier,
    tmp_path: Path,
    capsys,
    monkeypatch,
    target_name: str,
    attack: str,
):
    verifier, targets = isolated_harness_verifier
    target = targets[target_name]
    original = target.read_bytes()
    original_mode = stat.S_IMODE(target.lstat().st_mode)

    if attack == "symlink":
        relocated = tmp_path / f"relocated-{target_name}"
        relocated.write_bytes(original)
        relocated.chmod(original_mode)
        target.unlink()
        target.symlink_to(relocated)
    else:
        target.chmod(0o755 if original_mode == 0o644 else 0o644)

    real_sha = verifier._sha

    def forbid_target_hash(data: bytes) -> str:
        assert (
            data != original
        ), f"{target_name} bytes were hashed before type/mode rejection"
        return real_sha(data)

    monkeypatch.setattr(verifier, "_sha", forbid_target_hash)
    assert verifier.main() == 1
    failure = capsys.readouterr().err
    assert "TYPE/MODE" in failure

    monkeypatch.setattr(verifier, "_sha", real_sha)
    if target.is_symlink():
        target.unlink()
        target.write_bytes(original)
    target.chmod(original_mode)
    assert verifier.main() == 0


def test_git_records_both_wrappers_as_executable_regular_files():
    paths = (
        "hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_smoke.sh",
        "hardware/Esp32Tap/firmware/esp32_rs/tools/run_harness.sh",
        "hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_harness/run.sh",
    )
    for path in paths:
        row = subprocess.run(
            ["git", "ls-files", "--stage", path],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert row.startswith("100755 "), row
