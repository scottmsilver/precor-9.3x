CXX = g++
CXXFLAGS = -std=c++17 -fno-exceptions -fno-rtti -Wall -Wextra -O2 -pthread \
           -Wno-format-truncation
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
TEST_BINS = $(addprefix tests/,$(TEST_NAMES))

TARGET = treadmill_io

all: $(TARGET)

# Production binary (links libpigpio, runs on Pi)
$(TARGET): $(OBJS)
	$(CXX) $(CXXFLAGS) -o $@ $^ $(LDFLAGS)

# Build and run all tests
test: $(TEST_BINS)
	@for t in $(TEST_BINS); do echo "=== Running $$t ==="; ./$$t || exit 1; done
	@echo "=== All tests passed ==="

# Individual test binaries
tests/test_kv_protocol: tests/test_kv_protocol.o src/kv_protocol.test.o
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

tests/test_ipc_protocol: tests/test_ipc_protocol.o src/kv_protocol.test.o src/ipc_protocol.test.o
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

tests/test_ring_buffer: tests/test_ring_buffer.o
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

tests/test_mode_state: tests/test_mode_state.o src/mode_state.test.o
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

tests/test_emulation: tests/test_emulation.o src/kv_protocol.test.o src/mode_state.test.o
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

tests/test_integration: tests/test_integration.o $(TEST_LIB_OBJS)
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

tests/test_ipc_server: tests/test_ipc_server.o $(TEST_LIB_OBJS)
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

tests/test_controller_live: tests/test_controller_live.o $(TEST_LIB_OBJS)
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread -lrt

# Production object files
src/%.o: src/%.cpp
	$(CXX) $(CXXFLAGS) $(INCLUDES) -c -o $@ $<

# Test library object files (different suffix to avoid conflict with production .o)
src/%.test.o: src/%.cpp
	$(CXX) $(CXXFLAGS) $(INCLUDES) -DTESTING -c -o $@ $<

# Test object files
tests/%.o: tests/%.cpp
	$(CXX) $(CXXFLAGS) $(INCLUDES) -c -o $@ $<

clean:
	rm -f $(TARGET) $(TEST_BINS) src/*.o src/*.test.o tests/*.o
	rm -f src/*.gcda src/*.gcno tests/*.gcda tests/*.gcno *.gcov
	rm -f tests/test_*_cov

.PHONY: all clean test
