"""Python-vs-C++ encoding parity.

Golden vectors transcribed from the repo's doctest suites
(cpp/tests/test_kv_protocol.cpp, mirrored by host/tests/) — the C++
implementations are the hardware-proven truth; these tests pin the Python
ports in synth.py to them. Runs without docker (not marked qemu)."""

import synth


def test_encode_speed_hex_golden():
    # cpp/tests/test_kv_protocol.cpp encode_speed_hex vectors
    assert synth.encode_speed_hex(12) == "78"  # 1.2 mph
    assert synth.encode_speed_hex(120) == "4B0"  # 12.0 mph
    assert synth.encode_speed_hex(0) == "0"
    assert synth.encode_speed_hex(50) == "1F4"  # 5.0 mph (S3 motion)
    assert synth.encode_speed_hex(20) == "C8"  # 2.0 mph (S4 takeover)


def test_encode_incline_hex_golden():
    # cpp/tests/test_kv_protocol.cpp encode_incline_hex vectors
    assert synth.encode_incline_hex(0) == "0"
    assert synth.encode_incline_hex(10) == "A"  # 5%
    assert synth.encode_incline_hex(30) == "1E"  # 15%
    assert synth.encode_incline_hex(14) == "E"  # 7%
    assert synth.encode_incline_hex(1) == "1"  # 0.5%
    assert synth.encode_incline_hex(11) == "B"  # 5.5%


def test_frame_building_golden():
    # cpp kv_build: "[inc:5]" + 0xFF; bare key "[amps]" + 0xFF
    assert synth.build_console_frame("inc", "5") == b"[inc:5]\xff"
    assert synth.build_console_frame("amps") == b"[amps]\xff"
    # motor replies carry NO 0xFF terminator (RS485_DISCOVERY.md)
    assert synth.motor_reply("hmph", "78") == b"[hmph:78]"


def test_console_cycle_shape():
    cyc = synth.console_cycle(12, 10)
    assert len(cyc) == 14
    assert cyc[0] == b"[inc:A]\xff"
    assert cyc[1] == b"[hmph:78]\xff"
    # the other 12 keys are query-form
    assert cyc[2] == b"[amps]\xff"
    keys = [synth.frame_key(f[:-1]) for f in cyc]
    assert keys == synth.KEY_CYCLE


def test_count_complete_frames_model_semantics():
    # colon required: bare [key] frames are NOT complete console frames
    data = synth.console_cycle_bytes(12, 10)
    assert synth.count_complete_frames(data) == 2  # inc + hmph only
    # '[' restarts the candidate; non-printable resets
    assert synth.count_complete_frames(b"[a[b:1]") == 1
    assert synth.count_complete_frames(b"[b\x01elt:1]") == 0
    assert synth.count_complete_frames(b"[hm\xffph:78]") == 0
    # each malformed fuzz frame alone contributes no complete frame
    for f in synth.fuzz_frames():
        assert synth.count_complete_frames(f) == 0, f
