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
