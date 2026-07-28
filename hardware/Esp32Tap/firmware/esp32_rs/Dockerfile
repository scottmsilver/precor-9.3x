# Esp32Tap Rust toolchain — extends the SAME pinned ESP-IDF image the C++
# core and tools/qemu_smoke.sh already use, so that:
#   * esp-idf-sys builds against the container's IDF (v5.5.5-260-gdd8d7946bb4)
#     instead of vendoring a second, different IDF checkout;
#   * the esp-QEMU fork (esp_develop_9.2.2_20260417) that gates the C++
#     image is present in the very same image that produced the Rust one.
#
# Pinned inputs (change deliberately, never float):
#   base    espressif/idf:release-v5.5   (IDF v5.5.5-260-gdd8d7946bb4)
#   espup   v0.15.1
#   rust    1.90.0.0  (Espressif Xtensa fork — channel name "esp")
#   ldproxy 0.3.4
FROM espressif/idf:release-v5.5

ARG ESPUP_VERSION=v0.15.1
ARG ESP_RUST_VERSION=1.90.0.0
ARG LDPROXY_VERSION=0.3.4

USER root
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        libuv1 pkg-config libudev-dev \
    && rm -rf /var/lib/apt/lists/*

ENV RUSTUP_HOME=/opt/rust/rustup \
    CARGO_HOME=/opt/rust/cargo
ENV PATH=/opt/rust/cargo/bin:$PATH

# 1. Upstream rustup + a stable host toolchain (needed to build the host
#    tools ldproxy/espflash; the Xtensa target itself comes from espup).
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
      | sh -s -- -y --default-toolchain stable --profile minimal --no-modify-path

# 2. espup installs the Espressif Xtensa rustc fork as toolchain "esp".
#    Xtensa is NOT in upstream rustc — this fork is mandatory for the
#    ESP32-S3 (LX7) target. RISC-V ESP chips would not need it.
RUN curl -sSfL \
      https://github.com/esp-rs/espup/releases/download/${ESPUP_VERSION}/espup-x86_64-unknown-linux-gnu \
      -o /usr/local/bin/espup \
    && chmod +x /usr/local/bin/espup \
    && espup install \
         --toolchain-version ${ESP_RUST_VERSION} \
         --targets esp32s3 \
         --std \
         --export-file /opt/rust/esp-export.sh \
    && rm -rf /opt/rust/cargo/registry/cache

# 3. ldproxy: esp-idf-sys links the final binary through the IDF's own
#    linker invocation; ldproxy is the shim cargo calls as the linker.
RUN cargo +stable install ldproxy --version ${LDPROXY_VERSION} --locked \
    && rm -rf /opt/rust/cargo/registry

# 4. PIN THE IDF TO AN IMMUTABLE TAG.
#
#    The espressif/idf:release-v5.5 tag is NOT pinned — it tracks the
#    release/v5.5 BRANCH. As shipped today it is v5.5.5-260-gdd8d7946bb4
#    (branch head, 2026-07-23), i.e. 260 commits past the v5.5.5 tag.
#
#    That drift is fatal to the Rust path: commit b70607c08b7 added the
#    field `unaligned_multi_block_rw_max_chunk_size` to `sdmmc_host_t`
#    (first tagged in v5.5.5), and esp-idf-hal 0.46.2's src/sd.rs builds
#    that struct with a Rust struct literal. Rust struct literals must be
#    exhaustive, so the crate fails to compile:
#        error[E0063]: missing field `unaligned_multi_block_rw_max_chunk_size`
#    `pub mod sd;` is NOT feature-gated, so the module cannot be turned off.
#
#    v5.5.4 (2026-03-24) is the newest tag that predates the field.
ARG IDF_REF=v5.5.4
RUN cd /opt/esp/idf \
    && git fetch --tags --depth=1 origin tag ${IDF_REF} 2>/dev/null || true \
    && git checkout --detach ${IDF_REF} \
    && git submodule update --init --recursive --depth=1 \
    && ./install.sh esp32s3 \
    && git describe --tags

#    Reuse that pinned IDF rather than letting esp-idf-sys clone its own.
#    This is what keeps the Rust image, the C++ image, and the QEMU gate
#    on ONE known IDF revision.
ENV ESP_IDF_TOOLS_INSTALL_DIR=fromenv \
    IDF_PATH=/opt/esp/idf \
    LIBCLANG_PATH=/opt/rust/rustup/toolchains/esp/xtensa-esp32-elf-clang/esp-19.1.2_20250225/esp-clang/lib

# 5. Checking the IDF out at v5.5.4 makes idf_tools consider the image's
#    qemu-xtensa "not used by the active ESP-IDF version", so export.sh
#    drops it from PATH. The binary is still on disk and is the exact
#    esp_develop_9.2.2_20260417 build the C++ gate runs, so link it into
#    /usr/local/bin. tools/qemu_smoke.sh then works against this image
#    completely unmodified.
RUN ln -sf "$(ls -d /opt/esp/tools/qemu-xtensa/*/qemu/bin/qemu-system-xtensa | head -1)" \
        /usr/local/bin/qemu-system-xtensa \
    && qemu-system-xtensa --version | head -1

RUN chmod -R a+rwX /opt/rust
USER root
WORKDIR /project
