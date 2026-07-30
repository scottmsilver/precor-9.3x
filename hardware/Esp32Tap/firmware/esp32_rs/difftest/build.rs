//! Compile the COMMITTED, UNMODIFIED C++ safety core plus the extern "C" shim,
//! and link them into the differential test binary.
//!
//! Flags are copied VERBATIM from `firmware/esp32/host/Makefile` so the code
//! under differential test is byte-for-byte the shipped translation units:
//!   -std=c++20 -fno-exceptions -fno-rtti -O2
//!
//! Hand-rolled rather than using the `cc` crate — per the dependency policy,
//! anything this small is ours, and we need exact flag control regardless.

use std::path::PathBuf;
use std::process::Command;

fn main() {
    let manifest = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    let out_dir = PathBuf::from(std::env::var("OUT_DIR").unwrap());
    let core = manifest
        .join("../../esp32/components/portable_core")
        .canonicalize()
        .expect("committed C++ portable_core must exist");

    let sources = [
        core.join("protocol/kv_protocol.cpp"),
        core.join("engine/mode_state.cpp"),
        core.join("safety/safety_controller.cpp"),
        manifest.join("cpp_shim/shim.cpp"),
    ];

    let mut objs = Vec::new();
    for src in &sources {
        println!("cargo:rerun-if-changed={}", src.display());
        let stem = src.file_stem().unwrap().to_string_lossy().to_string();
        let obj = out_dir.join(format!("{stem}.o"));
        let status = Command::new(std::env::var("CXX").unwrap_or_else(|_| "g++".into()))
            .args([
                "-std=c++20",
                "-fno-exceptions",
                "-fno-rtti",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-O2",
                "-fPIC",
                "-c",
            ])
            .arg("-I")
            .arg(&core)
            .arg("-o")
            .arg(&obj)
            .arg(src)
            .status()
            .expect("failed to run the C++ compiler");
        assert!(status.success(), "C++ compile failed for {}", src.display());
        objs.push(obj);
    }

    let lib = out_dir.join("libcppcore.a");
    let _ = std::fs::remove_file(&lib);
    let status = Command::new("ar")
        .arg("crs")
        .arg(&lib)
        .args(&objs)
        .status()
        .expect("failed to run ar");
    assert!(status.success(), "ar failed");

    println!("cargo:rustc-link-search=native={}", out_dir.display());
    println!("cargo:rustc-link-lib=static=cppcore");
    println!("cargo:rustc-link-lib=dylib=stdc++");
    println!("cargo:rerun-if-changed=cpp_shim/shim.cpp");
}
