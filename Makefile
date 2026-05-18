PI_HOST ?= rpi-zero
VENV_DIR ?= .venv
FTMS_TARGET = aarch64-unknown-linux-gnu
FTMS_BIN = rust/ftms/target/$(FTMS_TARGET)/release/ftms-daemon
HRM_TARGET = aarch64-unknown-linux-gnu
HRM_BIN = rust/hrm/target/$(HRM_TARGET)/release/hrm-daemon
CPP_CROSS_IMG = treadmill-cross-cpp

.PHONY: all clean test stage deploy image cross cross-cpp ftms deploy-ftms \
        test-ftms test-ftms-ble hrm deploy-hrm test-hrm test-pi test-all \
        ship-check ship-check-nobelt

all:
	$(MAKE) -C cpp

test:
	$(MAKE) -C cpp test

clean:
	$(MAKE) -C cpp clean
	rm -rf build/

# Build the aarch64 treadmill_io inside the pinned cross container.
cross-cpp:
	docker build -t $(CPP_CROSS_IMG) -f deploy/cross/Dockerfile.cpp deploy/cross
	mkdir -p build
	docker run --rm --user "$(shell id -u):$(shell id -g)" -v "$(CURDIR)":/src -w /src $(CPP_CROSS_IMG) \
		make -C cpp CXX=aarch64-linux-gnu-g++
	test -f build/treadmill_io   # cpp/Makefile writes here (project build-dir convention)

# Build all three aarch64 binaries (C++ + both Rust daemons) off-Pi.
cross: cross-cpp ftms hrm
	mkdir -p build
	cp $(FTMS_BIN) build/ftms-daemon
	cp $(HRM_BIN) build/hrm-daemon

stage: cross
	deploy/deploy.sh --stage-only

# Bake a flashable full-appliance image: cross-build everything, stage it,
# then run the audited userspace image builder which carries build/ +
# manifest into the .img via the provisioning toolkit.
image: cross
	deploy/deploy.sh --stage-only
	provisioning/dietpi/build-image.sh
	@echo "Image built. Flash with provisioning/dietpi/build-image.sh --flash /dev/sdX (operator)."

# `make deploy` must still work (CLAUDE.md + Task 9 docs rely on it). It now
# depends on `cross` so the manifest's binaries exist before deploy.sh rsyncs.
deploy: cross
	deploy/deploy.sh

ftms:
	cd rust/ftms && cross build --release --target $(FTMS_TARGET)

deploy-ftms: ftms
	ssh $(PI_HOST) 'sudo systemctl stop ftms 2>/dev/null || true'
	scp $(FTMS_BIN) $(PI_HOST):/tmp/ftms-daemon
	ssh $(PI_HOST) 'sudo install -m 755 /tmp/ftms-daemon /usr/local/bin/ && sudo systemctl restart ftms'

test-ftms:
	cd rust/ftms && cargo test

test-ftms-ble:
	ssh $(PI_HOST) 'sudo bash ~/treadmill/rust/ftms/tests/ble_integration.sh'

hrm:
	cd rust/hrm && cross build --release --target $(HRM_TARGET)

deploy-hrm: hrm
	ssh $(PI_HOST) 'sudo systemctl stop hrm 2>/dev/null || true'
	scp $(HRM_BIN) $(PI_HOST):/tmp/hrm-daemon
	ssh $(PI_HOST) 'sudo install -m 755 /tmp/hrm-daemon /usr/local/bin/ && sudo systemctl restart hrm'

test-hrm:
	cd rust/hrm && cargo test

# Deploy to Pi, build, restart binary, run hardware tests
test-pi: test
	@echo "=== Deploying to Pi ==="
	deploy/deploy.sh
	@echo "=== Running hardware tests ==="
	ssh $(PI_HOST) 'cd ~/treadmill && source $(VENV_DIR)/bin/activate && pytest python/tests/test_hardware_integration.py -v -s -m hardware'

test-all: test test-pi

# Live "ready to ship?" acceptance gate against PI_HOST + treadmill.
# ship-check drives the belt (L1/L2/L4) — belt MUST be clear.
# ship-check-nobelt runs only the non-moving checks (no treadmill needed).
ship-check:
	deploy/ship-check.sh --belt-clear

ship-check-nobelt:
	deploy/ship-check.sh --no-belt
