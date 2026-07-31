"""Repository layout checks for ESP32Tap Rust firmware sources."""

from pathlib import Path
import subprocess


def repository_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=Path(__file__).resolve().parent,
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip())


def test_partition_source_exists_is_tracked_and_not_ignored() -> None:
    root = repository_root()
    source = "hardware/Esp32Tap/firmware/esp32_rs/partitions_esp32tap.csv"

    assert (root / source).is_file()

    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", source],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert tracked.returncode == 0, tracked.stderr

    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "--", source],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert ignored.returncode == 1, ignored.stdout


def test_generated_bundle_directories_have_no_tracked_files() -> None:
    root = repository_root()
    firmware = "hardware/Esp32Tap/firmware/esp32_rs"
    output_directories = (
        f"{firmware}/build/",
        f"{firmware}/build_qemu_test/",
    )

    tracked_outputs = subprocess.run(
        [
            "git",
            "ls-files",
            "--",
            *(f":(top,literal){path}" for path in output_directories),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tracked_outputs == []

    # These similarly named build sources are deliberately outside the two
    # exact output-directory pathspecs and remain ordinary tracked inputs.
    tracked_build_sources = subprocess.run(
        [
            "git",
            "ls-files",
            "--",
            f"{firmware}/**/build.rs",
            f"{firmware}/tools/build*.sh",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    expected_build_sources = {
        f"{firmware}/difftest/build.rs",
        f"{firmware}/esp32tap/build.rs",
        f"{firmware}/tools/build.sh",
        f"{firmware}/tools/build_image.sh",
    }
    assert expected_build_sources.issubset(tracked_build_sources)


def test_generated_bundle_links_and_private_generations_are_ignored() -> None:
    root = repository_root()
    firmware = "hardware/Esp32Tap/firmware/esp32_rs"
    generated_paths = (
        f"{firmware}/build",
        f"{firmware}/build_qemu_test",
        f"{firmware}/.artifacts/example-generation/artifact-manifest.json",
        f"{firmware}/.bench/example-run.json",
    )

    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "--", *generated_paths],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert ignored.returncode == 0, ignored.stderr
    assert ignored.stdout.splitlines() == list(generated_paths)
