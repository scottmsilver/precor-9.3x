CXX = g++
CXXFLAGS = -std=c++20 -fno-exceptions -fno-rtti -Wall -Wextra -O2 -pthread \
           -Wno-format-truncation -MMD -MP
INCLUDES = -I. -I../third_party/rapidjson -I../third_party
LDFLAGS = -lpigpio -lrt -pthread

BUILD = ../build
OBJ_DIR = $(BUILD)/obj
OBJ_TEST_DIR = $(BUILD)/obj_test
TEST_DIR = $(BUILD)/tests

# Source files (production)
SRCS = treadmill_io.cpp kv_protocol.cpp ipc_protocol.cpp \
       mode_state.cpp ipc_server.cpp
OBJS = $(patsubst %.cpp,$(OBJ_DIR)/%.o,$(SRCS))

# Shared library sources for tests (no gpio_pigpio.h, no main())
TEST_LIB_SRCS = kv_protocol.cpp ipc_protocol.cpp \
                mode_state.cpp ipc_server.cpp
TEST_LIB_OBJS = $(patsubst %.cpp,$(OBJ_TEST_DIR)/%.test.o,$(TEST_LIB_SRCS))

# Individual test binaries (each has its own main via doctest)
TEST_NAMES = test_kv_protocol test_ipc_protocol test_ring_buffer \
             test_mode_state test_emulation test_integration \
             test_ipc_server test_controller_live
TEST_BINS = $(addprefix $(TEST_DIR)/,$(TEST_NAMES))

TARGET = $(BUILD)/treadmill_io

all: $(TARGET)

# Production binary (links libpigpio, runs on Pi)
$(TARGET): $(OBJS) | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $^ $(LDFLAGS)

# Build and run all tests (stops treadmill_io if running to free the socket)
test: $(TEST_BINS)
	@sudo systemctl stop treadmill-io 2>/dev/null || true
	@sudo rm -f /tmp/treadmill_io.sock
	@failed=0; for t in $(TEST_BINS); do echo "=== Running $$t ==="; ./$$t || { failed=1; break; }; done; \
	 sudo systemctl start treadmill-io 2>/dev/null || true; \
	 [ $$failed -eq 0 ] && echo "=== All tests passed ===" || exit 1

# Individual test binaries
$(TEST_DIR)/test_kv_protocol: $(TEST_DIR)/test_kv_protocol.o $(OBJ_TEST_DIR)/kv_protocol.test.o | $(TEST_DIR)
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

$(TEST_DIR)/test_ipc_protocol: $(TEST_DIR)/test_ipc_protocol.o $(OBJ_TEST_DIR)/kv_protocol.test.o $(OBJ_TEST_DIR)/ipc_protocol.test.o | $(TEST_DIR)
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

$(TEST_DIR)/test_ring_buffer: $(TEST_DIR)/test_ring_buffer.o | $(TEST_DIR)
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

$(TEST_DIR)/test_mode_state: $(TEST_DIR)/test_mode_state.o $(OBJ_TEST_DIR)/mode_state.test.o | $(TEST_DIR)
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

$(TEST_DIR)/test_emulation: $(TEST_DIR)/test_emulation.o $(OBJ_TEST_DIR)/kv_protocol.test.o $(OBJ_TEST_DIR)/mode_state.test.o | $(TEST_DIR)
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

$(TEST_DIR)/test_integration: $(TEST_DIR)/test_integration.o $(TEST_LIB_OBJS) | $(TEST_DIR)
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

$(TEST_DIR)/test_ipc_server: $(TEST_DIR)/test_ipc_server.o $(TEST_LIB_OBJS) | $(TEST_DIR)
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

$(TEST_DIR)/test_controller_live: $(TEST_DIR)/test_controller_live.o $(TEST_LIB_OBJS) | $(TEST_DIR)
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

# Directory creation
$(BUILD) $(OBJ_DIR) $(OBJ_TEST_DIR) $(TEST_DIR):
	mkdir -p $@

# Production object files
$(OBJ_DIR)/%.o: %.cpp | $(OBJ_DIR)
	$(CXX) $(CXXFLAGS) $(INCLUDES) -c -o $@ $<

# Test library object files (different suffix to avoid conflict with production .o)
$(OBJ_TEST_DIR)/%.test.o: %.cpp | $(OBJ_TEST_DIR)
	$(CXX) $(CXXFLAGS) $(INCLUDES) -DTESTING -c -o $@ $<

# Test object files
$(TEST_DIR)/%.o: tests/%.cpp | $(TEST_DIR)
	$(CXX) $(CXXFLAGS) $(INCLUDES) -c -o $@ $<

clean:
	rm -rf $(BUILD)

# Auto-generated header dependencies
-include $(OBJ_DIR)/*.d $(OBJ_TEST_DIR)/*.d $(TEST_DIR)/*.d

.PHONY: all clean test
