from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

import fast_select


RS = "hardware/Esp32Tap/firmware/esp32_rs"
PROGRAM_HOST = (
    "cargo",
    "test",
    "--manifest-path",
    f"{RS}/program_core/Cargo.toml",
    "-q",
)
SAFETY_HOST = (
    "cargo",
    "test",
    "--manifest-path",
    f"{RS}/safety_core/Cargo.toml",
    "-q",
)
DIFFTEST_HOST = (
    "cargo",
    "test",
    "--manifest-path",
    f"{RS}/difftest/Cargo.toml",
    "-q",
)
REQBUDGET_HOST = (
    "cargo",
    "test",
    "--manifest-path",
    f"{RS}/reqbudget/Cargo.toml",
    "-q",
)
BLE_HOST = (
    "cargo",
    "test",
    "--manifest-path",
    f"{RS}/ble_core/Cargo.toml",
    "-q",
)
COACH_HOST = (
    "cargo",
    "test",
    "--manifest-path",
    f"{RS}/coach_core/Cargo.toml",
    "-q",
)
DOCS_HOST = (
    "python3",
    "-m",
    "pytest",
    f"{RS}/tools/test_source_layout.py",
    "-q",
)
BROAD_HOST = ("env", "-C", RS, "bash", "tools/sweep.sh")
QEMU = ("env", "-C", f"{RS}/tools/qemu_scenarios", "python3", "-m", "pytest")


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "selector@example.invalid")
    git(root, "config", "user.name", "Selector Test")
    return root


def write(root: Path, relative: str, content: str = "content\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def commit_all(root: Path, message: str = "fixture") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)


def selection_for(repo: Path, relative: str) -> fast_select.Selection:
    write(repo, relative)
    return fast_select.select(repo)


def test_worktree_uses_exact_nul_safe_git_argv(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    outputs = {
        ("diff", "-M", "--name-status", "-z", "--"): b"M\0docs/guide.md\0",
        ("diff", "-M", "--cached", "--name-status", "-z", "--"): b"",
        ("ls-files", "--others", "--exclude-standard", "-z", "--"): b"",
    }

    def fake(root: Path, args: tuple[str, ...]) -> bytes:
        assert root == tmp_path
        calls.append(args)
        return outputs[args]

    monkeypatch.setattr(fast_select, "_run_git", fake)
    selected = fast_select.select(tmp_path)

    assert calls == list(outputs)
    assert selected.paths == ("docs/guide.md",)
    assert selected.policies == ("docs",)


@pytest.mark.parametrize(
    ("option", "revision", "argv"),
    [
        ("base", "HEAD~2", ("diff", "-M", "--name-status", "-z", "HEAD~2", "--")),
        (
            "range_spec",
            "main..topic",
            ("diff", "-M", "--name-status", "-z", "main..topic", "--"),
        ),
    ],
)
def test_base_and_range_union_exact_authoritative_argv(
    monkeypatch, tmp_path: Path, option: str, revision: str, argv: tuple[str, ...]
) -> None:
    calls: list[tuple[str, ...]] = []
    outputs = {
        ("diff", "-M", "--name-status", "-z", "--"): b"M\0docs/dirty.md\0",
        ("diff", "-M", "--cached", "--name-status", "-z", "--"): b"",
        ("ls-files", "--others", "--exclude-standard", "-z", "--"): b"",
        argv: b"A\0docs/committed.md\0",
    }

    def fake(_root: Path, args: tuple[str, ...]) -> bytes:
        calls.append(args)
        return outputs[args]

    monkeypatch.setattr(fast_select, "_run_git", fake)
    selected = fast_select.select(tmp_path, **{option: revision})

    assert calls == list(outputs)
    assert selected.paths == ("docs/committed.md", "docs/dirty.md")


def test_base_and_range_conflict_is_rejected(repo: Path) -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        fast_select.select(repo, base="HEAD", range_spec="HEAD~1..HEAD")


@pytest.mark.parametrize(
    "revision",
    ["", "-p", "--output=/tmp/pwned", "HEAD\n--help", "left..--right"],
)
def test_revision_cannot_inject_git_options(repo: Path, revision: str) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        fast_select.select(repo, base=revision)


def test_rename_includes_old_and_new_paths(monkeypatch, tmp_path: Path) -> None:
    outputs = {
        ("diff", "-M", "--name-status", "-z", "--"): (
            b"R100\0docs/old guide.md\0docs/new\tguide.md\0"
        ),
        ("diff", "-M", "--cached", "--name-status", "-z", "--"): b"",
        ("ls-files", "--others", "--exclude-standard", "-z", "--"): b"",
    }
    monkeypatch.setattr(fast_select, "_run_git", lambda _root, args: outputs[args])

    selected = fast_select.select(tmp_path)

    assert selected.paths == ("docs/new\tguide.md", "docs/old guide.md")
    assert selected.policies == ("docs",)


def test_explicit_paths_only_augment_nonempty_authoritative_changes(
    monkeypatch, tmp_path: Path
) -> None:
    outputs = {
        ("diff", "-M", "--name-status", "-z", "--"): b"M\0docs/actual.md\0",
        ("diff", "-M", "--cached", "--name-status", "-z", "--"): b"",
        ("ls-files", "--others", "--exclude-standard", "-z", "--"): b"",
    }
    monkeypatch.setattr(fast_select, "_run_git", lambda _root, args: outputs[args])

    selected = fast_select.select(tmp_path, explicit_paths=("docs/hint.md",))

    assert selected.paths == ("docs/actual.md", "docs/hint.md")


def test_explicit_path_cannot_establish_authority(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fast_select, "_run_git", lambda _root, _args: b"")

    selected = fast_select.select(tmp_path, explicit_paths=("docs/hint.md",))

    assert selected.policies == ("broad",)
    assert selected.broad_reason == "no-authoritative-changes"
    assert selected.paths == ()


@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "..",
        "../escape",
        "/absolute",
        "docs/../../escape",
        "docs/./guide.md",
    ],
)
def test_explicit_paths_must_be_normalized_and_non_escaping(
    monkeypatch, tmp_path: Path, path: str
) -> None:
    outputs = iter((b"M\0docs/actual.md\0", b"", b""))
    monkeypatch.setattr(fast_select, "_run_git", lambda _root, _args: next(outputs))

    selected = fast_select.select(tmp_path, explicit_paths=(path,))

    assert selected.policies == ("broad",)
    assert selected.broad_reason == "unsafe-explicit-path"


@pytest.mark.parametrize(
    ("path", "policy", "host", "qemu", "artifacts", "workers"),
    [
        (
            f"{RS}/program_core/src/state.rs",
            "program-host",
            (PROGRAM_HOST,),
            (),
            (),
            {"host": 1, "qemu": 0},
        ),
        (
            f"{RS}/esp32tap/src/net/program.rs",
            "program-control",
            (PROGRAM_HOST, SAFETY_HOST, DIFFTEST_HOST),
            (
                QEMU + ("test_program.py", "-q", "-n", "4"),
                QEMU
                + (
                    "test_reviewer_attacks.py",
                    "-q",
                    "-n",
                    "3",
                    "-k",
                    "console_takeover",
                ),
            ),
            ("qemu",),
            {"host": 1, "qemu": 4},
        ),
        (
            f"{RS}/esp32tap/src/net/http.rs",
            "request-api",
            (REQBUDGET_HOST,),
            (
                QEMU + ("test_http_entry.py", "-q"),
                QEMU
                + (
                    "test_reviewer_attacks.py",
                    "-q",
                    "-n",
                    "3",
                    "-k",
                    "body_policy or unread_declared_body",
                ),
            ),
            ("qemu",),
            {"host": 1, "qemu": 3},
        ),
        (
            f"{RS}/safety_core/src/lib.rs",
            "safety",
            (SAFETY_HOST, DIFFTEST_HOST),
            (
                QEMU + ("test_normal_exit.py", "-q"),
                QEMU + ("test_reviewer_attacks.py", "-q", "-n", "3"),
            ),
            ("qemu",),
            {"host": 1, "qemu": 3},
        ),
        (
            f"{RS}/esp32tap/src/ble/server.rs",
            "ble",
            (BLE_HOST,),
            (
                QEMU + ("test_ble_degraded.py", "-q", "-n", "3"),
                QEMU + ("test_ble_control_point.py", "-q", "-n", "4"),
            ),
            ("qemu",),
            {"host": 1, "qemu": 4},
        ),
        (
            f"{RS}/esp32tap/src/net/coach.rs",
            "coach",
            (COACH_HOST,),
            (QEMU + ("test_coach.py", "-q", "-n", "4"),),
            ("qemu",),
            {"host": 1, "qemu": 4},
        ),
        (
            f"{RS}/esp32tap/src/net/records.rs",
            "storage",
            (),
            (
                QEMU + ("test_records.py", "-q", "-n", "4"),
                QEMU + ("test_store_persistence.py", "-q"),
                QEMU + ("test_store_power_loss.py", "-q", "-n", "4"),
            ),
            ("qemu",),
            {"host": 0, "qemu": 4},
        ),
        (
            "docs/firmware.md",
            "docs",
            (DOCS_HOST,),
            (),
            (),
            {"host": 1, "qemu": 0},
        ),
        (
            f"{RS}/tools/build.sh",
            "broad",
            (BROAD_HOST,),
            (),
            ("production", "qemu"),
            {"host": 1, "qemu": 0},
        ),
    ],
)
def test_exact_policy_table(
    repo: Path,
    path: str,
    policy: str,
    host: tuple[tuple[str, ...], ...],
    qemu: tuple[tuple[str, ...], ...],
    artifacts: tuple[str, ...],
    workers: dict[str, int],
) -> None:
    selected = selection_for(repo, path)

    assert selected.policies == (policy,)
    assert selected.host_argv == host
    assert selected.qemu_argv == qemu
    assert selected.artifact_kinds == artifacts
    assert selected.workers == workers
    assert selected.broad_reason == ("broad-policy-path" if policy == "broad" else None)


def test_control_exact_paths_win_over_request_api_prefix(repo: Path) -> None:
    selected = selection_for(repo, f"{RS}/esp32tap/src/control.rs")

    assert selected.policies == ("program-control",)


def test_markdown_under_focused_executable_tree_is_not_docs(repo: Path) -> None:
    selected = selection_for(repo, f"{RS}/program_core/README.md")

    assert selected.policies == ("program-host",)


def test_multiple_focused_policies_union_and_deduplicate_gates(repo: Path) -> None:
    write(repo, f"{RS}/program_core/src/state.rs")
    write(repo, f"{RS}/safety_core/src/lib.rs")
    selected = fast_select.select(repo)

    assert selected.policies == ("program-host", "safety")
    assert selected.host_argv == (PROGRAM_HOST, SAFETY_HOST, DIFFTEST_HOST)
    assert selected.artifact_kinds == ("qemu",)


def test_broad_policy_has_first_precedence_over_focused(repo: Path) -> None:
    write(repo, f"{RS}/program_core/src/state.rs")
    write(repo, "mobile/client.kt")

    selected = fast_select.select(repo)

    assert selected.policies == ("broad",)
    assert selected.host_argv == (BROAD_HOST,)
    assert selected.broad_reason == "path-outside-esp32-rs"


@pytest.mark.parametrize(
    "symbol",
    [
        "httpd_register_uri_handler",
        "httpd_uri_t",
        "register_program_handlers",
    ],
)
@pytest.mark.parametrize("mode", ["unstaged", "staged", "base"])
def test_route_registration_changed_hunk_forces_broad(
    repo: Path, symbol: str, mode: str
) -> None:
    path = write(
        repo,
        f"{RS}/esp32tap/src/net/program.rs",
        "fn focused() {}\n",
    )
    commit_all(repo)
    path.write_text(f"fn focused() {{ {symbol}(); }}\n", encoding="utf-8")
    kwargs: dict[str, str] = {}
    if mode == "staged":
        git(repo, "add", path.relative_to(repo).as_posix())
    elif mode == "base":
        commit_all(repo, "route change")
        kwargs["base"] = "HEAD~1"

    selected = fast_select.select(repo, **kwargs)

    assert selected.policies == ("broad",)
    assert selected.broad_reason == "route-registration-diff"


def test_unchanged_route_symbol_outside_changed_hunk_remains_focused(repo: Path) -> None:
    path = write(
        repo,
        f"{RS}/esp32tap/src/net/program.rs",
        "fn routes() { httpd_uri_t(); }\nfn focused() {}\n",
    )
    commit_all(repo)
    path.write_text(
        "fn routes() { httpd_uri_t(); }\nfn focused() { changed(); }\n",
        encoding="utf-8",
    )

    assert fast_select.select(repo).policies == ("program-control",)


def test_untracked_source_with_route_registration_forces_broad(repo: Path) -> None:
    write(
        repo,
        f"{RS}/esp32tap/src/net/new.rs",
        "fn new_route() { register_new_handlers(); }\n",
    )

    selected = fast_select.select(repo)

    assert selected.policies == ("broad",)
    assert selected.broad_reason == "route-registration-diff"


@pytest.mark.parametrize(
    "bad_output",
    [
        b"M\0unterminated",
        b"R100\0only-old\0",
        b"Rxx\0old\0new\0",
        b"Z\0unknown\0",
        b"M docs/not-nul.md\n",
        b"M\0../escape\0",
    ],
)
def test_malformed_git_name_status_fails_closed_to_broad(
    monkeypatch, tmp_path: Path, bad_output: bytes
) -> None:
    monkeypatch.setattr(fast_select, "_run_git", lambda _root, _args: bad_output)

    selected = fast_select.select(tmp_path)

    assert selected.policies == ("broad",)
    assert selected.broad_reason == "git-enumeration-failed"


def test_git_process_failure_fails_closed_to_broad(monkeypatch, tmp_path: Path) -> None:
    def fail(_root: Path, _args: tuple[str, ...]) -> bytes:
        raise fast_select.GitFailure("failed")

    monkeypatch.setattr(fast_select, "_run_git", fail)

    selected = fast_select.select(tmp_path)

    assert selected.policies == ("broad",)
    assert selected.broad_reason == "git-enumeration-failed"


def test_git_output_limit_terminates_producer_before_timeout(
    monkeypatch, tmp_path: Path
) -> None:
    binary = tmp_path / "bin"
    binary.mkdir()
    fake_git = binary / "git"
    fake_git.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "chunk = b'x' * 65536\n"
        "while True:\n"
        "    os.write(1, chunk)\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(fast_select, "_GIT_TIMEOUT_SECONDS", 2.0)

    started = time.monotonic()
    with pytest.raises(fast_select.GitFailure, match="exceeded"):
        fast_select._run_git(tmp_path, ("ignored",))

    assert time.monotonic() - started < 1.5


def test_unencodable_explicit_path_fails_closed(
    monkeypatch, tmp_path: Path
) -> None:
    outputs = iter((b"M\0docs/actual.md\0", b"", b""))
    monkeypatch.setattr(fast_select, "_run_git", lambda _root, _args: next(outputs))

    selected = fast_select.select(tmp_path, explicit_paths=("docs/\ud800.md",))

    assert selected.policies == ("broad",)
    assert selected.broad_reason == "unsafe-explicit-path"


def test_nul_safe_filenames_and_non_utf8_are_preserved(repo: Path) -> None:
    names = [
        "docs/line\nbreak.md",
        "docs/tab\tname.md",
        "docs/-leading.md",
        os.fsdecode(b"docs/nonutf8-\xff.md"),
    ]
    for name in names:
        raw = os.fsencode(repo) + b"/" + os.fsencode(name)
        os.makedirs(os.path.dirname(raw), exist_ok=True)
        fd = os.open(raw, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)

    selected = fast_select.select(repo)

    assert selected.paths == tuple(sorted(names, key=os.fsencode))
    assert selected.policies == ("docs",)
    assert "\\udcff" in selected.to_json()


def test_symlinked_script_path_discovers_physical_repository_root(
    repo: Path, tmp_path: Path
) -> None:
    script = write(repo, f"{RS}/tools/fast_select.py", "placeholder\n")
    commit_all(repo)
    link = tmp_path / "selector-link"
    link.symlink_to(script)

    assert fast_select.discover_repo_root(link) == repo.resolve()


def test_selection_json_is_canonical_and_deterministic(repo: Path) -> None:
    write(repo, "docs/z.md")
    write(repo, "docs/a.md")
    selected = fast_select.select(repo)

    encoded = selected.to_json()

    assert encoded == json.dumps(
        {
            "artifact_kinds": [],
            "broad_reason": None,
            "host_argv": [list(DOCS_HOST)],
            "paths": ["docs/a.md", "docs/z.md"],
            "policies": ["docs"],
            "qemu_argv": [],
            "version": 1,
            "workers": {"host": 1, "qemu": 0},
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    with pytest.raises(TypeError):
        selected.workers["host"] = 99


def test_every_tracked_real_repository_path_has_a_named_or_broad_policy() -> None:
    root = Path(
        git(Path(__file__).resolve().parent, "rev-parse", "--show-toplevel")
        .decode()
        .strip()
    )
    paths = [
        os.fsdecode(raw)
        for raw in git(root, "ls-files", "-z").split(b"\0")
        if raw
    ]

    for path in paths:
        policy, _reason = fast_select.classify_path(path)
        assert policy in {
            "program-host",
            "program-control",
            "request-api",
            "safety",
            "ble",
            "coach",
            "storage",
            "docs",
            "broad",
        }, path
