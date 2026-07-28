"""Collection rules for esp32_rs/tools/ when pytest is invoked from ABOVE
this directory (the mandated repo gate, `python3 -m pytest hardware/Esp32Tap`).

`qemu_harness/` here is a byte-identical copy of the committed C++ harness
(see tools/verify_harness_copy.py). Two of its files — `conftest.py` and
`test_scenarios.py` / `test_default_build.py` / `test_encoders.py` — share
their basenames with the originals under `firmware/esp32/tools/qemu_harness/`,
and neither directory is a package. pytest's rootdir-relative import of
same-named modules from two directories is an error ("import file mismatch"),
so a repo-root run must collect exactly one of them. It collects the C++ one,
which is the tree the mandated gate has always covered.

This is a COLLECTION rule, not a gate weakening: the copy is run in full by
`tools/run_harness.sh` (S1-S7 + encoder parity against the Rust image), which
sets rootdir to the copy itself and therefore never loads this file. The
Rust-image scenarios that ARE unique by basename — `qemu_scenarios/` (S8) —
stay collected by the repo gate.
"""

from __future__ import annotations

collect_ignore = ["qemu_harness"]
