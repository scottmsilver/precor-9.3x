# `firmware/esp32/` — NOT a firmware. Do not delete it.

The C++ **application is retired.** The device firmware is Rust, in
[`../esp32_rs/`](../esp32_rs/README.md). What remains here is not a second
firmware and cannot be built or flashed: `main/`, `CMakeLists.txt` and the
`sdkconfig*` files are gone. The full C++ application — including the abandoned
native-server tier, whose last verification found that an unauthenticated LAN
client could reboot the device in ~15 requests — is preserved on the branch
`archive/esp32tap-cpp-net-tier` if its API archaeology is ever wanted.

**Everything still here is load-bearing for the Rust tree's gates**, depended on
by name. Removing any of it breaks `../esp32_rs/tools/sweep.sh`:

| Path | Why it must stay |
|---|---|
| `components/portable_core/` | The **differential oracle.** `esp32_rs/difftest/build.rs` compiles `protocol/kv_protocol.cpp`, `engine/mode_state.cpp` and `safety/safety_controller.cpp` in-process and diffs them op-for-op against the Rust safety core. This is the evidence the Rust port is *equivalent*, not merely tested. Deleting it does not retire the C++ core — it deletes the proof. Per-file origin hashes are in its `PROVENANCE.md`. |
| `host/` | `esp32_rs/tools/check_case_parity.py` reads `host/tests/` to assert 148/148 cases are ported 1:1 — and these are the tests that make the oracle above trustworthy. An unvalidated oracle is a weak oracle. |
| `tools/qemu_harness/` | The **anchor** for `verify_harness_copy.py`: LEG 2 compares this directory against `git show HEAD:`, and LEG 1 compares the Rust copy against *this*. The Rust harness is measured against it. |
| `tools/qemu_smoke.sh` | `esp32_rs/tools/qemu_smoke.sh` is a **symlink** to this file. |
| `tools/check_pins.py` | `esp32_rs/tools/check_pins.py` is a **symlink** to this file, and the `build` gate runs it. |
| `partitions_esp32tap.csv` | `esp32_rs/partitions_esp32tap.csv` is a **symlink** to this file, and `esp32_rs/sdkconfig.defaults` names that path in `CONFIG_PARTITION_TABLE_CUSTOM_FILENAME`. Deleting it breaks the Rust build. |

## The mistake this README exists to prevent

On 2026-07-29 an "archive the abandoned C++ tier" commit moved untracked and
symlinked files off the branch without checking what still pointed at them. Two
gates broke. One was loud — `harnesslock` went red, and three agents in a row
correctly reported it as "pre-existing at HEAD", HEAD being where it had just
been put. The other was silent and worse: `esp32_rs/sdkconfig.defaults.qemu` was
a symlink into this directory, and once it dangled `SDKCONFIG_DEFAULTS` resolved
to nothing, so the QEMU test image quietly lost its OpenCores NIC, its
polled-MPI override and its panic printing. Nothing noticed, because IDF reuses
a stale generated `sdkconfig` until something moves the build hash.

Untracked and symlinked files can be load-bearing. Before tidying anything here,
look for links pointing in, and run the sweep afterwards:

```bash
find ../esp32_rs -type l -printf '%p -> %l\n'
```

## Running what remains

```bash
make -C host test           # the C++ core's own suite — validates the oracle
make -C host check-no-rtti  # the -fno-rtti guard, over components/ only now
```

`managed_components/` and the `build*/` directories are leftover container
output: root-owned, gitignored and inert. They are still here only because they
are not ours to remove.
