#!/usr/bin/env python3
"""Fast host-only contract tests for the immutable snapshot build driver."""

from __future__ import annotations

import hashlib
import json
import fcntl
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
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
    assert "ONLY must be prod, qemu, devkit, or both" in completed.stderr


def test_real_repo_ignores_each_exact_publication_alias_and_store() -> None:
    repo_root = Path(
        subprocess.run(
            ["git", "-C", str(TOOLS), "rev-parse", "--show-toplevel"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
    )
    paths = (
        "hardware/Esp32Tap/firmware/esp32_rs/build",
        "hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test",
        "hardware/Esp32Tap/firmware/esp32_rs/build_devkit_bringup",
        "hardware/Esp32Tap/firmware/esp32_rs/.artifacts/devkit/generation",
    )

    for relative in paths:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "check-ignore", "-q", relative],
            check=False,
        )
        assert completed.returncode == 0, f"publication path is not ignored: {relative}"

    unrelated = "hardware/Esp32Tap/firmware/esp32_rs/build_devkit_bringup_notes"
    assert (
        subprocess.run(
            ["git", "-C", str(repo_root), "check-ignore", "-q", unrelated],
            check=False,
        ).returncode
        == 1
    )


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


def test_devkit_build_uses_isolated_crate_config_and_final_linked_elf() -> None:
    text = source()
    assert '("devkit-bringup", "devkit")' in text
    assert 'cp -a "$RS_DIR/devkit_bringup" "$DEVKIT_BUILD_ROOT/devkit_bringup"' in text
    assert 'CRATE="$DEVKIT_BUILD_ROOT/devkit_bringup"' in text
    assert 'SDK="$RS_DIR/sdkconfig.defaults.devkit"' in text
    assert "ESP32TAP_RECIPE_ID" in text
    assert "ESP32TAP_GIT_COMMIT" in text
    assert "APP_NAME=devkit_bringup" in text
    assert '"$T/$APP_NAME"' in text
    assert "libespidf.bin" not in text
    assert "/project/.esp32tap-snapshot-v1" in text
    assert "CONFIG_ESPTOOLPY_FLASHSIZE_8MB=y" in text
    assert "CONFIG_SPIRAM_MODE_OCT=y" in text
    assert "CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y" in text
    assert "CONFIG_ESP_CONSOLE_UART_DEFAULT=y" in text
    assert "esp_wifi_init" in text
    assert "nimble_port_init" in text
    assert "image > factory" in text
    assert "DEVKIT_BUILD_ROOT=/tmp/esp32tap-devkit-source" in text
    assert 'cp -f "$RS_DIR/esp32tap/Cargo.lock" "$CRATE/Cargo.lock"' in text
    assert 'chmod u+w "$CRATE/Cargo.lock"' in text
    assert (
        'cargo +esp metadata --manifest-path "$CRATE/Cargo.toml" '
        '--format-version=1 >"$metadata"' in text
    )
    assert "derived DevKit Cargo lock contains an unpinned external package" in text
    assert 'cargo +esp build --manifest-path "$CRATE/Cargo.toml" --release' in text
    assert '--message-format=json-render-diagnostics "${args[@]}"' in text


def test_devkit_generated_contract_precedes_app_conversion_with_exact_inputs() -> None:
    text = source()
    contract = 'python3 "$RS_DIR/tools/test_devkit_source_contract.py" generated'
    assert (
        text.index('cargo +esp build --manifest-path "$CRATE/Cargo.toml"')
        < (text.index(contract))
        < text.index("python -m esptool --chip esp32s3 elf2image")
    )
    assert '--sdkconfig "$sdk"' in text
    assert '--elf "$T/$APP_NAME"' in text
    assert '--recipe-id "$ESP32TAP_RECIPE_ID"' in text
    assert '--git-commit "$ESP32TAP_GIT_COMMIT"' in text


def test_physical_worktree_keys_lock_targets_and_cache() -> None:
    text = source()
    assert 'lock_path(repo_root, "production")' in text
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
    inputs = tools / "artifact_inputs.py"
    _write(
        inputs,
        inputs.read_text(encoding="utf-8")
        + """

_real_create_snapshot_for_seam_test = create_snapshot


def create_snapshot(*args, **kwargs):
    mutation_path = os.environ.get("FAKE_SNAPSHOT_MUTATE_REVERT")
    if mutation_path:
        mutation = Path(mutation_path)
        original = mutation.read_bytes()
        mutation.write_bytes(b"B" + original[1:])
        try:
            result = _real_create_snapshot_for_seam_test(*args, **kwargs)
        finally:
            mutation.write_bytes(original)
    else:
        result = _real_create_snapshot_for_seam_test(*args, **kwargs)
    seam = os.environ.get("FAKE_RESOURCE_SEAM")
    if seam in ("snapshot_return", "snapshot_exception"):
        Path(os.environ["FAKE_RESOURCE_PATH"]).write_text(
            str(result.root), encoding="utf-8"
        )
        if seam == "snapshot_return":
            import signal as _signal

            os.kill(os.getpid(), _signal.SIGTERM)
        else:
            raise RuntimeError("intentional create_snapshot return failure")
    return result
""",
    )
    provenance = tools / "artifact_provenance.py"
    _write(
        provenance,
        provenance.read_text(encoding="utf-8")
        + """

_real_publish_generation_atomic = publish_generation_atomic


def publish_generation_atomic(*args, **kwargs):
    barrier_text = os.environ.get("FAKE_PUBLISH_SIGNAL_BARRIER")
    if barrier_text:
        import signal as _signal
        import time as _time

        barrier = Path(barrier_text)
        os.kill(os.getpid(), _signal.SIGTERM)
        barrier.with_suffix(".term-handled").write_text("term", encoding="utf-8")
        while not barrier.with_suffix(".release").exists():
            _time.sleep(0.01)
    return _real_publish_generation_atomic(*args, **kwargs)
""",
    )
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
    _write(
        tools / "test_devkit_source_contract.py",
        (
            "import json, sys\n"
            "from pathlib import Path\n"
            f"log = Path({str(event_log)!r})\n"
            "if sys.argv[1:] != ['prebuild']:\n"
            "    raise SystemExit('fake contract accepts only prebuild')\n"
            "with log.open('a', encoding='utf-8') as stream:\n"
            "    stream.write(json.dumps({'event':'gate','name':"
            "'test_devkit_source_contract.py','path':__file__}) + '\\n')\n"
            "source = Path(__file__).parent.parent / 'devkit_bringup/src/main.rs'\n"
            "if 'gpio_set_level' in source.read_text(encoding='utf-8').lower():\n"
            "    raise SystemExit('forbidden DevKit source contract token: gpio_set_level')\n"
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
    _write(
        root / ".gitignore",
        "hardware/Esp32Tap/firmware/esp32_rs/.artifacts/\n"
        "hardware/Esp32Tap/firmware/esp32_rs/build\n"
        "hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test\n"
        "hardware/Esp32Tap/firmware/esp32_rs/build_devkit_bringup\n",
    )
    _write(rs / "esp32tap" / "components_esp32s3.lock", "lock\n")
    _write(rs / "bringup_core" / "src" / "lib.rs", "pub const SAFE: bool = true;\n")
    _write(rs / "devkit_bringup" / "Cargo.toml", "[package]\nname='devkit_bringup'\n")
    _write(rs / "devkit_bringup" / "src" / "main.rs", "fn main() {}\n")
    _write(
        rs / "sdkconfig.defaults.devkit",
        'CONFIG_IDF_TARGET="esp32s3"\n'
        "CONFIG_ESPTOOLPY_FLASHSIZE_8MB=y\n"
        "CONFIG_SPIRAM_MODE_OCT=y\n",
    )
    source_file = rs / "esp32tap" / "src" / "lib.rs"
    _write(source_file, "A\n")

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

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    signal_hooks = tmp_path / "signal-hooks"
    signal_hooks.mkdir()
    _write(
        signal_hooks / "sitecustomize.py",
        """import os
import signal
import shutil
import sys
import tempfile
from pathlib import Path

seam = os.environ.get("FAKE_RESOURCE_SEAM", "")
resource_path = os.environ.get("FAKE_RESOURCE_PATH", "")
trace_line = int(os.environ.get("FAKE_TRACE_SIGNAL_LINE", "0"))
trace_marker = os.environ.get("FAKE_TRACE_SIGNAL_MARKER", "")


def signal_at_cleanup_entry(frame, event, arg):
    if (
        event == "line"
        and frame.f_code.co_filename == "<stdin>"
        and frame.f_lineno == trace_line
    ):
        sys.settrace(None)
        Path(trace_marker).write_text("cleanup-entry", encoding="utf-8")
        os.kill(os.getpid(), signal.SIGTERM)
        return None
    return signal_at_cleanup_entry


if trace_line:
    sys.settrace(signal_at_cleanup_entry)


def signal_after_create(path):
    Path(resource_path).write_text(str(path), encoding="utf-8")
    os.kill(os.getpid(), signal.SIGTERM)


_real_mkdtemp = tempfile.mkdtemp


def seam_mkdtemp(*args, **kwargs):
    result = _real_mkdtemp(*args, **kwargs)
    prefix = kwargs.get("prefix", args[0] if args else "")
    if seam == "task_root_return" and prefix == "esp32tap-snapshot-build.":
        signal_after_create(result)
    if seam == "snapshot_internal_temp_return" and prefix.startswith(".source."):
        signal_after_create(result)
    return result


tempfile.mkdtemp = seam_mkdtemp
_real_mkdir = Path.mkdir


def seam_mkdir(self, *args, **kwargs):
    result = _real_mkdir(self, *args, **kwargs)
    if seam == "staging_mkdir_return" and self.name.startswith(".snapshot-build-"):
        signal_after_create(self)
    return result


Path.mkdir = seam_mkdir
_real_rename = os.rename


def seam_rename(source, destination, *args, **kwargs):
    result = _real_rename(source, destination, *args, **kwargs)
    if (
        seam == "live_digest_rename_return"
        and Path(destination).name.startswith("output-sdkconfig-")
    ):
        signal_after_create(destination)
    return result


os.rename = seam_rename
_real_rmtree = shutil.rmtree


def seam_rmtree(path, *args, **kwargs):
    failure_path = os.environ.get("FAKE_CLEANUP_FAILURE_PATH", "")
    candidate = Path(path)
    if (
        failure_path
        and candidate.name.startswith(".snapshot-build-")
        and not Path(failure_path).exists()
    ):
        Path(failure_path).write_text(str(candidate), encoding="utf-8")
        raise RuntimeError("intentional first staging cleanup failure")
    return _real_rmtree(path, *args, **kwargs)


shutil.rmtree = seam_rmtree
""",
    )
    docker_log = tmp_path / "docker.jsonl"
    _write(
        fake_bin / "docker",
        """#!/usr/bin/env python3
import json, os, signal, struct, sys, time
from pathlib import Path
argv = sys.argv[1:]
with Path(os.environ["FAKE_DOCKER_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(argv) + "\\n")
if os.environ.get("FAKE_DOCKER_PID_FILE"):
    Path(os.environ["FAKE_DOCKER_PID_FILE"]).write_text(
        str(os.getpid()), encoding="utf-8"
    )
if os.environ.get("FAKE_DOCKER_BARRIER"):
    barrier = Path(os.environ["FAKE_DOCKER_BARRIER"])
    if os.environ.get("FAKE_DOCKER_RESIST_TERM"):
        def record_term(_signum, _frame):
            barrier.with_suffix(".term").write_text("term", encoding="utf-8")
        signal.signal(signal.SIGTERM, record_term)
    barrier.with_suffix(".pid").write_text(str(os.getpid()), encoding="utf-8")
    barrier.with_suffix(".ready").write_text("ready", encoding="utf-8")
    while not barrier.with_suffix(".release").exists():
        time.sleep(0.01)
if os.environ.get("FAKE_DOCKER_MUTATE"):
    path = Path(os.environ["FAKE_DOCKER_MUTATE"])
    current = path.read_text(encoding="utf-8")
    path.write_text(("B" if current[0] != "B" else "A") + current[1:], encoding="utf-8")
if os.environ.get("FAKE_DOCKER_FAIL"):
    raise SystemExit(17)
command = argv[-1] if argv else ""
if (
    os.environ.get("FAKE_DEVKIT_GENERATED_CONTRACT_FAIL")
    and "test_devkit_source_contract.py" in command
    and " generated " in command
):
    print("generated DevKit contract rejected linked ELF", file=sys.stderr)
    raise SystemExit(23)
mounts = [argv[index + 1] for index, value in enumerate(argv) if value == "-v"]
output = Path(next(value.split(":", 1)[0] for value in mounts if value.endswith(":/output")))
settings = {}
for index, value in enumerate(argv):
    if value == "-e" and index + 1 < len(argv) and "=" in argv[index + 1]:
        key, setting = argv[index + 1].split("=", 1)
        settings[key] = setting
kind = settings.get("ARTIFACT_KIND")
image = b"esp32tap.bin\\n"
sdkconfig = b"sdkconfig\\n"
partition = b"partition-table.bin\\n"
if kind == "devkit-bringup":
    image = (
        settings["ESP32TAP_RECIPE_ID"].encode()
        + settings["ESP32TAP_GIT_COMMIT"].encode()
        + "ESP32TAP DEVKIT BRINGUP — NO CONTROL OUTPUTS".encode()
    )
    sdkconfig = (
        b'CONFIG_IDF_TARGET="esp32s3"\\n'
        b'CONFIG_ESPTOOLPY_FLASHSIZE_8MB=y\\n'
        b'CONFIG_SPIRAM=y\\nCONFIG_SPIRAM_MODE_OCT=y\\n'
        b'CONFIG_ESP_CONSOLE_UART_DEFAULT=y\\n'
        b'CONFIG_ESP_CONSOLE_UART=y\\n'
        b'CONFIG_ESP_CONSOLE_SECONDARY_NONE=y\\n'
        b'# CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG is not set\\n'
        b'# CONFIG_BT_ENABLED is not set\\n'
    )
    entry = struct.pack("<HBBII16sI", 0x50AA, 0, 0, 0x10000, 0x200000, b"factory", 0)
    partition = entry + b"\\xff" * (32 - len(entry)) + b"\\xff" * 32
    corruption = os.environ.get("FAKE_DEVKIT_CORRUPTION", "")
    if corruption == "embedded-recipe":
        image = b"0" * 64 + image[64:]
    elif corruption == "flash-size":
        sdkconfig = sdkconfig.replace(b"FLASHSIZE_8MB", b"FLASHSIZE_4MB")
    elif corruption == "octal-psram":
        sdkconfig = sdkconfig.replace(b"CONFIG_SPIRAM_MODE_OCT=y\\n", b"")
    elif corruption == "usb-jtag":
        sdkconfig += b"CONFIG_USJ_ENABLE_USB_SERIAL_JTAG=y\\n"
    elif corruption == "uart-default":
        sdkconfig = sdkconfig.replace(b"CONFIG_ESP_CONSOLE_UART_DEFAULT=y\\n", b"")
    elif corruption == "qemu-identity":
        image += b"esp32tap QEMU-TEST build"
    elif corruption == "partition-overflow":
        entry = struct.pack("<HBBII16sI", 0x50AA, 0, 0, 0x10000, 1, b"factory", 0)
        partition = entry + b"\\xff" * (32 - len(entry)) + b"\\xff" * 32
    elif corruption == "production-copy":
        image = b"esp32tap production image"
        sdkconfig = b"sdkconfig production\\n"
output.joinpath("esp32tap.bin").write_bytes(image)
output.joinpath("bootloader.bin").write_bytes(b"bootloader.bin\\n")
output.joinpath("partition-table.bin").write_bytes(partition)
output.joinpath("sdkconfig").write_bytes(sdkconfig)
output.joinpath("flash_args").write_text(
    "--flash_mode qio --flash_freq 80m --flash_size 8MB\\n"
    "0x0 bootloader.bin\\n0x8000 partition-table.bin\\n0x10000 esp32tap.bin\\n",
    encoding="utf-8",
)
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
        "esp32_rs/esp32tap" in docker
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


def test_fake_devkit_build_publishes_independent_identity_bound_manifest(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, event_log, docker_log, _ = fake_worktree
    completed = _run(fake_worktree, only="devkit")
    assert completed.returncode == 0, completed.stderr
    events = _events(event_log)
    assert events[0] == {
        "event": "check",
        "kind": "devkit-bringup",
        "path": events[0]["path"],
    }
    docker = _events(docker_log)[0]
    settings = {
        docker[index + 1].split("=", 1)[0]: docker[index + 1].split("=", 1)[1]
        for index, value in enumerate(docker)
        if value == "-e" and "=" in docker[index + 1]
    }
    assert settings["ARTIFACT_KIND"] == "devkit-bringup"
    assert len(settings["ESP32TAP_RECIPE_ID"]) == 64
    assert len(settings["ESP32TAP_GIT_COMMIT"]) == 40
    rs = root / "hardware/Esp32Tap/firmware/esp32_rs"
    public = rs / "build_devkit_bringup"
    assert public.is_symlink()
    assert os.readlink(public).startswith(".artifacts/devkit/")
    assert not (rs / "build").exists()
    assert not (rs / "build_qemu_test").exists()
    manifest = json.loads((public / "artifact-manifest.json").read_text())
    assert manifest["kind"] == "devkit-bringup"
    assert manifest["input_digest"] != manifest["recipe_id"]
    assert manifest["recipe_id"] == settings["ESP32TAP_RECIPE_ID"]
    assert manifest["git_commit"] == settings["ESP32TAP_GIT_COMMIT"]
    assert manifest["dirty_state"] == "clean"
    assert manifest["profile"] == "release"
    assert manifest["flash_geometry"] == {
        "chip": "esp32s3",
        "size": 8_388_608,
        "offsets": [0, 32_768, 65_536],
    }


def test_repeated_devkit_publish_stays_clean_current_and_executable(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, _, docker_log, _ = fake_worktree
    rs = root / "hardware/Esp32Tap/firmware/esp32_rs"
    expected_ignores = {
        "hardware/Esp32Tap/firmware/esp32_rs/.artifacts/",
        "hardware/Esp32Tap/firmware/esp32_rs/build",
        "hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test",
        "hardware/Esp32Tap/firmware/esp32_rs/build_devkit_bringup",
    }
    assert set((root / ".gitignore").read_text(encoding="utf-8").splitlines()) == (
        expected_ignores
    )
    provenance_env = {
        **os.environ,
        "PATH": f"{docker_log.parent / 'fake-bin'}:{os.environ['PATH']}",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    for iteration in range(2):
        built = _run(fake_worktree, only="devkit")
        assert built.returncode == 0, built.stderr
        status = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout
        assert status == ""

        verify = subprocess.run(
            [
                sys.executable,
                str(rs / "tools/artifact_provenance.py"),
                "--repo-root",
                str(root),
                "verify",
                "--kind",
                "devkit-bringup",
                str(rs / "build_devkit_bringup"),
            ],
            cwd=root,
            env=provenance_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert verify.returncode == 0, verify.stderr

        executed = subprocess.run(
            [
                sys.executable,
                str(rs / "tools/artifact_provenance.py"),
                "--repo-root",
                str(root),
                "exec",
                "--kind",
                "devkit-bringup",
                "--",
                sys.executable,
                "-c",
                f"print('current-devkit-{iteration}')",
            ],
            cwd=root,
            env=provenance_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert executed.returncode == 0, executed.stderr
        assert executed.stdout.strip() == f"current-devkit-{iteration}"

    assert len(_events(docker_log)) == 2


def test_devkit_refuses_dirty_live_tree_before_docker(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, _, docker_log, _ = fake_worktree
    live = root / "hardware/Esp32Tap/firmware/esp32_rs/devkit_bringup/src/main.rs"
    live.write_text("fn main() { panic!(); }\n", encoding="utf-8")
    completed = _run(fake_worktree, only="devkit")
    assert completed.returncode != 0
    assert "clean Git worktree" in completed.stderr
    assert not docker_log.exists()


def test_devkit_rejects_mutate_capture_revert_snapshot_race_before_docker(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, _, docker_log, _ = fake_worktree
    source_path = (
        root / "hardware/Esp32Tap/firmware/esp32_rs/devkit_bringup/src/main.rs"
    )
    original = source_path.read_bytes()

    completed = _run(
        fake_worktree,
        only="devkit",
        extra_env={"FAKE_SNAPSHOT_MUTATE_REVERT": str(source_path)},
    )

    assert completed.returncode != 0
    assert "claimed Git commit" in completed.stderr
    assert source_path.read_bytes() == original
    assert not docker_log.exists()
    assert not (
        root / "hardware/Esp32Tap/firmware/esp32_rs/build_devkit_bringup"
    ).exists()


def test_devkit_clean_forbidden_source_contract_stops_before_docker(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, _, docker_log, _ = fake_worktree
    source_path = (
        root / "hardware/Esp32Tap/firmware/esp32_rs/devkit_bringup/src/main.rs"
    )
    source_path.write_text(
        source_path.read_text(encoding="utf-8")
        + "\nfn forbidden() { gpio_set_level(4, 1); }\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(root), "add", str(source_path)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "forbidden clean source"],
        check=True,
    )

    completed = _run(fake_worktree, only="devkit")

    assert completed.returncode != 0
    assert "forbidden DevKit source contract token" in completed.stderr
    assert not docker_log.exists()
    assert not (
        root / "hardware/Esp32Tap/firmware/esp32_rs/build_devkit_bringup"
    ).exists()


def test_devkit_generated_contract_failure_preserves_public_generation(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, _, docker_log, _ = fake_worktree
    rs = root / "hardware/Esp32Tap/firmware/esp32_rs"
    first = _run(fake_worktree, only="devkit")
    assert first.returncode == 0, first.stderr
    original = os.readlink(rs / "build_devkit_bringup")

    rejected = _run(
        fake_worktree,
        only="devkit",
        extra_env={"FAKE_DEVKIT_GENERATED_CONTRACT_FAIL": "1"},
    )

    assert rejected.returncode != 0
    assert "generated DevKit contract rejected linked ELF" in rejected.stderr
    assert os.readlink(rs / "build_devkit_bringup") == original
    assert len(_events(docker_log)) == 2


@pytest.mark.parametrize(
    "corruption",
    [
        "embedded-recipe",
        "flash-size",
        "octal-psram",
        "usb-jtag",
        "uart-default",
        "qemu-identity",
        "partition-overflow",
        "production-copy",
    ],
)
def test_devkit_rejects_wrong_identity_geometry_and_partition_fit(
    fake_worktree: tuple[Path, Path, Path, Path], corruption: str
) -> None:
    root, _, _, _ = fake_worktree
    completed = _run(
        fake_worktree,
        only="devkit",
        extra_env={"FAKE_DEVKIT_CORRUPTION": corruption},
    )
    assert completed.returncode != 0
    assert not (
        root / "hardware/Esp32Tap/firmware/esp32_rs/build_devkit_bringup"
    ).exists()


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
            item / "hardware/Esp32Tap/firmware/esp32_rs" for item in (root, second)
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
        + hashlib.md5(str(rs.resolve()).encode(), usedforsecurity=False).hexdigest()[
            :12
        ]
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
    term_seen = barrier.with_suffix(".term")
    deadline = time.monotonic() + 10
    while not term_seen.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert term_seen.exists()
    child_pid = int(barrier.with_suffix(".pid").read_text(encoding="utf-8"))
    for duplicate in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        process.send_signal(duplicate)
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 128 + signal.SIGTERM, (stdout, stderr)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
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
        + hashlib.md5(str(rs.resolve()).encode(), usedforsecurity=False).hexdigest()[
            :12
        ]
        + ".lock"
    )
    descriptor = os.open(lock, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(descriptor)


def _assert_build_lock_released(root: Path) -> None:
    rs = root / "hardware/Esp32Tap/firmware/esp32_rs"
    lock = Path("/tmp") / (
        "esp32tap-build-"
        + hashlib.md5(str(rs.resolve()).encode(), usedforsecurity=False).hexdigest()[
            :12
        ]
        + ".lock"
    )
    descriptor = os.open(lock, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(descriptor)


def _seam_cleanup_root(created: Path, rs: Path) -> Path:
    absolute = created.absolute()
    if absolute.parent == rs and absolute.name.startswith(".snapshot-build-"):
        return absolute
    for candidate in (absolute, *absolute.parents):
        if candidate.parent == Path("/tmp") and candidate.name.startswith(
            "esp32tap-snapshot-build."
        ):
            return candidate
    raise AssertionError(f"unsafe test seam resource path: {created}")


def _remove_seam_test_resource(created: Path, rs: Path) -> None:
    root = _seam_cleanup_root(created, rs)
    if not os.path.lexists(root):
        return
    assert not root.is_symlink()
    for directory, dirnames, _ in os.walk(root, topdown=False, followlinks=False):
        base = Path(directory)
        for name in dirnames:
            child = base / name
            if not child.is_symlink():
                child.chmod(
                    stat.S_IMODE(child.stat().st_mode) | stat.S_IWUSR | stat.S_IXUSR
                )
        base.chmod(stat.S_IMODE(base.stat().st_mode) | stat.S_IWUSR | stat.S_IXUSR)
    shutil.rmtree(root)


@pytest.mark.parametrize(
    "seam",
    [
        "task_root_return",
        "snapshot_internal_temp_return",
        "snapshot_return",
        "staging_mkdir_return",
        "live_digest_rename_return",
    ],
)
def test_signal_at_resource_registration_seam_cleans_and_releases_lock(
    fake_worktree: tuple[Path, Path, Path, Path],
    seam: str,
) -> None:
    root, event_log, docker_log, _ = fake_worktree
    rs = root / "hardware/Esp32Tap/firmware/esp32_rs"
    resource_path = root.parent / f"{seam}.path"
    docker_pid_path = root.parent / f"{seam}.docker-pid"
    completed = _run(
        fake_worktree,
        extra_env={
            "PYTHONPATH": str(root.parent / "signal-hooks"),
            "FAKE_RESOURCE_SEAM": seam,
            "FAKE_RESOURCE_PATH": str(resource_path),
            "FAKE_DOCKER_PID_FILE": str(docker_pid_path),
        },
    )
    assert resource_path.is_file(), (completed.stdout, completed.stderr)
    created = Path(resource_path.read_text(encoding="utf-8"))
    try:
        assert completed.returncode == 128 + signal.SIGTERM, (
            completed.stdout,
            completed.stderr,
        )
        assert not os.path.lexists(created)
        assert not list(rs.glob(".snapshot-build-*"))
        if event_log.exists():
            snapshot_path = Path(_events(event_log)[0]["path"])
            task_root = next(
                parent
                for parent in snapshot_path.parents
                if parent.name.startswith("esp32tap-snapshot-build.")
            )
            assert not task_root.exists()
        if docker_pid_path.exists():
            docker_pid = int(docker_pid_path.read_text(encoding="utf-8"))
            with pytest.raises(ProcessLookupError):
                os.kill(docker_pid, 0)
        else:
            assert not docker_log.exists()
        _assert_build_lock_released(root)
    finally:
        _remove_seam_test_resource(created, rs)


def test_snapshot_create_exception_after_publication_cleans_registered_parent(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, _, docker_log, _ = fake_worktree
    rs = root / "hardware/Esp32Tap/firmware/esp32_rs"
    resource_path = root.parent / "snapshot-exception.path"
    completed = _run(
        fake_worktree,
        extra_env={
            "FAKE_RESOURCE_SEAM": "snapshot_exception",
            "FAKE_RESOURCE_PATH": str(resource_path),
        },
    )
    assert resource_path.is_file(), (completed.stdout, completed.stderr)
    created = Path(resource_path.read_text(encoding="utf-8"))
    try:
        assert completed.returncode != 0
        assert "intentional create_snapshot return failure" in completed.stderr
        assert not os.path.lexists(created)
        assert not created.parent.exists()
        assert not docker_log.exists()
        _assert_build_lock_released(root)
    finally:
        _remove_seam_test_resource(created, rs)


def _embedded_python_line(exact_text: str) -> int:
    outer = source().split("<<'PY'\n", 1)[1].rsplit("\nPY\n", 1)[0]
    matches = [
        number
        for number, line in enumerate(outer.splitlines(), 1)
        if line.strip() == exact_text
    ]
    assert len(matches) == 1
    return matches[0]


def test_signal_before_cleanup_entry_cannot_bypass_registered_cleanup(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, event_log, _, _ = fake_worktree
    marker = root.parent / "cleanup-entry.marker"
    docker_pid_path = root.parent / "cleanup-entry.docker-pid"
    completed = _run(
        fake_worktree,
        extra_env={
            "PYTHONPATH": str(root.parent / "signal-hooks"),
            "FAKE_TRACE_SIGNAL_LINE": str(
                _embedded_python_line("cleanup_in_progress = True")
            ),
            "FAKE_TRACE_SIGNAL_MARKER": str(marker),
            "FAKE_DOCKER_PID_FILE": str(docker_pid_path),
        },
    )
    assert event_log.is_file(), (completed.stdout, completed.stderr)
    snapshot_path = Path(_events(event_log)[0]["path"])
    task_root = next(
        parent
        for parent in snapshot_path.parents
        if parent.name.startswith("esp32tap-snapshot-build.")
    )
    try:
        assert marker.is_file(), (completed.stdout, completed.stderr)
        assert completed.returncode == 128 + signal.SIGTERM, (
            completed.stdout,
            completed.stderr,
        )
        assert not task_root.exists()
        docker_pid = int(docker_pid_path.read_text(encoding="utf-8"))
        with pytest.raises(ProcessLookupError):
            os.kill(docker_pid, 0)
        _assert_build_lock_released(root)
    finally:
        _remove_seam_test_resource(task_root, root / "unused")


def test_cleanup_failure_attempts_every_resource_and_pending_signal_wins(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, _, _, _ = fake_worktree
    rs = root / "hardware/Esp32Tap/firmware/esp32_rs"
    resource_path = root.parent / "cleanup-failure-resource.path"
    failure_path = root.parent / "cleanup-failure-staging.path"
    docker_pid_path = root.parent / "cleanup-failure.docker-pid"
    completed = _run(
        fake_worktree,
        only="both",
        extra_env={
            "PYTHONPATH": str(root.parent / "signal-hooks"),
            "FAKE_RESOURCE_SEAM": "live_digest_rename_return",
            "FAKE_RESOURCE_PATH": str(resource_path),
            "FAKE_CLEANUP_FAILURE_PATH": str(failure_path),
            "FAKE_DOCKER_PID_FILE": str(docker_pid_path),
        },
    )
    assert resource_path.is_file(), (completed.stdout, completed.stderr)
    destination = Path(resource_path.read_text(encoding="utf-8"))
    task_root = _seam_cleanup_root(destination, rs)
    try:
        assert failure_path.is_file(), (completed.stdout, completed.stderr)
        failed_staging = Path(failure_path.read_text(encoding="utf-8"))
        assert completed.returncode == 128 + signal.SIGTERM, (
            completed.stdout,
            completed.stderr,
        )
        assert not os.path.lexists(failed_staging)
        assert not list(rs.glob(".snapshot-build-*"))
        assert not task_root.exists()
        docker_pid = int(docker_pid_path.read_text(encoding="utf-8"))
        with pytest.raises(ProcessLookupError):
            os.kill(docker_pid, 0)
        _assert_build_lock_released(root)
    finally:
        _remove_seam_test_resource(destination, rs)
        for staging in rs.glob(".snapshot-build-*"):
            _remove_seam_test_resource(staging, rs)


def test_first_signal_during_publish_is_latched_in_arrival_order(
    fake_worktree: tuple[Path, Path, Path, Path],
) -> None:
    root, _, docker_log, _ = fake_worktree
    rs = root / "hardware" / "Esp32Tap" / "firmware" / "esp32_rs"
    barrier = root.parent / "publish-signal-barrier"
    env = os.environ.copy()
    env.update(
        PATH=f"{root.parent / 'fake-bin'}:{env['PATH']}",
        FAKE_DOCKER_LOG=str(docker_log),
        FAKE_PUBLISH_SIGNAL_BARRIER=str(barrier),
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
    handled = barrier.with_suffix(".term-handled")
    deadline = time.monotonic() + 10
    while not handled.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert handled.exists()
    process.send_signal(signal.SIGHUP)
    barrier.with_suffix(".release").touch()
    stdout, stderr = process.communicate(timeout=20)

    assert process.returncode == 128 + signal.SIGTERM, (stdout, stderr)


def test_signal_latch_is_deferred_before_docker_spawn_has_a_process_handle() -> None:
    run_docker = (
        source().split("def run_docker(", 1)[1].split("\n\nimport contextlib", 1)[0]
    )
    defer = run_docker.index("cancellation_deferred = True")
    guarded_try = run_docker.index("try:", defer)
    spawn = run_docker.index("subprocess.Popen(", guarded_try)
    assigned = run_docker.index("cancellation_deferred = False", spawn)
    pending_check = run_docker.index("if pending_signal is not None:", assigned)

    assert defer < guarded_try < spawn < assigned < pending_check


def _embedded_sdkconfig_selector() -> str:
    text = source()
    marker = "<<'PYSDK'\n"
    assert marker in text
    body = text.split(marker, 1)[1]
    assert "\nPYSDK\n" in body
    return body.split("\nPYSDK\n", 1)[0]


def _select_sdkconfig(
    tmp_path: Path,
    messages: list[dict],
    expected_package: str = "esp-idf-sys-package-id",
    build_status: int = 0,
) -> subprocess.CompletedProcess[str]:
    target = tmp_path / "target"
    target.mkdir(exist_ok=True)
    message_file = tmp_path / "cargo-messages.jsonl"
    message_file.write_text(
        "".join(json.dumps(message) + "\n" for message in messages),
        encoding="utf-8",
    )
    return subprocess.run(
        [
            "python3",
            "-c",
            _embedded_sdkconfig_selector(),
            str(message_file),
            expected_package,
            str(target),
            str(build_status),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _idf_out(tmp_path: Path, fingerprint: str) -> Path:
    return (
        tmp_path
        / "target"
        / "xtensa-esp32s3-espidf"
        / "release"
        / "build"
        / f"esp-idf-sys-{fingerprint}"
        / "out"
    )


def test_sdkconfig_selector_uses_only_current_cargo_build_script_message(
    tmp_path: Path,
) -> None:
    stale = _idf_out(tmp_path, "1" * 16)
    active = _idf_out(tmp_path, "2" * 16)
    stale.mkdir(parents=True)
    active.mkdir(parents=True)
    (stale / "sdkconfig").write_text("STALE\n", encoding="utf-8")
    (active / "sdkconfig").write_text("CURRENT\n", encoding="utf-8")

    completed = _select_sdkconfig(
        tmp_path,
        [
            {
                "reason": "compiler-artifact",
                "package_id": "unrelated",
                "target": {},
            },
            {
                "reason": "build-script-executed",
                "package_id": "esp-idf-sys-package-id",
                "out_dir": str(active),
            },
        ],
    )

    assert completed.returncode == 0, completed.stderr
    assert Path(completed.stdout.strip()) == active / "sdkconfig"
    assert "find " not in source()


def test_sdkconfig_selector_accepts_duplicate_cached_message_for_same_out_dir(
    tmp_path: Path,
) -> None:
    active = _idf_out(tmp_path, "3" * 16)
    active.mkdir(parents=True)
    (active / "sdkconfig").write_text("CURRENT\n", encoding="utf-8")
    message = {
        "reason": "build-script-executed",
        "package_id": "esp-idf-sys-package-id",
        "out_dir": str(active),
    }

    completed = _select_sdkconfig(tmp_path, [message, message])

    assert completed.returncode == 0, completed.stderr
    assert Path(completed.stdout.strip()) == active / "sdkconfig"


def test_sdkconfig_selector_renders_diagnostics_then_propagates_cargo_failure(
    tmp_path: Path,
) -> None:
    active = _idf_out(tmp_path, "6" * 16)
    active.mkdir(parents=True)
    (active / "sdkconfig").write_text("CURRENT\n", encoding="utf-8")
    completed = _select_sdkconfig(
        tmp_path,
        [
            {
                "reason": "compiler-message",
                "message": {"rendered": "error: intentional compile failure\n"},
            },
            {
                "reason": "build-script-executed",
                "package_id": "esp-idf-sys-package-id",
                "out_dir": str(active),
            },
        ],
        build_status=7,
    )

    assert completed.returncode == 7
    assert completed.stdout == ""
    assert "error: intentional compile failure" in completed.stderr
    assert "cargo_status=$?" in source()


@pytest.mark.parametrize("case", ["absent", "ambiguous", "outside", "symlink"])
def test_sdkconfig_selector_fails_closed_without_one_safe_current_result(
    tmp_path: Path, case: str
) -> None:
    first = _idf_out(tmp_path, "4" * 16)
    second = _idf_out(tmp_path, "5" * 16)
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "sdkconfig").write_text("FIRST\n", encoding="utf-8")
    (second / "sdkconfig").write_text("SECOND\n", encoding="utf-8")
    outputs: list[Path] = []
    if case == "ambiguous":
        outputs = [first, second]
    elif case == "outside":
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "sdkconfig").write_text("OUTSIDE\n", encoding="utf-8")
        outputs = [outside]
    elif case == "symlink":
        (first / "sdkconfig").unlink()
        (first / "sdkconfig").symlink_to(second / "sdkconfig")
        outputs = [first]
    messages = [
        {
            "reason": "build-script-executed",
            "package_id": "esp-idf-sys-package-id",
            "out_dir": str(output),
        }
        for output in outputs
    ]

    completed = _select_sdkconfig(tmp_path, messages)

    assert completed.returncode != 0
    assert completed.stdout == ""


def _plan_cleanup_helper() -> str:
    plan = (
        TOOLS.parents[4]
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-30-esp32tap-fast-inner-loop.md"
    ).read_text(encoding="utf-8")
    marker = "<<'PYCLEAN'\n"
    assert marker in plan
    body = plan.split(marker, 1)[1]
    assert "\nPYCLEAN\n" in body
    return body.split("\nPYCLEAN\n", 1)[0]


def _plan_cleanup_shell_functions() -> str:
    plan = (
        TOOLS.parents[4]
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-30-esp32tap-fast-inner-loop.md"
    ).read_text(encoding="utf-8")
    start = plan.index("make_disposable_artifacts_owner_writable() {")
    end = plan.index("\ntrap cleanup_proof EXIT", start)
    return plan[start:end]


def test_plan_cleanup_helper_makes_only_owned_sealed_artifacts_removable() -> None:
    root = Path(tempfile.mkdtemp(prefix="esp32tap-publish-proof.", dir="/tmp"))
    artifacts = root / "hardware" / "Esp32Tap" / "firmware" / "esp32_rs" / ".artifacts"
    generation = artifacts / "prod" / ("a" * 64)
    generation.mkdir(parents=True)
    member = generation / "esp32tap.bin"
    member.write_bytes(b"image")
    member.chmod(0o444)
    generation.chmod(0o555)
    (artifacts / "prod").chmod(0o555)
    artifacts.chmod(0o555)
    try:
        completed = subprocess.run(
            ["python3", "-c", _plan_cleanup_helper(), str(root)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert stat.S_IMODE(member.stat().st_mode) & stat.S_IWUSR
        assert stat.S_IMODE(generation.stat().st_mode) & stat.S_IWUSR
    finally:
        shutil.rmtree(root)


def test_plan_cleanup_helper_rejects_artifact_symlink_without_following() -> None:
    root = Path(tempfile.mkdtemp(prefix="esp32tap-publish-proof.", dir="/tmp"))
    artifacts = root / "hardware" / "Esp32Tap" / "firmware" / "esp32_rs" / ".artifacts"
    artifacts.mkdir(parents=True)
    victim = root.parent / f"{root.name}.victim"
    victim.write_text("protected", encoding="utf-8")
    (artifacts / "escape").symlink_to(victim)
    try:
        completed = subprocess.run(
            ["python3", "-c", _plan_cleanup_helper(), str(root)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert completed.returncode != 0
        assert victim.read_text(encoding="utf-8") == "protected"
    finally:
        (artifacts / "escape").unlink(missing_ok=True)
        shutil.rmtree(root)
        victim.unlink(missing_ok=True)


def test_plan_cleanup_helper_fails_closed_on_unsearchable_directory() -> None:
    root = Path(tempfile.mkdtemp(prefix="esp32tap-publish-proof.", dir="/tmp"))
    artifacts = root / "hardware" / "Esp32Tap" / "firmware" / "esp32_rs" / ".artifacts"
    hidden = artifacts / "hidden"
    hidden.mkdir(parents=True)
    victim = root.parent / f"{root.name}.victim"
    victim.write_text("protected", encoding="utf-8")
    (hidden / "escape").symlink_to(victim)
    hidden.chmod(0)
    try:
        completed = subprocess.run(
            ["python3", "-c", _plan_cleanup_helper(), str(root)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert completed.returncode != 0
        assert victim.read_text(encoding="utf-8") == "protected"
    finally:
        hidden.chmod(0o700)
        (hidden / "escape").unlink(missing_ok=True)
        shutil.rmtree(root)
        victim.unlink(missing_ok=True)


def test_plan_cleanup_helper_pins_nodes_across_validate_mutate_boundary() -> None:
    root = Path(tempfile.mkdtemp(prefix="esp32tap-publish-proof.", dir="/tmp"))
    artifacts = root / "hardware" / "Esp32Tap" / "firmware" / "esp32_rs" / ".artifacts"
    artifacts.mkdir(parents=True)
    member = artifacts / "esp32tap.bin"
    member.write_bytes(b"original")
    member.chmod(0o444)
    victim = root.parent / f"{root.name}.victim"
    victim.write_bytes(b"victim")
    victim.chmod(0o400)
    barrier = root.parent / f"{root.name}.race"
    boundary = "# Validation complete; retained descriptors pin exact nodes."
    helper = _plan_cleanup_helper()
    assert boundary in helper
    instrumented = helper.replace(
        boundary,
        boundary
        + """
    barrier = Path(sys.argv[2])
    barrier.with_suffix(".ready").write_text("ready", encoding="utf-8")
    while not barrier.with_suffix(".release").exists():
        import time
        time.sleep(0.01)
""",
        1,
    )
    process = subprocess.Popen(
        ["python3", "-c", instrumented, str(root), str(barrier)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    ready = barrier.with_suffix(".ready")
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    member.unlink()
    os.link(victim, member)
    barrier.with_suffix(".release").touch()
    stdout, stderr = process.communicate(timeout=10)
    try:
        assert process.returncode != 0, (stdout, stderr)
        assert stat.S_IMODE(victim.stat().st_mode) == 0o400
    finally:
        member.unlink(missing_ok=True)
        shutil.rmtree(root)
        victim.chmod(0o600)
        victim.unlink(missing_ok=True)
        ready.unlink(missing_ok=True)
        barrier.with_suffix(".release").unlink(missing_ok=True)


def test_plan_cleanup_does_not_remove_worktree_after_validation_failure(
    tmp_path: Path,
) -> None:
    root = Path(tempfile.mkdtemp(prefix="esp32tap-publish-proof.", dir="/tmp"))
    artifacts = root / "hardware" / "Esp32Tap" / "firmware" / "esp32_rs" / ".artifacts"
    artifacts.mkdir(parents=True)
    victim = root.parent / f"{root.name}.victim"
    victim.write_text("protected", encoding="utf-8")
    (artifacts / "escape").symlink_to(victim)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    git_called = tmp_path / "git-called"
    _write(
        fake_bin / "git",
        '#!/bin/sh\n: > "$FAKE_GIT_CALLED"\nexit 0\n',
        executable=True,
    )
    env = os.environ.copy()
    env.update(
        PATH=f"{fake_bin}:{env['PATH']}",
        FAKE_GIT_CALLED=str(git_called),
    )
    try:
        completed = subprocess.run(
            [
                "bash",
                "-c",
                _plan_cleanup_shell_functions() + '\nPROOF_WT="$1"\ncleanup_proof',
                "bash",
                str(root),
            ],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert completed.returncode != 0
        assert not git_called.exists()
        assert victim.read_text(encoding="utf-8") == "protected"
    finally:
        (artifacts / "escape").unlink(missing_ok=True)
        shutil.rmtree(root)
        victim.unlink(missing_ok=True)


def test_plan_verifies_disposable_bundles_against_disposable_repo() -> None:
    plan = (
        TOOLS.parents[4]
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-30-esp32tap-fast-inner-loop.md"
    ).read_text(encoding="utf-8")

    assert (
        plan.count(
            'artifact_provenance.py" \\\n  --repo-root "$PROOF_WT" verify --kind '
        )
        == 2
    )
