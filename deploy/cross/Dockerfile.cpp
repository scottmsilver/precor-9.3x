# aarch64 C++ cross toolchain for treadmill_io. Mirrors the cross/Rust
# pattern: a pinned base, the aarch64 g++ cross compiler, and libpigpio
# built from source for aarch64 (libpigpio-dev is not in Debian main;
# it lives in the Raspberry Pi OS repo which cannot satisfy libc6:arm64
# in a Debian multiarch container). Building from source is the
# correct approach: one RUN layer, pure Debian deps, fully reproducible.
FROM debian:bookworm-slim

# PIGPIO_VERSION matches the Pi's installed version (1.79, git tag v79).
ARG PIGPIO_VERSION=79

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        g++-aarch64-linux-gnu \
        binutils-aarch64-linux-gnu \
        make \
        curl \
        ca-certificates && \
    # Build libpigpio for aarch64 and install into the cross sysroot.
    curl -fsSL "https://github.com/joan2937/pigpio/archive/refs/tags/v${PIGPIO_VERSION}.tar.gz" \
        | tar -xz && \
    cd "pigpio-${PIGPIO_VERSION}" && \
    make CC=aarch64-linux-gnu-gcc \
         STRIP=aarch64-linux-gnu-strip \
         SIZE=true && \
    install -m 0644 pigpio.h /usr/aarch64-linux-gnu/include/ && \
    install -m 0755 libpigpio.so.1 /usr/aarch64-linux-gnu/lib/ && \
    ln -fs libpigpio.so.1 /usr/aarch64-linux-gnu/lib/libpigpio.so && \
    cd / && rm -rf "/pigpio-${PIGPIO_VERSION}" && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /src
# The build is driven by the repo's cpp/Makefile with CXX overridden.
CMD ["make", "-C", "cpp", "CXX=aarch64-linux-gnu-g++"]
