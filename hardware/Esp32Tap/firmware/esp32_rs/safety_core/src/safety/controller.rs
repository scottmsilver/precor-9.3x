//! `SafetyController` — Rust port of `safety/safety_controller.{h,cpp}`, which
//! is itself a line-faithful port of `firmware/safety_model.py` (the normative
//! executable contract).
//!
//! Every method mirrors its Python namesake and every event string is
//! byte-identical, so the 57 vectors assert the same sequences in all three
//! implementations.
//!
//! # What the type system does here that C++ could not
//!
//! **`Phase` destroys stale transfer state by construction.** In C++ the
//! transfer is three independent fields — `mode_`,
//! `std::optional<int64_t> phase_deadline_`, and
//! `std::optional<int64_t> feedback_candidate_since_` — that EVERY emergency
//! path has to remember to clear. "A new emergency path forgot to clear the
//! deadline" is a whole live bug class. Here `self.phase = Phase::Proxy;`
//! destroys the deadline and the candidate together; they cannot outlive the
//! phase that owns them. `mode()` is a pure projection, so the vectors that
//! interrogate `mode()` are unchanged.
//!
//! Full typestate on the controller was considered and REJECTED: transitions
//! are driven by runtime deadlines, the vectors interrogate `mode()` directly,
//! and typestate would force type erasure at every call site for no verified
//! property.
//!
//! **`Option<Lease>` replaces a validity flag plus owner field.** Ownership
//! has no command deadline; explicit disconnect and safety events release it.
//!
//! # What the type system does NOT do here — carried by the tests and harness
//!
//! Relay entry/exit ORDERING, the 10 ms feedback qualification window,
//! fail-closed on unknown feedback, `BOTH_CLOSED` as a latched fault in every
//! mode, `BOTH_OPEN` as transit-only, the exact-deadline-loses rule, the 1.5 s
//! console freshness, persistent ownership and the clamps are all SEMANTIC
//! invariants no compiler checks.

use crate::fixed_str::FixedStr;
use crate::safety::constants::*;
use crate::units::*;

// --- transports and modes -------------------------------------------------

#[derive(Clone, Copy, PartialEq, Eq, Debug, Hash)]
pub enum Transport {
    Wss,
    Ble,
    Executor,
}

impl Transport {
    /// Uppercase form used in `connected:`/`lease_acquired:` event text.
    pub const fn upper(self) -> &'static str {
        match self {
            Transport::Wss => "WSS",
            Transport::Ble => "BLE",
            Transport::Executor => "EXECUTOR",
        }
    }
    /// Lowercase form used in `ignored_<t>_drop` / `<t>_disconnect`.
    pub const fn lower(self) -> &'static str {
        match self {
            Transport::Wss => "wss",
            Transport::Ble => "ble",
            Transport::Executor => "executor",
        }
    }
}

/// The controller's externally observable mode. `as_str()` yields the exact
/// tokens the QEMU harness's `QTSTATE mode=` regex matches.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum SafeMode {
    Proxy,
    EntryWaitGap,
    EntryWaitFeedback,
    Emulating,
    ExitWaitGap,
    ExitWaitFeedback,
}

impl SafeMode {
    pub const fn as_str(self) -> &'static str {
        match self {
            SafeMode::Proxy => "PROXY",
            SafeMode::EntryWaitGap => "ENTRY_WAIT_GAP",
            SafeMode::EntryWaitFeedback => "ENTRY_WAIT_FEEDBACK",
            SafeMode::Emulating => "EMULATING",
            SafeMode::ExitWaitGap => "EXIT_WAIT_GAP",
            SafeMode::ExitWaitFeedback => "EXIT_WAIT_FEEDBACK",
        }
    }
}

/// Decoded state of K1's grounded dry-contact feedback pole.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Feedback {
    Unknown,
    /// NC closed, NO open.
    Bypass,
    /// NC open, NO closed.
    Emulate,
    /// Latched fault in EVERY mode.
    BothClosed,
    /// Break-before-make transit only; never qualifies a transfer.
    BothOpen,
}

/// `safety_model.py Feedback.from_gpio`. NC/NO are pulled up (R25/R26), so a
/// HIGH line means the contact is OPEN.
///
/// Takes `NcHigh`/`NoHigh` newtypes rather than two bare `bool`s: swapping the
/// arguments is exactly the silent inversion that would fail closed in the
/// WRONG direction (reading BYPASS as EMULATE).
pub fn feedback_from_gpio(nc_high: NcHigh, no_high: NoHigh) -> Feedback {
    match (nc_high.get(), no_high.get()) {
        (false, true) => Feedback::Bypass,
        (true, false) => Feedback::Emulate,
        (false, false) => Feedback::BothClosed,
        (true, true) => Feedback::BothOpen,
    }
}

// --- connection identity --------------------------------------------------

/// A connection handle plus a non-reusable generation.
///
/// PLAN D5 phase-1: integer handles stand in for WSS socket objects / BLE
/// conn_handles. `Generation` cannot hold a negative, so the C++
/// `connection_rejected:invalid_identity` state is unrepresentable HERE — the
/// rejection moves to the untrusted-boundary form
/// [`SafetyController::connect_raw`], covered by the Rust-only vector
/// `connect_raw_rejects_a_negative_generation`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct ConnectionIdentity {
    pub transport: Transport,
    pub handle: Handle,
    pub generation: Generation,
}

impl ConnectionIdentity {
    pub fn new(transport: Transport, handle: i32, generation: i64) -> Option<Self> {
        Some(ConnectionIdentity {
            transport,
            handle: Handle(handle),
            generation: Generation::new(generation)?,
        })
    }
    /// Same transport + handle, ignoring generation.
    pub fn same_connection(&self, other: &ConnectionIdentity) -> bool {
        self.transport == other.transport && self.handle == other.handle
    }
}

// --- lease ----------------------------------------------------------------

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
struct Lease {
    owner: ConnectionIdentity,
}

// --- transfer phase -------------------------------------------------------

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum TransferPhase {
    WaitGap {
        deadline: Micros,
    },
    WaitFeedback {
        deadline: Micros,
        /// When the expected feedback was first seen continuously.
        candidate: Option<Micros>,
    },
}

impl TransferPhase {
    fn deadline(self) -> Micros {
        match self {
            TransferPhase::WaitGap { deadline } => deadline,
            TransferPhase::WaitFeedback { deadline, .. } => deadline,
        }
    }
}

/// The controller's phase. Owning the deadline and the feedback candidate
/// INSIDE the phase is what makes "forgot to clear it" unrepresentable.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Phase {
    Proxy,
    Emulating,
    Entry(TransferPhase),
    Exit(TransferPhase),
}

impl Phase {
    fn mode(self) -> SafeMode {
        match self {
            Phase::Proxy => SafeMode::Proxy,
            Phase::Emulating => SafeMode::Emulating,
            Phase::Entry(TransferPhase::WaitGap { .. }) => SafeMode::EntryWaitGap,
            Phase::Entry(TransferPhase::WaitFeedback { .. }) => SafeMode::EntryWaitFeedback,
            Phase::Exit(TransferPhase::WaitGap { .. }) => SafeMode::ExitWaitGap,
            Phase::Exit(TransferPhase::WaitFeedback { .. }) => SafeMode::ExitWaitFeedback,
        }
    }
    fn deadline(self) -> Option<Micros> {
        match self {
            Phase::Proxy | Phase::Emulating => None,
            Phase::Entry(t) | Phase::Exit(t) => Some(t.deadline()),
        }
    }
    /// Which feedback state would finish the transfer, if any. `None` outside
    /// the two `WaitFeedback` states — a timer tick can therefore never
    /// qualify anything.
    fn feedback_expected(self) -> Option<Feedback> {
        match self {
            Phase::Entry(TransferPhase::WaitFeedback { .. }) => Some(Feedback::Emulate),
            Phase::Exit(TransferPhase::WaitFeedback { .. }) => Some(Feedback::Bypass),
            _ => None,
        }
    }
    fn candidate(self) -> Option<Micros> {
        match self {
            Phase::Entry(TransferPhase::WaitFeedback { candidate, .. })
            | Phase::Exit(TransferPhase::WaitFeedback { candidate, .. }) => candidate,
            _ => None,
        }
    }
    fn set_candidate(&mut self, value: Option<Micros>) {
        match self {
            Phase::Entry(TransferPhase::WaitFeedback { candidate, .. })
            | Phase::Exit(TransferPhase::WaitFeedback { candidate, .. }) => *candidate = value,
            _ => {}
        }
    }
}

// --- output seam ----------------------------------------------------------

/// The controller's commanded output state, as ONE value.
///
/// The `SafetyIo` trait deliberately exposes `apply(OutputIntent)` and NOT
/// `set_relay_cmd`/`set_tx_enable`, so the tx-before-relay write order exists
/// at exactly one site instead of being a convention any caller can bypass
/// (in C++, `apply_outputs_locked()` is bypassable — the QEMU shim does
/// exactly that on its wrapper).
///
/// HONEST LIMIT: this makes the ordering single-sourced, NOT correct.
/// tx-before-relay is a semantic invariant no compiler checks; it stays
/// carried by boot-envelope case 2 and the S3 audit subsequence.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct OutputIntent {
    pub tx_enable: TxEnable,
    pub relay: RelayCmd,
}

// --- the controller -------------------------------------------------------

pub const MAX_ACTIVE_CONNECTIONS: usize = 8;
pub const MAX_TRACKED_GENERATIONS: usize = 16;
pub const EVENT_CAPACITY: usize = 256;
pub const EVENT_MAX_LEN: usize = 95;

/// Slots in the SEPARATE, eviction-resistant critical-event log.
///
/// The 256-slot audit ring is a rolling window: at ~200 routine events/s it
/// wraps in about 1.3 s, so the `emergency:<reason>` record that says WHY the
/// machine stopped — the single most valuable line in the whole log — could be
/// flushed out by ordinary traffic before anything read it. A reviewer flagged
/// exactly that. Critical records are therefore ALSO copied here, where
/// routine traffic cannot touch them.
pub const CRITICAL_CAPACITY: usize = 16;

/// Is this audit event a fault/emergency record worth protecting from
/// eviction?
///
/// Deliberately narrow — it must stay a tiny fraction of the event stream, or
/// the critical log becomes just a second rolling window. These are the only
/// events the controller emits that record a SAFETY OUTCOME rather than a step:
///  * `emergency:<reason>` — every stop, from every path (this is the "why";
///    `watchdog_stall` and `reset` funnel through `emergency_stop` too);
///  * `entry_abort:no_gap` — the one fail-closed abort that is not an
///    `emergency:` line;
///  * `proxy_feedback_invalid` — latches a fault WITHOUT an emergency stop
///    (N24), so it has no `emergency:` twin to protect it.
fn is_critical_event(text: &str) -> bool {
    text.starts_with("emergency:") || text == "entry_abort:no_gap" || text == "proxy_feedback_invalid"
}

pub type EventText = FixedStr<EVENT_MAX_LEN>;

#[derive(Clone, Copy)]
struct GenerationEntry {
    transport: Transport,
    handle: Handle,
    highest: i64,
}

pub struct SafetyController {
    phase: Phase,
    speed_tenths: SpeedTenths,
    incline_half_percent: InclineHalfPct,
    tread_ok: TreadOk,
    feedback: Feedback,
    fault_latched: bool,
    relay_cmd: RelayCmd,
    tx_enable: TxEnable,
    usb_pullup_enabled: bool,
    last_frame_at: Option<Micros>,
    bypass_since: Option<Micros>,
    bypass_qualified: bool,

    lease: Option<Lease>,

    active: [Option<ConnectionIdentity>; MAX_ACTIVE_CONNECTIONS],
    active_count: usize,

    generations: [Option<GenerationEntry>; MAX_TRACKED_GENERATIONS],
    generation_count: usize,

    // Console-frame candidate scanner. 101 bytes exactly: the >100 check runs
    // AFTER the byte is stored, so index 100 is a real write.
    candidate: [u8; 101],
    candidate_len: usize,

    events: [EventText; EVENT_CAPACITY],
    event_total: u64,

    /// Eviction-resistant copies of the fault/emergency records (see
    /// [`CRITICAL_CAPACITY`]). Slot 0 holds the FIRST critical event since
    /// boot and is never overwritten — the first fault is usually the cause
    /// and everything after it the consequence. Slots `1..` are a rolling
    /// window of the most recent criticals, so a later, unrelated stop cannot
    /// hide the original one and a burst of stops cannot hide the newest.
    /// Absolute indexes are kept so [`SafetyController::event_at`] can serve a
    /// critical record that the main ring has already dropped.
    critical_idx: [u64; CRITICAL_CAPACITY],
    critical_txt: [EventText; CRITICAL_CAPACITY],
    /// Total critical events ever appended (monotonic, like `event_total`).
    critical_total: u64,

    /// PLAN normal-exit STEP 1 ("transmit and finish a complete zero frame")
    /// is owed and has not been transmitted yet.
    ///
    /// NOT part of the model's observable state and NOT compared by the
    /// differential: `safety_model.py` records step 1 as the audit event
    /// `send_and_finish_complete_zero_frame` and models no wire, so adding a
    /// wire obligation to the controller's compared state would be a fork.
    /// The obligation is DISCHARGED by the task layer (`emulate_cycle`), which
    /// is the only place that owns the motor writer.
    exit_zero_frame_owed: bool,

    /// The obligation has been CLAIMED by the writer but the frame is not yet
    /// finished on the wire. The exit-gap interlock must stay closed for this
    /// too: clearing on claim alone let the serial engine qualify the gap
    /// during the ~25-50 ms the burst spends transmitting at 9600 baud, so
    /// K1 could open mid-frame. Cleared only by `discharge_exit_zero_frame`,
    /// which the writer calls AFTER tx-done.
    exit_zero_frame_in_flight: bool,

    /// Bumped on every Proxy -> Emulating transition. NOT part of the model's
    /// observable state and NOT compared by the differential — it exists so a
    /// 100 ms sampler can tell "still session N" from "exited and re-entered
    /// since the last sample". See [`EmulateSessionId`].
    emulate_session: EmulateSessionId,
}

impl Default for SafetyController {
    fn default() -> Self {
        Self::new()
    }
}

impl SafetyController {
    pub const fn new() -> Self {
        SafetyController {
            phase: Phase::Proxy,
            speed_tenths: SpeedTenths::ZERO,
            incline_half_percent: InclineHalfPct::ZERO,
            // NOTE: tread_ok boots TRUE (case `boot_state_is_proxy_...`).
            tread_ok: TreadOk(true),
            // Boot feedback is UNKNOWN, NOT assumed bypass (N23).
            feedback: Feedback::Unknown,
            fault_latched: false,
            relay_cmd: RelayCmd(false),
            tx_enable: TxEnable(false),
            usb_pullup_enabled: false,
            last_frame_at: None,
            bypass_since: None,
            bypass_qualified: false,
            lease: None,
            active: [None; MAX_ACTIVE_CONNECTIONS],
            active_count: 0,
            generations: [None; MAX_TRACKED_GENERATIONS],
            generation_count: 0,
            candidate: [0u8; 101],
            candidate_len: 0,
            events: [FixedStr::new(); EVENT_CAPACITY],
            event_total: 0,
            critical_idx: [0u64; CRITICAL_CAPACITY],
            critical_txt: [FixedStr::new(); CRITICAL_CAPACITY],
            critical_total: 0,
            exit_zero_frame_owed: false,
            exit_zero_frame_in_flight: false,
            emulate_session: EmulateSessionId(0),
        }
    }

    // --- observable state (mirrors safety_model.py attributes) ------------

    pub fn mode(&self) -> SafeMode {
        self.phase.mode()
    }

    /// `Some(id)` exactly while EMULATING; `None` otherwise. The id changes on
    /// every entry, so a sampler that compares ids cannot miss an
    /// exit-and-re-entry that happened entirely between two samples.
    pub fn emulate_session(&self) -> Option<EmulateSessionId> {
        match self.phase {
            Phase::Emulating => Some(self.emulate_session),
            _ => None,
        }
    }
    pub fn speed_tenths(&self) -> SpeedTenths {
        self.speed_tenths
    }
    pub fn incline_half_percent(&self) -> InclineHalfPct {
        self.incline_half_percent
    }
    pub fn tread_ok(&self) -> TreadOk {
        self.tread_ok
    }
    pub fn feedback(&self) -> Feedback {
        self.feedback
    }
    pub fn fault_latched(&self) -> bool {
        self.fault_latched
    }
    pub fn relay_cmd(&self) -> RelayCmd {
        self.relay_cmd
    }
    pub fn tx_enable(&self) -> TxEnable {
        self.tx_enable
    }
    pub fn usb_pullup_enabled(&self) -> bool {
        self.usb_pullup_enabled
    }
    pub fn last_complete_console_frame_at(&self) -> Option<Micros> {
        self.last_frame_at
    }
    pub fn owner(&self) -> Option<ConnectionIdentity> {
        self.lease.map(|l| l.owner)
    }
    /// Compatibility projection: ownership has no command deadline.
    pub fn lease_expires_at(&self) -> Option<Micros> {
        None
    }

    /// The commanded output state, as one value. See [`OutputIntent`].
    pub fn output_intent(&self) -> OutputIntent {
        OutputIntent {
            tx_enable: self.tx_enable,
            relay: self.relay_cmd,
        }
    }

    // --- event ring -------------------------------------------------------

    /// Total events ever appended; the ring keeps the newest `EVENT_CAPACITY`.
    pub fn event_count(&self) -> u64 {
        self.event_total
    }

    /// Absolute-indexed event text. `None` if evicted or out of range.
    ///
    /// A record the main ring has evicted is still served if it was CRITICAL
    /// and the critical log still holds it — so "why did the machine stop"
    /// survives a flood of routine traffic. Note this can only turn a `None`
    /// into a `Some`: no index that was readable before is readable
    /// differently now.
    pub fn event_at(&self, index: u64) -> Option<&str> {
        if index >= self.event_total {
            return None;
        }
        if self.event_total - index > EVENT_CAPACITY as u64 {
            return self.critical_event_at_index(index); // evicted from the ring
        }
        Some(self.events[(index % EVENT_CAPACITY as u64) as usize].as_str())
    }

    /// Total critical (fault/emergency) records ever appended.
    pub fn critical_event_count(&self) -> u64 {
        self.critical_total
    }

    /// The retained critical records, oldest slot first: `(absolute_index,
    /// text)`. Slot 0 is the first critical event since boot; the rest are the
    /// most recent ones. Yields at most [`CRITICAL_CAPACITY`] items.
    pub fn critical_events(&self) -> impl Iterator<Item = (u64, &str)> + '_ {
        let live = core::cmp::min(self.critical_total, CRITICAL_CAPACITY as u64) as usize;
        (0..live).map(move |s| (self.critical_idx[s], self.critical_txt[s].as_str()))
    }

    fn critical_event_at_index(&self, index: u64) -> Option<&str> {
        let live = core::cmp::min(self.critical_total, CRITICAL_CAPACITY as u64) as usize;
        for s in 0..live {
            if self.critical_idx[s] == index {
                return Some(self.critical_txt[s].as_str());
            }
        }
        None
    }

    /// Copy a critical record into the eviction-resistant log.
    ///
    /// Slot 0 is write-once (the first critical event since boot). Slots
    /// `1..CRITICAL_CAPACITY` are a rolling window over the rest.
    fn record_critical(&mut self, index: u64, text: EventText) {
        let slot = if self.critical_total == 0 {
            0
        } else {
            1 + ((self.critical_total - 1) % (CRITICAL_CAPACITY as u64 - 1)) as usize
        };
        self.critical_idx[slot] = index;
        self.critical_txt[slot] = text;
        self.critical_total += 1;
    }

    fn push_event(&mut self, text: &str) {
        self.push_event_owned(FixedStr::from_str_truncating(text));
    }

    fn push_event_owned(&mut self, text: EventText) {
        let slot = (self.event_total % EVENT_CAPACITY as u64) as usize;
        self.events[slot] = text;
        let index = self.event_total;
        self.event_total += 1;
        if is_critical_event(text.as_str()) {
            self.record_critical(index, text);
        }
    }

    fn push_event2(&mut self, prefix: &str, reason: &str) {
        let mut buf = EventText::new();
        buf.push_str(prefix);
        buf.push_str(reason);
        self.push_event_owned(buf);
    }

    /// Model format: `f"{prefix}:{transport}:{handle}:{generation}"`.
    fn push_connection_event(&mut self, prefix: &str, connection: &ConnectionIdentity) {
        let mut buf = EventText::new();
        buf.push_str(prefix);
        buf.push_str(":");
        buf.push_str(connection.transport.upper());
        buf.push_str(":");
        buf.push_i64(connection.handle.0 as i64);
        buf.push_str(":");
        buf.push_i64(connection.generation.get());
        self.push_event_owned(buf);
    }

    // --- connection bookkeeping -------------------------------------------

    fn highest_generation_for(&self, c: &ConnectionIdentity) -> i64 {
        for e in self.generations[..self.generation_count].iter().flatten() {
            if e.transport == c.transport && e.handle == c.handle {
                return e.highest;
            }
        }
        -1
    }

    fn set_highest_generation(&mut self, c: &ConnectionIdentity) -> bool {
        for e in self.generations[..self.generation_count].iter_mut().flatten() {
            if e.transport == c.transport && e.handle == c.handle {
                e.highest = c.generation.get();
                return true;
            }
        }
        if self.generation_count >= MAX_TRACKED_GENERATIONS {
            return false;
        }
        self.generations[self.generation_count] = Some(GenerationEntry {
            transport: c.transport,
            handle: c.handle,
            highest: c.generation.get(),
        });
        self.generation_count += 1;
        true
    }

    fn is_active(&self, c: &ConnectionIdentity) -> bool {
        self.active[..self.active_count]
            .iter()
            .flatten()
            .any(|a| a == c)
    }

    /// Read-only connection observation for integration diagnostics.
    ///
    /// Ownership is a separate fact (`owner()`); a failed acquisition can be
    /// connected without owning, which is exactly the state transactional
    /// application rollback must remove.
    pub fn is_connected(&self, connection: &ConnectionIdentity) -> bool {
        self.is_active(connection)
    }

    fn retain_active<F: Fn(&ConnectionIdentity) -> bool>(&mut self, keep: F) {
        let mut w = 0;
        for i in 0..self.active_count {
            if let Some(a) = self.active[i] {
                if keep(&a) {
                    self.active[w] = Some(a);
                    w += 1;
                }
            }
        }
        for slot in self.active[w..self.active_count].iter_mut() {
            *slot = None;
        }
        self.active_count = w;
    }

    // --- public operations (same names as safety_model.py) ----------------

    /// Register a connection. Returns false if the generation is stale, the
    /// tables are full, or (via [`Self::connect_raw`]) the identity is invalid.
    ///
    /// A HIGHER generation for the same concrete handle first removes every
    /// lower-generation active identity, and if a lower generation OWNED the
    /// lease, supersedes it with an emergency stop BEFORE registering. The new
    /// generation begins unowned (N3).
    pub fn connect(&mut self, connection: &ConnectionIdentity) -> bool {
        let highest = self.highest_generation_for(connection);
        if connection.generation.get() <= highest {
            self.push_event("connection_rejected:stale_generation");
            return false;
        }
        self.retain_active(|a| !a.same_connection(connection));
        if let Some(l) = self.lease {
            if l.owner.same_connection(connection)
                && l.owner.generation.get() < connection.generation.get()
            {
                // Model quirk carried for parity: the supersession path passes
                // a HARDCODED zero timestamp. Harmless — `emergency_stop`
                // ignores `now` — but it is a quirk, not intent.
                self.emergency_stop("owner_superseded", Micros::ZERO);
            }
        }
        if self.active_count >= MAX_ACTIVE_CONNECTIONS || !self.set_highest_generation(connection) {
            // Fixed-capacity deviation from the unbounded Python model
            // (PROVENANCE deviation 1). Fail-safe: a refused connection can
            // never take a lease or energize the relay.
            self.push_event("connection_rejected:capacity");
            return false;
        }
        self.active[self.active_count] = Some(*connection);
        self.active_count += 1;
        self.push_connection_event("connected", connection);
        true
    }

    /// Boundary form of [`Self::connect`] that accepts a RAW generation and
    /// rejects a negative one with `connection_rejected:invalid_identity`.
    ///
    /// `Generation` makes an invalid identity unrepresentable inside the
    /// controller (an improvement over both C++, which returns false, and
    /// Python, which raises `ValueError`), but the REJECTING behaviour is
    /// still required at the untrusted boundary — so it lives here.
    ///
    /// RESERVED FOR M5. Nothing in the phase-1 firmware calls this: the only
    /// identity-construction site (the QEMU shim) builds a firmware-internal
    /// identity through `ConnectionIdentity::new` and handles `None`. It has
    /// no C++ twin to be 1:1 with — the C++ validates inside `connect()` — so
    /// it is covered by the Rust-only vector
    /// `connect_raw_rejects_a_negative_generation`, NOT by a ported case.
    pub fn connect_raw(&mut self, transport: Transport, handle: i32, generation: i64) -> bool {
        match ConnectionIdentity::new(transport, handle, generation) {
            Some(c) => self.connect(&c),
            None => {
                self.push_event("connection_rejected:invalid_identity");
                false
            }
        }
    }

    pub fn acquire(&mut self, connection: &ConnectionIdentity, now: Micros) -> bool {
        self.enforce_due_safety(now);
        if self.lease.is_some() {
            self.push_event("lease_rejected:already_owned");
            return false;
        }
        if !self.is_active(connection) {
            self.push_event("lease_rejected:not_connected");
            return false;
        }
        self.lease = Some(Lease { owner: *connection });
        self.push_connection_event("lease_acquired", connection);
        true
    }

    fn is_owner(&self, connection: &ConnectionIdentity) -> bool {
        matches!(self.lease, Some(l) if l.owner == *connection)
    }

    fn authorize_owner(
        &mut self,
        connection: &ConnectionIdentity,
        now: Micros,
        ignored_event: &str,
    ) -> bool {
        if self.enforce_due_safety(now) {
            return false;
        }
        if !self.is_owner(connection) {
            self.push_event(ignored_event);
            return false;
        }
        true
    }

    pub fn heartbeat(&mut self, connection: &ConnectionIdentity, now: Micros) -> bool {
        if !self.authorize_owner(connection, now, "ignored_non_owner_heartbeat") {
            return false;
        }
        self.push_event("owner_heartbeat");
        true
    }

    /// Motion clamps are applied WHOLESALE: an out-of-range speed or incline
    /// rejects the entire command with no partial application (N28).
    pub fn command_motion(
        &mut self,
        connection: &ConnectionIdentity,
        speed_tenths: SpeedTenths,
        incline_half_percent: InclineHalfPct,
        now: Micros,
    ) -> bool {
        if !self.authorize_owner(connection, now, "ignored_non_owner_motion") {
            return false;
        }
        if speed_tenths.get() < 0 || speed_tenths.get() > SPEED_MAX_TENTHS.get() {
            self.push_event("motion_rejected:speed_range");
            return false;
        }
        if incline_half_percent.get() < 0
            || incline_half_percent.get() > INCLINE_APP_MAX_HALF.get()
        {
            self.push_event("motion_rejected:incline_range");
            return false;
        }
        self.speed_tenths = speed_tenths;
        self.incline_half_percent = incline_half_percent;
        self.push_event("owner_motion");
        true
    }

    pub fn disconnect(&mut self, connection: &ConnectionIdentity, now: Micros) -> bool {
        self.enforce_due_safety(now);
        self.retain_active(|a| a != connection);
        if !self.is_owner(connection) {
            self.push_event("ignored_non_owner_disconnect");
            return false;
        }
        self.emergency_stop("owner_disconnect", now);
        true
    }

    /// A transport-wide drop kills the lease only if the OWNER is on that
    /// transport; otherwise `ignored_<transport>_drop` (N6).
    pub fn disconnect_transport(&mut self, transport: Transport, now: Micros) -> bool {
        self.enforce_due_safety(now);
        self.retain_active(|a| a.transport != transport);
        let owner_on_transport = matches!(self.lease, Some(l) if l.owner.transport == transport);
        if !owner_on_transport {
            let mut buf = EventText::new();
            buf.push_str("ignored_");
            buf.push_str(transport.lower());
            buf.push_str("_drop");
            self.push_event_owned(buf);
            return false;
        }
        let mut reason = EventText::new();
        reason.push_str(transport.lower());
        reason.push_str("_disconnect");
        self.emergency_stop(reason.as_str(), now);
        true
    }

    /// Consume console bytes, timestamping ONLY syntactically complete frames.
    ///
    /// Returns 0 AND CONSUMES NOTHING when `enforce_due_safety` fires — the
    /// bytes are dropped, not buffered (case 1.7/16 asserts `== 0`).
    ///
    /// Frame pattern (N8): `\[[A-Za-z][A-Za-z0-9_]{0,31}:[\x20-\x7e]{0,64}\]`.
    /// `[` restarts the candidate; a non-printable byte clears it; a candidate
    /// over 100 bytes is discarded.
    pub fn observe_console_bytes(&mut self, data: &[u8], now: Micros) -> i32 {
        if self.enforce_due_safety(now) {
            return 0;
        }
        let mut complete = 0i32;
        for &byte in data {
            if byte == b'[' {
                self.candidate[0] = byte;
                self.candidate_len = 1;
                continue;
            }
            if self.candidate_len == 0 {
                continue;
            }
            if !(0x20..=0x7E).contains(&byte) {
                self.candidate_len = 0;
                continue;
            }
            self.candidate[self.candidate_len] = byte;
            self.candidate_len += 1;
            if self.candidate_len > 100 {
                self.candidate_len = 0;
                continue;
            }
            if byte != b']' {
                continue;
            }

            let content_len = self.candidate_len - 2;
            let mut content = [0u8; 101];
            content[..content_len].copy_from_slice(&self.candidate[1..1 + content_len]);
            let content = &content[..content_len];
            self.candidate_len = 0;

            let Some(colon) = content.iter().position(|&c| c == b':') else {
                continue;
            };
            let key = &content[..colon];
            let value = &content[colon + 1..];
            if key.is_empty() || key.len() > 32 {
                continue;
            }
            let first = key[0];
            if !first.is_ascii_alphabetic() {
                continue;
            }
            if !key[1..]
                .iter()
                .all(|&c| c.is_ascii_alphanumeric() || c == b'_')
            {
                continue;
            }
            if value.len() > 64 {
                continue;
            }
            // Value bytes are printable by construction; the model's
            // [\x20-\x7e] class adds no further constraint.

            self.last_frame_at = Some(now);
            complete += 1;
            self.push_event("complete_console_frame");
        }
        complete
    }

    /// `0 <= now - ts < CONSOLE_FRESH_US`. A frame at EXACTLY 1.5 s is stale.
    fn console_is_fresh(&self, now: Micros) -> bool {
        let Some(ts) = self.last_frame_at else {
            return false;
        };
        let age = now - ts;
        age >= Micros::ZERO && age < CONSOLE_FRESH_US
    }

    /// Emulate entry. The SIX preconditions are evaluated in this NORMATIVE
    /// order: not_owner -> not_proxy -> fault_latched -> tread_not_ok ->
    /// feedback_not_bypass -> console_not_fresh -> uart_not_idle_low.
    pub fn request_emulate(
        &mut self,
        connection: &ConnectionIdentity,
        now: Micros,
        uart_idle_low: bool,
    ) -> bool {
        if !self.authorize_owner(connection, now, "entry_rejected:not_owner") {
            return false;
        }
        if self.phase.mode() != SafeMode::Proxy || self.relay_cmd.get() || self.tx_enable.get() {
            self.push_event("entry_rejected:not_proxy");
            return false;
        }
        if self.fault_latched {
            self.push_event("entry_rejected:fault_latched");
            return false;
        }
        if !self.tread_ok.get() {
            self.push_event("entry_rejected:tread_not_ok");
            return false;
        }
        if self.feedback != Feedback::Bypass {
            self.push_event("entry_rejected:feedback_not_bypass");
            return false;
        }
        if !self.console_is_fresh(now) {
            self.push_event("entry_rejected:console_not_fresh");
            return false;
        }
        if !uart_idle_low {
            self.push_event("entry_rejected:uart_not_idle_low");
            return false;
        }

        self.begin_emulate_entry(now);
        true
    }

    /// Explicit, health-gated fault acknowledgement and Emulate entry.
    ///
    /// Every predicate is checked before the latch changes. On success the
    /// latch clear and ordinary entry sequence happen in this same call.
    pub fn request_emulate_recovering(
        &mut self,
        connection: &ConnectionIdentity,
        now: Micros,
        uart_idle_low: bool,
    ) -> bool {
        if !self.authorize_owner(connection, now, "entry_rejected:not_owner") {
            return false;
        }
        if self.phase.mode() != SafeMode::Proxy || self.relay_cmd.get() || self.tx_enable.get() {
            self.push_event("recovery_rejected:not_proxy");
            return false;
        }
        if !self.tread_ok.get() {
            self.push_event("recovery_rejected:tread_not_ok");
            return false;
        }
        if self.feedback != Feedback::Bypass
            || self.bypass_since.is_none()
            || !self.bypass_qualified
        {
            self.push_event("recovery_rejected:feedback_not_qualified_bypass");
            return false;
        }
        if !self.console_is_fresh(now) {
            self.push_event("recovery_rejected:console_not_fresh");
            return false;
        }
        if !uart_idle_low {
            self.push_event("recovery_rejected:uart_not_idle_low");
            return false;
        }

        self.fault_latched = false;
        self.push_event("fault_recovery_accepted");
        self.begin_emulate_entry(now);
        true
    }

    fn begin_emulate_entry(&mut self, now: Micros) {
        self.speed_tenths = SpeedTenths::ZERO;
        self.incline_half_percent = InclineHalfPct::ZERO;
        // These five are BATCH-EMITTED INTENT MARKERS, not evidence: they are
        // all pushed here in one go, so "configure_inverted_uart" does not
        // attest that a UART was reconfigured. The real actuation evidence is
        // relay_cmd_on -> feedback_candidate -> feedback_emulate_stable plus
        // the io_relay/io_tx boundary levels. Emitted at the same point as C++
        // because S3 keys on them; do not reorder.
        self.push_event("command_zero");
        self.push_event("configure_inverted_uart");
        self.push_event("verify_physical_idle_low");
        self.push_event("tx_enable_on");
        self.push_event("wait_entry_gap");
        // TX_ENABLE asserted WITHOUT sending a byte (N12).
        self.tx_enable = TxEnable(true);
        self.phase = Phase::Entry(TransferPhase::WaitGap {
            deadline: now + TRANSFER_GAP_DEADLINE_US,
        });
    }

    pub fn observe_interframe_gap(&mut self, now: Micros) -> bool {
        if self.enforce_due_safety(now) {
            return false;
        }
        match self.phase {
            Phase::Entry(TransferPhase::WaitGap { .. }) => {
                if self.feedback != Feedback::Bypass {
                    self.fault_latched = true;
                    self.emergency_stop("entry_feedback_changed_before_transfer", now);
                    return false;
                }
                self.relay_cmd = RelayCmd(true);
                self.phase = Phase::Entry(TransferPhase::WaitFeedback {
                    deadline: now + RELAY_FEEDBACK_DEADLINE_US,
                    candidate: None,
                });
                self.push_event("relay_cmd_on");
                true
            }
            Phase::Exit(TransferPhase::WaitGap { .. }) => {
                if self.feedback != Feedback::Emulate {
                    self.fault_latched = true;
                    self.emergency_stop("exit_feedback_changed_before_transfer", now);
                    return false;
                }
                self.relay_cmd = RelayCmd(false);
                self.phase = Phase::Exit(TransferPhase::WaitFeedback {
                    deadline: now + RELAY_FEEDBACK_DEADLINE_US,
                    candidate: None,
                });
                // GUEST-SIDE ORDERING FACT. Whether PLAN step 1 finished
                // before step 3 cannot be decided host-side: the audit log and
                // the motor-TX capture are independently buffered channels, so
                // their arrival order does not reflect guest order. Record the
                // answer here, where both facts are known at the same instant.
                #[cfg(feature = "exit-ordering-audit")]
                if self.exit_zero_frame_in_flight || self.exit_zero_frame_owed {
                    self.push_event("relay_cmd_off:zero_frame_unfinished");
                } else {
                    self.push_event("relay_cmd_off:zero_frame_done");
                }
                self.push_event("relay_cmd_off");
                true
            }
            _ => false,
        }
    }

    fn finish_feedback_transfer(&mut self) {
        match self.phase {
            Phase::Entry(TransferPhase::WaitFeedback { .. }) => {
                self.phase = Phase::Emulating;
                // The ONLY transition into Emulating, so the ONLY place the
                // session id may advance.
                self.emulate_session = self.emulate_session.next();
                self.push_event("feedback_emulate_stable");
                self.push_event("send_first_complete_zero_frame");
            }
            Phase::Exit(TransferPhase::WaitFeedback { .. }) => {
                self.phase = Phase::Proxy;
                self.push_event("feedback_bypass_stable");
                self.push_event("tx_enable_off");
                self.tx_enable = TxEnable(false);
                self.release_lease(true);
            }
            _ => {}
        }
    }

    /// D4: qualification requires `since + STABLE_US <= now` AND
    /// `since + STABLE_US < deadline`. A sample at the EXACT 10 ms deadline
    /// fails closed, and a timer tick alone never reaches here — only
    /// `observe_relay_feedback` samples do.
    fn qualify_feedback(&mut self, now: Micros) -> bool {
        let (Some(expected), Some(deadline), Some(since)) = (
            self.phase.feedback_expected(),
            self.phase.deadline(),
            self.phase.candidate(),
        ) else {
            return false;
        };
        let qualification_time = since + RELAY_FEEDBACK_STABLE_US;
        if self.feedback == expected && qualification_time <= now && qualification_time < deadline {
            self.finish_feedback_transfer();
            return true;
        }
        false
    }

    pub fn observe_relay_feedback(
        &mut self,
        nc_high: NcHigh,
        no_high: NoHigh,
        now: Micros,
    ) -> Feedback {
        self.enforce_due_safety(now);
        let feedback = feedback_from_gpio(nc_high, no_high);
        self.feedback = feedback;
        if feedback == Feedback::Bypass {
            if self.bypass_since.is_none() {
                self.bypass_since = Some(now);
                self.bypass_qualified = false;
            } else if !self.bypass_qualified {
                if let Some(since) = self.bypass_since {
                    if now >= since {
                        // Signed ordering proves a non-negative mathematical
                        // delta; bit-equivalent unsigned subtraction represents
                        // its entire i64 input domain without overflow.
                        let elapsed =
                            (now.get() as u64).wrapping_sub(since.get() as u64);
                        self.bypass_qualified =
                            elapsed >= RELAY_FEEDBACK_STABLE_US.get() as u64;
                    }
                }
            }
        } else {
            self.bypass_since = None;
            self.bypass_qualified = false;
        }
        if feedback == Feedback::BothClosed {
            // BOTH_CLOSED is an immediate latched fault in EVERY mode (N21).
            self.fault_latched = true;
            self.emergency_stop("relay_feedback_both_closed", now);
            return feedback;
        }

        if let Some(expected) = self.phase.feedback_expected() {
            if feedback == expected {
                if self.phase.candidate().is_none() {
                    self.phase.set_candidate(Some(now));
                    self.push_event("feedback_candidate");
                }
                self.qualify_feedback(now);
            } else {
                // BOTH_OPEN lands here: it resets the candidate and never
                // qualifies a transfer (N22).
                self.phase.set_candidate(None);
                self.push_event("feedback_transition");
            }
        } else {
            match self.phase.mode() {
                SafeMode::EntryWaitGap if feedback != Feedback::Bypass => {
                    self.fault_latched = true;
                    self.emergency_stop("entry_feedback_changed_before_gap", now);
                }
                SafeMode::ExitWaitGap if feedback != Feedback::Emulate => {
                    self.fault_latched = true;
                    self.emergency_stop("exit_feedback_changed_before_gap", now);
                }
                SafeMode::Emulating if feedback != Feedback::Emulate => {
                    self.fault_latched = true;
                    self.emergency_stop("relay_feedback_invalid", now);
                }
                SafeMode::Proxy if feedback != Feedback::Bypass => {
                    // Latches a fault but does NOT emergency-stop and does NOT
                    // change mode (N24).
                    self.fault_latched = true;
                    self.push_event("proxy_feedback_invalid");
                }
                _ => {}
            }
        }
        feedback
    }

    pub fn request_normal_exit(&mut self, connection: &ConnectionIdentity, now: Micros) -> bool {
        if !self.authorize_owner(connection, now, "exit_rejected:not_owner") {
            return false;
        }
        if self.phase.mode() != SafeMode::Emulating {
            self.push_event("exit_rejected:not_emulating");
            return false;
        }
        self.push_event("send_and_finish_complete_zero_frame");
        self.push_event("wait_exit_gap");
        // PLAN normal exit is ordered 1) transmit and finish a complete zero
        // frame, 2) wait for a capture-qualified gap, 3) deassert RELAY_CMD.
        // The model records step 1 as the event above and stops there; the
        // firmware owes an actual frame on the wire, and it must FINISH before
        // the gap may be qualified, or the relay would open while the motor
        // was still being commanded the old speed.
        self.exit_zero_frame_owed = true;
        self.speed_tenths = SpeedTenths::ZERO;
        self.incline_half_percent = InclineHalfPct::ZERO;
        self.phase = Phase::Exit(TransferPhase::WaitGap {
            deadline: now + TRANSFER_GAP_DEADLINE_US,
        });
        true
    }

    /// PLAN normal-exit step 1 is owed and not yet on the wire.
    ///
    /// The serial engine must NOT qualify the exit gap while this is true —
    /// that is step 2, and step 1 has to finish first. This gates only the
    /// engine's VOLUNTARY gap observation: every fail-closed path
    /// (`enforce_due_safety`, TREAD_OK loss, console staleness,
    /// emergency stop, the 1 s exit-gap deadline itself) is untouched and
    /// still releases the relay immediately, exactly as PLAN N19 requires.
    /// A stuck writer therefore costs at most the 1 s gap deadline, after
    /// which the relay drops without waiting for the frame.
    pub fn exit_zero_frame_owed(&self) -> bool {
        // In flight counts as outstanding: PLAN step 1 says transmit AND
        // FINISH, so the interlock must hold until tx-done, not until claim.
        self.exit_zero_frame_owed || self.exit_zero_frame_in_flight
    }

    /// Claim the obligation to transmit PLAN normal-exit step 1. Returns the
    /// token EXACTLY ONCE per exit request; the holder must actually transmit
    /// and finish a complete zero frame.
    ///
    /// Same unforgeable-token pattern as [`SafetyTimeoutFired`]: the caller
    /// cannot manufacture one, so "the frame was owed" and "the frame was
    /// claimed" cannot drift apart.
    pub fn take_exit_zero_frame(&mut self) -> Option<ExitZeroFrameOwed> {
        if !self.exit_zero_frame_owed {
            return None;
        }
        self.exit_zero_frame_owed = false;
        self.exit_zero_frame_in_flight = true;
        Some(ExitZeroFrameOwed(()))
    }

    /// Discharge PLAN normal-exit step 1 — call ONLY after the complete zero
    /// frame has FINISHED on the wire (tx-done). Consumes the unforgeable
    /// token, so it cannot be called without having claimed the obligation.
    ///
    /// Until this runs, `exit_zero_frame_owed()` keeps reporting true and the
    /// serial engine refuses to qualify the exit gap, which is what makes
    /// step 1 genuinely precede step 3 instead of racing it. This is fail-safe,
    /// not fail-blocking: the controller's own 1 s exit-gap deadline still
    /// fires and opens K1 regardless (N19), so a wedged writer costs at most
    /// that deadline.
    pub fn discharge_exit_zero_frame(&mut self, _proof: ExitZeroFrameOwed) {
        self.exit_zero_frame_in_flight = false;
    }

    /// FORK EXTENSION (no `safety_model.py` counterpart). Record that the
    /// task-layer console KV buffer had to resynchronise past an unterminated
    /// frame (see `crate::parse_buf`). Observable ONLY as an audit line: it
    /// changes no motion, mode, relay, lease or fault state, so it cannot make
    /// the machine less safe, and it turns a previously silent, permanent loss
    /// of the console-takeover interlock into something a log shows.
    pub fn note_console_parse_resync(&mut self) {
        self.push_event("console_parse_resync");
    }

    /// TREAD_OK loss is HARDWARE permission loss: immediate, never waits for a
    /// gap (N20).
    pub fn set_tread_ok(&mut self, value: TreadOk, now: Micros) {
        self.enforce_due_safety(now);
        self.tread_ok = value;
        if !self.tread_ok.get()
            && (self.phase.mode() != SafeMode::Proxy
                || self.relay_cmd.get()
                || self.tx_enable.get())
        {
            self.emergency_stop("tread_not_ok", now);
        }
    }

    /// GPIO7 is ACTIVE-LOW: the native-USB D+ pull-up is enabled only when the
    /// level is LOW. Defaults detached (N27).
    ///
    /// Takes the raw LEVEL (`level_high`), matching the C++ `set_vbus_present_n`
    /// — the sole inversion site.
    pub fn set_vbus_present_n(&mut self, level_high: bool) {
        self.usb_pullup_enabled = !level_high;
        if self.usb_pullup_enabled {
            self.push_event("usb_attach");
        } else {
            self.push_event("usb_detach");
        }
    }

    pub fn tick(&mut self, now: Micros) {
        self.enforce_due_safety(now);
    }

    /// Advance EVERY due safety deadline before accepting timed input (N7).
    /// An input at an exact deadline LOSES to the deadline.
    ///
    /// Returns true if the call was "handled" (i.e. the caller must not
    /// consume or mutate anything).
    fn enforce_due_safety(&mut self, now: Micros) -> bool {
        if self.phase.mode() != SafeMode::Proxy {
            if !self.tread_ok.get() {
                self.emergency_stop("tread_not_ok", now);
                return true;
            }
            if !self.console_is_fresh(now) {
                self.emergency_stop("console_stale", now);
                return true;
            }
        }
        // D4: a deadline is due when now >= deadline.
        let Some(deadline) = self.phase.deadline() else {
            return false;
        };
        if now < deadline {
            return false;
        }
        match self.phase {
            Phase::Entry(TransferPhase::WaitGap { .. }) => {
                // K1 is never moved on this path (N16).
                self.emergency_stop("entry_no_gap", now);
                self.push_event("entry_abort:no_gap");
                true
            }
            Phase::Entry(TransferPhase::WaitFeedback { .. }) => {
                self.fault_latched = true;
                self.emergency_stop("entry_feedback_timeout", now);
                true
            }
            Phase::Exit(TransferPhase::WaitGap { .. }) => {
                // At the exit-gap deadline, deassert RELAY_CMD IMMEDIATELY —
                // remaining in Emulate is less safe (N19). This is the one
                // deadline branch that does NOT report "handled".
                self.push_event("exit_gap_timeout");
                self.relay_cmd = RelayCmd(false);
                self.phase = Phase::Exit(TransferPhase::WaitFeedback {
                    deadline: now + RELAY_FEEDBACK_DEADLINE_US,
                    candidate: None,
                });
                self.push_event("relay_cmd_off");
                false
            }
            Phase::Exit(TransferPhase::WaitFeedback { .. }) => {
                self.fault_latched = true;
                self.emergency_stop("exit_feedback_timeout", now);
                true
            }
            _ => false,
        }
    }

    fn release_lease(&mut self, log: bool) {
        self.lease = None;
        if log {
            self.push_event("lease_released");
        }
    }

    /// FORK EXTENSION — no counterpart in `safety_model.py` (PROVENANCE
    /// deviation 4). Zeroes commanded motion WITHOUT touching mode, lease,
    /// relay or feedback. Called when the emulate cycle's 3-hour inactivity
    /// timeout fires, so the authoritative state can never keep reporting
    /// stale nonzero motion after the wire has been zeroed.
    ///
    /// Strictly monotonic toward safe: it only ever lowers motion to zero.
    ///
    /// Requires a [`SafetyTimeoutFired`] token, so it cannot be called without
    /// having actually observed the timeout — the C++ equivalent takes a plain
    /// `bool` that any caller can pass `true`.
    pub fn safety_timeout_zero_motion(&mut self, _proof: SafetyTimeoutFired, _now: Micros) {
        if self.speed_tenths.is_zero() && self.incline_half_percent.is_zero() {
            return;
        }
        self.speed_tenths = SpeedTenths::ZERO;
        self.incline_half_percent = InclineHalfPct::ZERO;
        self.push_event("safety_timeout_zero_motion");
    }

    /// Exactly: speed 0, incline 0, relay off, TX off, mode PROXY, phase
    /// deadline and feedback candidate cleared, lease released SILENTLY,
    /// append `emergency:<reason>` (N25).
    ///
    /// `now` is accepted for call-site parity and audit; the model ignores it.
    pub fn emergency_stop(&mut self, reason: &str, _now: Micros) {
        self.speed_tenths = SpeedTenths::ZERO;
        self.incline_half_percent = InclineHalfPct::ZERO;
        self.relay_cmd = RelayCmd(false);
        self.tx_enable = TxEnable(false);
        // Assigning the phase destroys the deadline AND the feedback candidate
        // together — they cannot be left behind.
        self.phase = Phase::Proxy;
        // An emergency abandons a normal exit in progress: the belt is already
        // zeroed and TX_ENABLE is already deasserted, so a "polite" zero frame
        // is both impossible and pointless. Clearing it here is what stops the
        // obligation leaking into a LATER emulate session.
        self.exit_zero_frame_owed = false;
        self.exit_zero_frame_in_flight = false;
        self.release_lease(false);
        self.push_event2("emergency:", reason);
    }

    pub fn watchdog_stall(&mut self, now: Micros) {
        self.reset_class_stop("watchdog", now);
    }

    pub fn reset(&mut self, now: Micros, reason: &str) {
        self.reset_class_stop(reason, now);
    }

    /// Reset-class stops additionally clear all active connections, the
    /// console candidate and timestamp, set feedback UNKNOWN, and disable the
    /// USB pull-up. The GENERATION MAP SURVIVES, so a stale identity still
    /// cannot reconnect (N26).
    fn reset_class_stop(&mut self, reason: &str, now: Micros) {
        self.emergency_stop(reason, now);
        self.active = [None; MAX_ACTIVE_CONNECTIONS];
        self.active_count = 0;
        self.candidate_len = 0;
        self.last_frame_at = None;
        self.feedback = Feedback::Unknown;
        self.bypass_since = None;
        self.bypass_qualified = false;
        self.usb_pullup_enabled = false;
    }
}

/// Proof that the emulate cycle's 3-hour timeout actually fired.
///
/// Private constructor, not `Copy`, not `Default` — the ONLY way to obtain one
/// is [`crate::cycle::EmulationCycle::consume_safety_timeout`], so the
/// mode -> controller back-mirror cannot be invoked speculatively. The C++
/// equivalent is a `bool` any caller can pass `true`.
#[derive(Debug)]
pub struct SafetyTimeoutFired(());

impl SafetyTimeoutFired {
    pub(crate) fn new() -> Self {
        SafetyTimeoutFired(())
    }

    /// TEST-HARNESS ONLY. `safety_timeout_zero_motion` is the one
    /// fork-extension with no `safety_model.py` counterpart, and the token
    /// (rightly) made it the one controller method the C++ differential could
    /// not drive. This mint is gated behind the `test-mint` cargo feature,
    /// which ONLY `difftest` enables — the firmware crate does not, so the
    /// token stays unforgeable in the shipped image and the differential still
    /// covers the method.
    #[cfg(feature = "test-mint")]
    pub fn mint_for_differential_test() -> Self {
        SafetyTimeoutFired(())
    }
}

/// Proof that PLAN normal-exit step 1 was CLAIMED by the task that owns the
/// motor writer.
///
/// Private constructor; obtainable only from
/// [`SafetyController::take_exit_zero_frame`], and only once per exit request.
#[derive(Debug)]
pub struct ExitZeroFrameOwed(());
