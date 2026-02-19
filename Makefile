PI_HOST ?= rpi
VENV_DIR ?= .venv
FTMS_TARGET = aarch64-unknown-linux-gnu
FTMS_BIN = ftms/target/$(FTMS_TARGET)/release/ftms-daemon

.PHONY: all clean test stage deploy ftms deploy-ftms test-ftms test-pi test-all

all:
	$(MAKE) -C src

test:
	$(MAKE) -C src test

clean:
	$(MAKE) -C src clean
	rm -rf build/

stage: all
	deploy/deploy.sh --stage-only

deploy:
	deploy/deploy.sh

ftms:
	cd ftms && cross build --release --target $(FTMS_TARGET)

deploy-ftms: ftms
	ssh $(PI_HOST) 'sudo systemctl stop ftms 2>/dev/null || true'
	scp $(FTMS_BIN) $(PI_HOST):/tmp/ftms-daemon
	ssh $(PI_HOST) 'sudo install -m 755 /tmp/ftms-daemon /usr/local/bin/ && sudo systemctl restart ftms'

test-ftms:
	cd ftms && cargo test

# Deploy to Pi, build, restart binary, run hardware tests
test-pi: test
	@echo "=== Deploying to Pi ==="
	deploy/deploy.sh
	@echo "=== Running hardware tests ==="
	ssh $(PI_HOST) 'cd ~/treadmill && source $(VENV_DIR)/bin/activate && pytest tests/test_hardware_integration.py -v -s -m hardware'

test-all: test test-pi
