# Host-side test build for the Esp32Tap portable core.
#
#   make test    — pin-map check + all doctest binaries
#
# Mirrors cpp/Makefile flags: C++20, -fno-exceptions -fno-rtti.
# doctest is the repo-vendored header (third_party/doctest.h) — no copy.

CXX      ?= g++
REPO     := $(abspath ../../../../..)
CORE     := ../components/portable_core
CXXFLAGS := -std=c++20 -fno-exceptions -fno-rtti -Wall -Wextra -Werror -O2 \
            -pthread -MMD -MP -I$(CORE) -I. -I$(REPO)/third_party

BUILD := build

TEST_NAMES := test_kv_protocol test_mode_state test_ring_buffer \
              test_emulation test_safety_controller test_safety_boot_envelope \
              test_key_cache
TEST_BINS  := $(addprefix $(BUILD)/,$(TEST_NAMES))

CORE_SRCS := $(CORE)/protocol/kv_protocol.cpp \
             $(CORE)/engine/mode_state.cpp \
             $(CORE)/safety/safety_controller.cpp
CORE_OBJS := $(BUILD)/kv_protocol.o $(BUILD)/mode_state.o \
             $(BUILD)/safety_controller.o

.PHONY: test clean check-pins check-no-rtti

test: check-pins check-no-rtti $(TEST_BINS)
	@failed=0; for t in $(TEST_BINS); do echo "=== Running $$t ==="; ./$$t || { failed=1; break; }; done; \
	 [ $$failed -eq 0 ] && echo "=== All host tests passed ===" || exit 1

check-pins:
	python3 ../tools/check_pins.py

# The host build passes -fno-rtti explicitly; the ESP-IDF target build
# relies on CONFIG_COMPILER_CXX_RTTI being unset. Guard the property the
# flag protects: no RTTI constructs may enter the firmware sources.
check-no-rtti:
	@if grep -rnE '\b(typeid|dynamic_cast)\b' ../components \
	      --include='*.cpp' --include='*.h' --include='*.hpp' \
	    | grep -vE '^[^:]+:[0-9]+:[[:space:]]*(//|\*|/\*)'; then \
	  echo 'error: RTTI construct found (build relies on -fno-rtti)'; exit 1; \
	else \
	  echo 'check-no-rtti: OK (no typeid/dynamic_cast in firmware sources)'; \
	fi

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/kv_protocol.o: $(CORE)/protocol/kv_protocol.cpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -c -o $@ $<

$(BUILD)/mode_state.o: $(CORE)/engine/mode_state.cpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -c -o $@ $<

$(BUILD)/safety_controller.o: $(CORE)/safety/safety_controller.cpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -c -o $@ $<

$(BUILD)/%.o: tests/%.cpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -c -o $@ $<

$(BUILD)/test_kv_protocol: $(BUILD)/test_kv_protocol.o $(BUILD)/kv_protocol.o
	$(CXX) $(CXXFLAGS) -o $@ $^

$(BUILD)/test_mode_state: $(BUILD)/test_mode_state.o $(BUILD)/mode_state.o
	$(CXX) $(CXXFLAGS) -o $@ $^

$(BUILD)/test_ring_buffer: $(BUILD)/test_ring_buffer.o
	$(CXX) $(CXXFLAGS) -o $@ $^

$(BUILD)/test_emulation: $(BUILD)/test_emulation.o $(BUILD)/kv_protocol.o $(BUILD)/mode_state.o
	$(CXX) $(CXXFLAGS) -o $@ $^

$(BUILD)/test_safety_controller: $(BUILD)/test_safety_controller.o $(BUILD)/safety_controller.o
	$(CXX) $(CXXFLAGS) -o $@ $^

$(BUILD)/test_safety_boot_envelope: $(BUILD)/test_safety_boot_envelope.o $(BUILD)/safety_controller.o $(BUILD)/kv_protocol.o $(BUILD)/mode_state.o
	$(CXX) $(CXXFLAGS) -o $@ $^

$(BUILD)/test_key_cache: $(BUILD)/test_key_cache.o $(BUILD)/mode_state.o
	$(CXX) $(CXXFLAGS) -o $@ $^

clean:
	rm -rf $(BUILD)

-include $(wildcard $(BUILD)/*.d)
