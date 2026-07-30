//! A BLE peer's identity: bounded, JSON-safe, and total over garbage.
//!
//! These vectors have no counterpart in the Pi daemon and are not meant to:
//! `bluer` hands that side a `String` and `serde_json` escapes it on the way
//! out. This device builds JSON by hand into fixed stack buffers, so a strap's
//! advertised name is raw attacker-controlled bytes on their way into a JSON
//! string — and the escaping has to happen ONCE, at ingest, or every renderer
//! has to remember it.

use ble_core::peer::{Addr, FixedName, ADDR_TEXT_LEN, MAX_NAME};

// ---------------------------------------------------------------------------
// Names
// ---------------------------------------------------------------------------

#[test]
fn an_ordinary_name_survives_unchanged() {
    let mut n = FixedName::EMPTY;
    n.set(b"Polar H10 4A2B1C");
    assert_eq!(n.as_str(), "Polar H10 4A2B1C");
}

#[test]
fn the_two_json_escapes_cannot_get_through() {
    // The whole point. `{"device":"a"…` would truncate the object and let the
    // rest of the name be read as JSON.
    let mut n = FixedName::EMPTY;
    n.set(br#"a"b\c"#);
    assert_eq!(n.as_str(), "a.b.c");

    n.set(br#""},"bpm":999,"x":""#);
    assert!(!n.as_str().contains('"'));
    assert!(!n.as_str().contains('\\'));
}

#[test]
fn control_bytes_and_non_ascii_become_dots() {
    let mut n = FixedName::EMPTY;
    n.set(&[0x00, 0x0a, 0x1f, 0x7f, 0x80, 0xff, b'x']);
    assert_eq!(n.as_str(), "......x");
}

#[test]
fn a_long_name_is_truncated_to_the_bound() {
    let mut n = FixedName::EMPTY;
    n.set(&[b'z'; 512]);
    assert_eq!(n.len(), MAX_NAME);
    assert_eq!(n.as_str().len(), MAX_NAME);
}

#[test]
fn truncation_can_never_split_a_character() {
    // A multi-byte sequence is filtered to single-byte dots BEFORE the length
    // bound applies, so `as_str` is always valid UTF-8 no matter where the cut
    // lands. Walk every cut position through a 3-byte-per-character name.
    for extra in 0..8usize {
        let mut src = Vec::new();
        for _ in 0..(MAX_NAME + extra) {
            src.extend_from_slice("→".as_bytes()); // 3 bytes each
        }
        let mut n = FixedName::EMPTY;
        n.set(&src);
        assert!(n.len() <= MAX_NAME);
        // The fallback in `as_str` would mask a break, so check the bytes.
        assert!(
            core::str::from_utf8(n.as_str().as_bytes()).is_ok(),
            "extra={extra}"
        );
        assert_eq!(n.as_str().len(), n.len());
    }
}

#[test]
fn set_is_total_over_every_single_byte() {
    for b in 0u16..=255 {
        let mut n = FixedName::EMPTY;
        n.set(&[b as u8]);
        assert_eq!(n.len(), 1);
        let s = n.as_str();
        assert_eq!(s.len(), 1);
        let c = s.as_bytes()[0];
        assert!((0x20..=0x7e).contains(&c), "byte {b:#04x} produced {c:#04x}");
        assert!(c != b'"' && c != b'\\');
    }
}

#[test]
fn setting_twice_replaces_rather_than_appends() {
    let mut n = FixedName::EMPTY;
    n.set(b"aaaaaaaaaa");
    n.set(b"bb");
    assert_eq!(n.as_str(), "bb");
}

// ---------------------------------------------------------------------------
// Addresses
// ---------------------------------------------------------------------------

#[test]
fn text_renders_big_endian_from_nimble_little_endian_bytes() {
    // NimBLE's `ble_addr_t.val` is little-endian; every UI shows the reverse.
    // Getting this backwards would show the user an address that does not
    // match the one on the strap, and would round-trip through `select`
    // wrongly.
    let a = Addr::new([0xFF, 0xEE, 0xDD, 0xCC, 0xBB, 0xAA], 1);
    let mut out = [0u8; ADDR_TEXT_LEN];
    let n = a.text(&mut out);
    assert_eq!(&out[..n], b"AA:BB:CC:DD:EE:FF");
    assert_eq!(n, ADDR_TEXT_LEN);
}

#[test]
fn parse_and_text_are_inverses() {
    for text in [
        b"AA:BB:CC:DD:EE:FF".as_slice(),
        b"00:00:00:00:00:00".as_slice(),
        b"01:23:45:67:89:AB".as_slice(),
        b"FF:FF:FF:FF:FF:FF".as_slice(),
    ] {
        let val = Addr::parse(text).expect("valid");
        let mut out = [0u8; ADDR_TEXT_LEN];
        let n = Addr::new(val, 0).text(&mut out);
        assert_eq!(&out[..n], text);
    }
}

#[test]
fn parse_is_case_insensitive_and_separator_tolerant() {
    let canonical = Addr::parse(b"AA:BB:CC:DD:EE:FF").unwrap();
    assert_eq!(Addr::parse(b"aa:bb:cc:dd:ee:ff").unwrap(), canonical);
    assert_eq!(Addr::parse(b"aa-bb-cc-dd-ee-ff").unwrap(), canonical);
    assert_eq!(Addr::parse(b"AABBCCDDEEFF").unwrap(), canonical);
    assert_eq!(Addr::parse(b"aA:Bb:cC:Dd:eE:Ff").unwrap(), canonical);
}

#[test]
fn a_malformed_address_is_rejected_whole_never_partially_parsed() {
    // A half-parsed address is a connection attempt to a device the user did
    // not pick.
    for bad in [
        b"".as_slice(),
        b":".as_slice(),
        b"AA:BB:CC:DD:EE".as_slice(),          // too short
        b"AA:BB:CC:DD:EE:F".as_slice(),        // 11 nibbles
        b"AA:BB:CC:DD:EE:FF:00".as_slice(),    // too long
        b"AA:BB:CC:DD:EE:FFF".as_slice(),      // 13 nibbles
        b"ZZ:BB:CC:DD:EE:FF".as_slice(),       // not hex
        b"AA BB CC DD EE FF".as_slice(),       // space is not a separator
        b"AA:BB:CC:DD:EE:FF\0".as_slice(),     // trailing NUL
        b"\"AA:BB:CC:DD:EE:FF\"".as_slice(),   // quoted, as JSON would give it
    ] {
        assert!(Addr::parse(bad).is_none(), "{:?} was accepted", bad);
    }
}

#[test]
fn parse_never_panics_over_a_wide_byte_domain() {
    // The address string arrives in an HTTP body from anyone on the LAN.
    for b in 0u16..=255 {
        for len in 0..20usize {
            let buf = vec![b as u8; len];
            let _ = Addr::parse(&buf);
        }
    }
    // Mixed garbage of every length up to the real one and past it.
    for len in 0..40usize {
        let buf: Vec<u8> = (0..len).map(|i| (i * 37 % 256) as u8).collect();
        let _ = Addr::parse(&buf);
    }
}

#[test]
fn an_absent_address_renders_as_nothing_rather_than_zeros() {
    // `00:00:00:00:00:00` in the app's device field would look like a real
    // strap; empty is what "none" means everywhere else in this project.
    let mut out = [0u8; ADDR_TEXT_LEN];
    assert_eq!(Addr::NONE.text(&mut out), 0);
}

#[test]
fn the_address_type_is_carried_and_compared() {
    // Most straps use a RANDOM static address. Two peers can share `val` and
    // differ only in `kind`; treating them as one device would connect to the
    // wrong one.
    let public = Addr::new([1, 2, 3, 4, 5, 6], 0);
    let random = Addr::new([1, 2, 3, 4, 5, 6], 1);
    assert_ne!(public, random);
    assert_eq!(public, Addr::new([1, 2, 3, 4, 5, 6], 0));
}
