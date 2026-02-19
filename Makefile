CXX = g++
CXXFLAGS = -std=c++20 -fno-exceptions -fno-rtti -Wall -Wextra -O2 -pthread \
           -Wno-format-truncation -MMD -MP
INCLUDES = -Isrc -Ithird_party/rapidjson -Ithird_party
LDFLAGS = -lpigpio -lrt -pthread

# Source files (production)
SRCS = src/treadmill_io.cpp src/kv_protocol.cpp src/ipc_protocol.cpp \
       src/mode_state.cpp src/ipc_server.cpp
OBJS = $(SRCS:.cpp=.o)

# Shared library sources for tests (no gpio_pigpio.h, no main())
TEST_LIB_SRCS = src/kv_protocol.cpp src/ipc_protocol.cpp \
                src/mode_state.cpp src/ipc_server.cpp
TEST_LIB_OBJS = $(patsubst src/%.cpp,src/%.test.o,$(TEST_LIB_SRCS))

# Individual test binaries (each has its own main via doctest)
TEST_NAMES = test_kv_protocol test_ipc_protocol test_ring_buffer \
             test_mode_state test_emulation test_integration \
             test_ipc_server test_controller_live
TEST_BINS = $(addprefix src/tests/,$(TEST_NAMES))

TARGET = treadmill_io

all: $(TARGET)

# Production binary (links libpigpio, runs on Pi)
$(TARGET): $(OBJS)
	$(CXX) $(CXXFLAGS) -o $@ $^ $(LDFLAGS)

# Build and run all tests (stops treadmill_io if running to free the socket)
test: $(TEST_BINS)
	@sudo systemctl stop treadmill_io 2>/dev/null || true
	@sudo rm -f /tmp/treadmill_io.sock
	@failed=0; for t in $(TEST_BINS); do echo "=== Running $$t ==="; ./$$t || { failed=1; break; }; done; \
	 sudo systemctl start treadmill_io 2>/dev/null || true; \
	 [ $$failed -eq 0 ] && echo "=== All tests passed ===" || exit 1

# Individual test binaries
src/tests/test_kv_protocol: src/tests/test_kv_protocol.o src/kv_protocol.test.o
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

src/tests/test_ipc_protocol: src/tests/test_ipc_protocol.o src/kv_protocol.test.o src/ipc_protocol.test.o
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

src/tests/test_ring_buffer: src/tests/test_ring_buffer.o
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

src/tests/test_mode_state: src/tests/test_mode_state.o src/mode_state.test.o
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

src/tests/test_emulation: src/tests/test_emulation.o src/kv_protocol.test.o src/mode_state.test.o
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

src/tests/test_integration: src/tests/test_integration.o $(TEST_LIB_OBJS)
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

src/tests/test_ipc_server: src/tests/test_ipc_server.o $(TEST_LIB_OBJS)
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

src/tests/test_controller_live: src/tests/test_controller_live.o $(TEST_LIB_OBJS)
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

# Production object files
src/%.o: src/%.cpp
	$(CXX) $(CXXFLAGS) $(INCLUDES) -c -o $@ $<

# Test library object files (different suffix to avoid conflict with production .o)
src/%.test.o: src/%.cpp
	$(CXX) $(CXXFLAGS) $(INCLUDES) -DTESTING -c -o $@ $<

# Test object files
src/tests/%.o: src/tests/%.cpp
	$(CXX) $(CXXFLAGS) $(INCLUDES) -c -o $@ $<

PI_HOST = rpi
VENV_DIR = .venv

# Deploy Python server + static assets to Pi
deploy:
	./deploy.sh

# Deploy to Pi, build, restart binary, run hardware tests
test-pi: test
	@echo "=== Deploying to Pi ==="
	rsync -az src/ $(PI_HOST):~/src/
	rsync -az third_party/ $(PI_HOST):~/third_party/
	rsync -az tests/ $(PI_HOST):~/tests/
	scp Makefile gpio.json treadmill_client.py pyproject.toml $(PI_HOST):~/
	@echo "=== Building on Pi ==="
	ssh $(PI_HOST) 'cd ~ && make'
	@echo "=== Deploying treadmill_io ==="
	scp treadmill_io $(PI_HOST):/tmp/treadmill_io
	scp treadmill_io.service $(PI_HOST):/tmp/treadmill_io.service
	ssh $(PI_HOST) 'sudo systemctl stop treadmill_io 2>/dev/null || true && sudo install -m 755 /tmp/treadmill_io /usr/local/bin/ && sudo cp /tmp/treadmill_io.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now treadmill_io'
	sleep 2
	ssh $(PI_HOST) 'systemctl is-active treadmill_io' || (echo "ERROR: treadmill_io failed to start"; ssh $(PI_HOST) 'journalctl -u treadmill_io -n 10 --no-pager'; exit 1)
	@echo "=== Running hardware tests ==="
	ssh $(PI_HOST) 'cd ~ && source $(VENV_DIR)/bin/activate && pytest tests/test_hardware_integration.py -v -s -m hardware'

# Full pre-commit gate: local unit tests + Pi hardware tests
test-all: test test-pi

# --- FTMS Bluetooth daemon ---
FTMS_TARGET = aarch64-unknown-linux-gnu
FTMS_BIN = ftms/target/$(FTMS_TARGET)/release/ftms-daemon

ftms:
	cd ftms && cross build --release --target $(FTMS_TARGET)

deploy-ftms: ftms
	ssh $(PI_HOST) 'sudo systemctl stop ftms 2>/dev/null || true'
	scp $(FTMS_BIN) $(PI_HOST):/tmp/ftms-daemon
	scp ftms/ftms.service $(PI_HOST):/tmp/ftms.service
	ssh $(PI_HOST) 'sudo install -m 755 /tmp/ftms-daemon /usr/local/bin/ && sudo cp /tmp/ftms.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now ftms'

test-ftms:
	cd ftms && cargo test

clean:
	rm -f $(TARGET) $(TEST_BINS) src/*.o src/*.test.o src/tests/*.o
	rm -f src/*.d src/*.test.d src/tests/*.d
	rm -f src/*.gcda src/*.gcno src/tests/*.gcda src/tests/*.gcno *.gcov

# Auto-generated header dependencies
-include src/*.d src/*.test.d src/tests/*.d

.PHONY: all clean test test-pi test-all deploy ftms deploy-ftms test-ftms
