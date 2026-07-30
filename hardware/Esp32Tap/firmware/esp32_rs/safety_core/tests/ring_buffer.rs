//! Port of `host/tests/test_ring_buffer.cpp` — 7 cases, 1:1 by name.

use safety_core::ring::RingBuffer;
use std::sync::{Arc, Mutex};

// cpp: "empty ring buffer"
#[test]
fn empty_ring_buffer() {
    let ring = RingBuffer::<2048, 256>::new();
    let snap = ring.snapshot();
    assert_eq!(snap.head, 0);
    assert_eq!(snap.count, 0);
}

// cpp: "push and snapshot"
#[test]
fn push_and_snapshot() {
    let mut ring = RingBuffer::<2048, 256>::new();
    ring.push("hello\n");
    let snap = ring.snapshot();
    assert_eq!(snap.head, 1);
    assert_eq!(snap.count, 1);
    assert_eq!(ring.at(0), *"hello\n");
}

// cpp: "multiple pushes"
#[test]
fn multiple_pushes() {
    let mut ring = RingBuffer::<2048, 256>::new();
    ring.push("msg1\n");
    ring.push("msg2\n");
    ring.push("msg3\n");
    let snap = ring.snapshot();
    assert_eq!(snap.head, 3);
    assert_eq!(snap.count, 3);
    assert_eq!(ring.at(0), *"msg1\n");
    assert_eq!(ring.at(1), *"msg2\n");
    assert_eq!(ring.at(2), *"msg3\n");
}

// cpp: "wrap-around"
#[test]
fn wrap_around() {
    let mut ring = RingBuffer::<4, 64>::new(); // tiny ring for wrap testing
    ring.push("a\n");
    ring.push("b\n");
    ring.push("c\n");
    ring.push("d\n"); // fills ring
    ring.push("e\n"); // wraps, overwrites "a"

    let snap = ring.snapshot();
    assert_eq!(snap.head, 1);
    // LOAD-BEARING: `count` is a monotonic TOTAL, not a fill level.
    assert_eq!(snap.count, 5);
    assert_eq!(ring.at(0), *"e\n");
    assert_eq!(ring.at(1), *"b\n");
}

// cpp: "message truncation"
#[test]
fn message_truncation() {
    let mut ring = RingBuffer::<4, 8>::new();
    ring.push("this is a very long message that exceeds the buffer");
    // Truncated to MSG - 1 chars.
    assert!(ring.at(0).len() <= 7);
}

// cpp: "negative index wraps safely"
#[test]
fn negative_index_wraps_safely() {
    let mut ring = RingBuffer::<4, 64>::new();
    ring.push("a\n");
    ring.push("b\n");
    ring.push("c\n");
    ring.push("d\n");

    assert_eq!(ring.at(-1), ring.at(3)); // -1 % 4 -> 3
    assert_eq!(ring.at(-4), ring.at(0)); // -4 % 4 -> 0
    assert_eq!(ring.at(-5), ring.at(3)); // -5 % 4 -> 3
}

// cpp: "concurrent push and snapshot"
//
// The C++ ring locks internally; the Rust one takes `&mut self` for `push`
// and `&self` for reads, so sharing across threads is expressed by an
// EXPLICIT `Mutex`. Same property under test: a writer thread pushing N
// messages while another thread takes snapshots and reads `at(0)` must not
// tear or crash, and the final count must be exactly N.
#[test]
fn concurrent_push_and_snapshot() {
    const N: u32 = 1000;
    let ring = Arc::new(Mutex::new(RingBuffer::<2048, 256>::new()));

    let writer_ring = Arc::clone(&ring);
    let writer = std::thread::spawn(move || {
        for i in 0..N {
            let msg = format!("msg{i}\n");
            writer_ring.lock().unwrap().push(&msg);
        }
    });

    let mut max_count = 0;
    for _ in 0..100 {
        let g = ring.lock().unwrap();
        let snap = g.snapshot();
        if snap.count > max_count {
            max_count = snap.count;
        }
        if snap.count > 0 {
            let _ = g.at(0);
        }
    }

    writer.join().unwrap();

    let final_snap = ring.lock().unwrap().snapshot();
    assert_eq!(final_snap.count, N);
}
