package com.precor.treadmill.ui.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.precor.treadmill.data.preferences.ServerPreferences
import com.precor.treadmill.data.remote.TreadmillApi
import com.precor.treadmill.data.remote.TreadmillWebSocket
import com.precor.treadmill.data.remote.models.*
import com.precor.treadmill.data.remote.models.Profile
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import com.precor.treadmill.ui.util.LeadingGuard
import com.precor.treadmill.ui.util.TrailingDebounce
import com.precor.treadmill.ui.util.fmtDur
import com.precor.treadmill.ui.util.paceDisplay
import android.os.SystemClock
import android.util.Log
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import kotlin.math.*

// --- Client state data classes ---

data class TreadmillStatus(
    val proxy: Boolean = true,
    val emulate: Boolean = false,
    val emuSpeed: Int = 0,       // tenths of mph
    val emuIncline: Double = 0.0,  // percent (0.5 steps)
    val speed: Double? = null,   // live motor speed mph
    val incline: Double? = null, // live motor incline percent
    val motor: Map<String, String> = emptyMap(),
    val treadmillConnected: Boolean = false,
    val heartRate: Int = 0,
    val hrmConnected: Boolean = false,
    val hrmDevice: String = "",
)

data class SessionState(
    val active: Boolean = false,
    val elapsed: Double = 0.0,
    val distance: Double = 0.0,
    val vertFeet: Double = 0.0,
    val calories: Double = 0.0,
    val wallStartedAt: String = "",
    val endReason: String? = null,
)

data class ProgramState(
    val program: Program? = null,
    val running: Boolean = false,
    val paused: Boolean = false,
    val completed: Boolean = false,
    val currentInterval: Int = 0,
    val intervalElapsed: Double = 0.0,
    val totalElapsed: Double = 0.0,
    val totalDuration: Double = 0.0,
)

data class KVEntry(
    val ts: String,
    val src: String,
    val key: String,
    val value: String,
)

// --- Derived state ---

data class DerivedSession(
    val active: Boolean,
    val elapsed: Double,
    val elapsedDisplay: String,
    val distance: Double,
    val distDisplay: String,
    val vertFeet: Double,
    val vertDisplay: String,
    val calories: Double,
    val caloriesDisplay: String,
    val pace: String,
    val speedMph: Double,
    val endReason: String?,
)

data class ElevationSegment(val x: Float, val w: Float, val y: Float)

data class DerivedProgram(
    val program: Program?,
    val running: Boolean,
    val paused: Boolean,
    val completed: Boolean,
    val currentInterval: Int,
    val intervalElapsed: Double,
    val totalElapsed: Double,
    val totalDuration: Double,
    val currentIv: Interval?,
    val nextIv: Interval?,
    val ivRemaining: Double,
    val totalRemaining: Double,
    val ivPct: Double,
    val timelinePos: Double,
    // Elevation profile data
    val segments: List<ElevationSegment>,
    val elevPosX: Float,
    val elevPosY: Float,
    val maxIncline: Double,
    val yAxisMax: Float,
    val intervalCount: Int,
    val intervalBoundaryXs: FloatArray,
) {
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is DerivedProgram) return false
        return program == other.program && running == other.running && paused == other.paused &&
                completed == other.completed && currentInterval == other.currentInterval &&
                intervalElapsed == other.intervalElapsed && totalElapsed == other.totalElapsed &&
                totalDuration == other.totalDuration && currentIv == other.currentIv &&
                nextIv == other.nextIv && ivRemaining == other.ivRemaining &&
                totalRemaining == other.totalRemaining && ivPct == other.ivPct &&
                timelinePos == other.timelinePos && segments == other.segments &&
                elevPosX == other.elevPosX &&
                elevPosY == other.elevPosY && maxIncline == other.maxIncline && yAxisMax == other.yAxisMax &&
                intervalCount == other.intervalCount &&
                intervalBoundaryXs.contentEquals(other.intervalBoundaryXs)
    }

    override fun hashCode(): Int = program.hashCode() + running.hashCode() + totalElapsed.hashCode() +
            currentInterval.hashCode() + intervalCount.hashCode()
}

// --- Elevation profile constants (match useProgram.ts) ---
private const val ELEV_W = 400f
private const val ELEV_H = 140f
private const val ELEV_PAD = 10f
private const val MAX_KV_LOG = 500
private const val DIRTY_GRACE_MS = 500L
private const val TAG = "TreadmillVM"
private const val TIMER_BLEND = 0.15       // 15% correction per server update (~1Hz)
private const val TIMER_SNAP_MS = 2000L    // snap if drift > 2s (unpause, initial state)

class TreadmillViewModel(
    private val api: TreadmillApi,
    private val webSocket: TreadmillWebSocket,
    private val prefs: ServerPreferences,
) : ViewModel() {

    // --- Core state ---
    private val _status = MutableStateFlow(TreadmillStatus())
    val status: StateFlow<TreadmillStatus> = _status.asStateFlow()

    private val _session = MutableStateFlow(SessionState())
    val session: StateFlow<SessionState> = _session.asStateFlow()

    private val _program = MutableStateFlow(ProgramState())
    val program: StateFlow<ProgramState> = _program.asStateFlow()

    private val _kvLog = MutableStateFlow<List<KVEntry>>(emptyList())
    val kvLog: StateFlow<List<KVEntry>> = _kvLog.asStateFlow()

    val wsConnected: StateFlow<Boolean> = webSocket.connected

    private val _hrmDevices = MutableStateFlow<List<HrmDevice>>(emptyList())
    val hrmDevices: StateFlow<List<HrmDevice>> = _hrmDevices.asStateFlow()

    // --- Profile state ---
    private val _activeProfile = MutableStateFlow<Profile?>(null)
    val activeProfile: StateFlow<Profile?> = _activeProfile.asStateFlow()

    private val _guestMode = MutableStateFlow(false)
    val guestMode: StateFlow<Boolean> = _guestMode.asStateFlow()

    private val _profiles = MutableStateFlow<List<Profile>>(emptyList())
    val profiles: StateFlow<List<Profile>> = _profiles.asStateFlow()

    /** Emits Unit each time the profile changes (for VoiceViewModel to invalidate config). */
    private val _profileChanged = MutableSharedFlow<Unit>(extraBufferCapacity = 4)
    val profileChanged: SharedFlow<Unit> = _profileChanged.asSharedFlow()

    // --- Toast ---
    private val _toast = MutableSharedFlow<String>(extraBufferCapacity = 8)
    val toast: SharedFlow<String> = _toast.asSharedFlow()

    // --- Encouragement (bounce animation in timer area) ---
    private val _encouragement = MutableStateFlow<String?>(null)
    val encouragement: StateFlow<String?> = _encouragement.asStateFlow()

    fun clearEncouragement() { _encouragement.value = null }

    /** Show a bounce message in the timer area. Auto-clears after [durationMs]. */
    fun showMessage(msg: String, durationMs: Long = 4000L) {
        _encouragement.value = msg
        viewModelScope.launch {
            delay(durationMs)
            // Only clear if still showing the same message
            if (_encouragement.value == msg) _encouragement.value = null
        }
    }

    // --- Pure client-side timer with gradual drift correction ---
    // Instead of anchoring to server elapsed and interpolating forward (which causes
    // visible bouncing when server updates arrive late), we maintain a local start time
    // and count up independently. On each server update, we blend toward the server value
    // using exponential smoothing — never snapping, always smooth.
    private var timerStartMs = 0L       // SystemClock.elapsedRealtime() base
    private var timerInitialized = false

    // --- Dirty guard timestamps ---
    private var dirtySpeed = 0L
    private var dirtyIncline = 0L

    // --- Debounced API calls ---
    private val debouncedSetSpeed = TrailingDebounce<Double>(150, viewModelScope) { mph ->
        runCatching { api.setSpeed(SpeedRequest(mph)) }
            .onFailure { Log.e(TAG, "Failed to set speed", it) }
    }

    private val debouncedSetIncline = TrailingDebounce<Double>(150, viewModelScope) { inc ->
        runCatching { api.setIncline(InclineRequest(inc)) }
            .onFailure { Log.e(TAG, "Failed to set incline", it) }
    }

    // --- Leading guards ---
    private val startGuard = LeadingGuard(1000)
    private val stopGuard = LeadingGuard(1000)
    private val pauseGuard = LeadingGuard(500)
    private val skipGuard = LeadingGuard(400)
    private val prevGuard = LeadingGuard(400)
    private val extendGuard = LeadingGuard(400)
    private val resetGuard = LeadingGuard(1000)
    private val modeGuard = LeadingGuard(1000)

    // --- Derived state (with local clock interpolation at 10Hz) ---
    // Local ticker emits at 100ms intervals for smooth timer display
    private val localTicker = flow {
        while (true) {
            emit(SystemClock.elapsedRealtime())
            delay(100)
        }
    }

    val derivedSession: StateFlow<DerivedSession> = combine(
        _session, _status, _program, localTicker
    ) { sess, stat, pgm, now ->
        val speedMph = if (stat.emulate) stat.emuSpeed / 10.0 else (stat.speed ?: 0.0)

        // Pure client-side timer: count from local start, never snap
        val displayElapsed = if (sess.active && !pgm.paused && timerInitialized) {
            max(0.0, (now - timerStartMs) / 1000.0)
        } else {
            sess.elapsed
        }

        DerivedSession(
            active = sess.active,
            elapsed = sess.elapsed,
            elapsedDisplay = fmtDur(displayElapsed.toInt()),
            distance = sess.distance,
            distDisplay = "%.2f".format(sess.distance),
            vertFeet = sess.vertFeet,
            vertDisplay = sess.vertFeet.roundToInt().let { if (it >= 1000) "%,d".format(it) else it.toString() },
            calories = sess.calories,
            caloriesDisplay = sess.calories.roundToInt().let { if (it >= 1000) "%,d".format(it) else it.toString() },
            pace = paceDisplay(speedMph),
            speedMph = speedMph,
            endReason = sess.endReason,
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), DerivedSession(
        active = false, elapsed = 0.0, elapsedDisplay = "0:00", distance = 0.0,
        distDisplay = "0.00", vertFeet = 0.0, vertDisplay = "0", calories = 0.0,
        caloriesDisplay = "0", pace = "--:--", speedMph = 0.0, endReason = null,
    ))

    val derivedProgram: StateFlow<DerivedProgram> = _program.map { pgm ->
        computeDerivedProgram(pgm)
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), computeDerivedProgram(ProgramState()))

    init {
        // Connect WebSocket and collect messages
        viewModelScope.launch {
            prefs.serverUrl
                .distinctUntilChanged()
                .collect { url ->
                    if (url.isNotBlank()) {
                        webSocket.connect(url)
                    }
                }
        }

        viewModelScope.launch {
            webSocket.messages.collect { msg ->
                handleMessage(msg)
            }
        }

        // Fetch initial program state on connect
        viewModelScope.launch {
            webSocket.connected.collect { connected ->
                if (connected) {
                    runCatching {
                        val pgm = api.getProgram()
                        if (pgm.program != null) {
                            handleProgramUpdate(pgm)
                        }
                    }
                }
            }
        }
    }

    // --- Message handling ---

    private fun handleMessage(msg: ServerMessage) {
        when (msg) {
            is StatusMessage -> handleStatusUpdate(msg)
            is SessionMessage -> handleSessionUpdate(msg)
            is ProgramMessage -> handleProgramUpdate(msg)
            is ConnectionMessage -> handleConnectionUpdate(msg)
            is KVMessage -> handleKVUpdate(msg)
            is HRMessage -> handleHRUpdate(msg)
            is ScanResultMessage -> handleScanResult(msg)
            is ProfileChangedMessage -> handleProfileChanged(msg)
            is UnknownMessage -> {} // ignored
        }
    }

    private fun handleStatusUpdate(msg: StatusMessage) {
        val now = SystemClock.elapsedRealtime()
        val speedDirty = now - dirtySpeed < DIRTY_GRACE_MS
        val inclineDirty = now - dirtyIncline < DIRTY_GRACE_MS

        _status.update { cur ->
            cur.copy(
                proxy = msg.proxy,
                emulate = msg.emulate,
                emuSpeed = if (speedDirty) cur.emuSpeed else msg.emuSpeed,
                emuIncline = if (inclineDirty) cur.emuIncline else msg.emuIncline,
                speed = msg.speed ?: cur.speed,
                incline = msg.incline ?: cur.incline,
                motor = msg.motor,
                treadmillConnected = msg.treadmillConnected,
                heartRate = msg.heartRate,
                hrmConnected = msg.hrmConnected,
                hrmDevice = msg.hrmDevice,
            )
        }
    }

    private fun handleSessionUpdate(msg: SessionMessage) {
        val now = SystemClock.elapsedRealtime()
        if (msg.active) {
            val targetStartMs = now - (msg.elapsed * 1000).toLong()
            if (!timerInitialized) {
                // First update — snap to server elapsed
                timerStartMs = targetStartMs
                timerInitialized = true
            } else {
                // Gradual drift correction via exponential blend
                val drift = targetStartMs - timerStartMs
                if (abs(drift) > TIMER_SNAP_MS) {
                    timerStartMs = targetStartMs  // large drift (unpause, etc.) — snap
                } else {
                    timerStartMs += (drift * TIMER_BLEND).toLong()
                }
            }
        } else {
            timerInitialized = false
        }

        _session.update {
            SessionState(
                active = msg.active,
                elapsed = msg.elapsed,
                distance = msg.distance,
                vertFeet = msg.vertFeet,
                calories = msg.calories,
                wallStartedAt = msg.wallStartedAt,
                endReason = msg.endReason,
            )
        }
        if (!msg.active && msg.endReason != null) {
            val toastMsg = when (msg.endReason) {
                "disconnect" -> "Belt stopped — treadmill disconnected"
                else -> null
            }
            toastMsg?.let { _toast.tryEmit(it) }
        }
    }

    private fun handleProgramUpdate(msg: ProgramMessage) {
        _program.update { cur ->
            ProgramState(
                program = msg.program ?: cur.program,  // Keep existing if null
                running = msg.running,
                paused = msg.paused,
                completed = msg.completed,
                currentInterval = msg.currentInterval,
                intervalElapsed = msg.intervalElapsed,
                totalElapsed = msg.totalElapsed,
                totalDuration = msg.totalDuration,
            )
        }
        msg.encouragement?.let { _encouragement.value = it }
    }

    private fun handleConnectionUpdate(msg: ConnectionMessage) {
        _status.update { it.copy(treadmillConnected = msg.connected) }
    }

    private fun handleKVUpdate(msg: KVMessage) {
        val entry = KVEntry(
            ts = msg.ts?.let { "%.2f".format(it) } ?: "",
            src = msg.source,
            key = msg.key,
            value = msg.value,
        )
        _kvLog.update { log ->
            val newLog = log + entry
            if (newLog.size > MAX_KV_LOG) newLog.drop(100) else newLog
        }
        if (msg.source == "motor") {
            _status.update {
                val updated = it.motor + (msg.key to msg.value)
                it.copy(motor = if (updated.size > 20) updated.toList().takeLast(20).toMap() else updated)
            }
        }
    }

    private fun handleHRUpdate(msg: HRMessage) {
        _status.update { it.copy(
            heartRate = msg.bpm,
            hrmConnected = msg.connected,
            hrmDevice = msg.device,
        ) }
    }

    private fun handleScanResult(msg: ScanResultMessage) {
        _hrmDevices.value = msg.devices
    }

    private fun handleProfileChanged(msg: ProfileChangedMessage) {
        _activeProfile.value = msg.profile
        _guestMode.value = msg.guestMode
        _profileChanged.tryEmit(Unit)
    }

    // --- Actions ---

    fun setSpeed(mph: Double) {
        val clamped = (mph * 10).roundToInt().coerceIn(0, 120)
        dirtySpeed = SystemClock.elapsedRealtime()
        _status.update { it.copy(emuSpeed = clamped) }
        debouncedSetSpeed.invoke(mph)
    }

    fun setIncline(value: Double) {
        val clamped = (Math.round(value * 2) / 2.0).coerceIn(0.0, 99.0)
        dirtyIncline = SystemClock.elapsedRealtime()
        _status.update { it.copy(emuIncline = clamped) }
        debouncedSetIncline.invoke(clamped)
    }

    fun adjustSpeed(deltaTenths: Int) {
        val cur = _status.value.emuSpeed
        val newSpeed = (cur + deltaTenths).coerceIn(0, 120)
        dirtySpeed = SystemClock.elapsedRealtime()
        _status.update { it.copy(emuSpeed = newSpeed) }
        debouncedSetSpeed.invoke(newSpeed / 10.0)
    }

    fun adjustIncline(delta: Double) {
        val cur = _status.value.emuIncline
        val newInc = (Math.round((cur + delta) * 2) / 2.0).coerceIn(0.0, 99.0)
        dirtyIncline = SystemClock.elapsedRealtime()
        _status.update { it.copy(emuIncline = newInc) }
        debouncedSetIncline.invoke(newInc)
    }

    /** Emergency stop — never debounced, fires all three API calls simultaneously. */
    fun emergencyStop() {
        _status.update { it.copy(emuSpeed = 0, emuIncline = 0.0) }
        dirtySpeed = SystemClock.elapsedRealtime()
        dirtyIncline = SystemClock.elapsedRealtime()
        viewModelScope.launch {
            launch { runCatching { api.setSpeed(SpeedRequest(0.0)) }
                .onFailure { Log.e(TAG, "Failed to set speed (emergency)", it) } }
            launch { runCatching { api.setIncline(InclineRequest(0.0)) }
                .onFailure { Log.e(TAG, "Failed to set incline (emergency)", it) } }
            launch { runCatching { api.stopProgram() }
                .onFailure { Log.e(TAG, "Failed to stop program (emergency)", it) } }
        }
    }

    fun resetAll() {
        viewModelScope.launch {
            resetGuard.tryExecuteSuspend {
                _status.update { it.copy(emuSpeed = 0, emuIncline = 0.0) }
                dirtySpeed = SystemClock.elapsedRealtime()
                dirtyIncline = SystemClock.elapsedRealtime()
                runCatching { api.reset() }
                    .onFailure { Log.e(TAG, "Failed to reset", it) }
            }
        }
    }

    fun startProgram() {
        viewModelScope.launch {
            startGuard.tryExecuteSuspend {
                runCatching { api.startProgram() }
                    .onFailure {
                        Log.e(TAG, "Failed to start program", it)
                        _toast.tryEmit("Failed to start program")
                    }
            }
        }
    }

    fun stopProgram() {
        viewModelScope.launch {
            stopGuard.tryExecuteSuspend {
                runCatching { api.stopProgram() }
                    .onFailure {
                        Log.e(TAG, "Failed to stop program", it)
                        _toast.tryEmit("Failed to stop program")
                    }
            }
        }
    }

    fun pauseProgram() {
        viewModelScope.launch {
            pauseGuard.tryExecuteSuspend {
                runCatching { api.pauseProgram() }
                    .onFailure {
                        Log.e(TAG, "Failed to pause program", it)
                        _toast.tryEmit("Failed to pause program")
                    }
            }
        }
    }

    fun skipInterval() {
        viewModelScope.launch {
            skipGuard.tryExecuteSuspend {
                runCatching { api.skipInterval() }
                    .onFailure { Log.e(TAG, "Failed to skip interval", it) }
            }
        }
    }

    fun prevInterval() {
        viewModelScope.launch {
            prevGuard.tryExecuteSuspend {
                runCatching { api.prevInterval() }
                    .onFailure { Log.e(TAG, "Failed to go to previous interval", it) }
            }
        }
    }

    fun extendInterval(seconds: Int) {
        viewModelScope.launch {
            extendGuard.tryExecuteSuspend {
                runCatching { api.extendInterval(ExtendRequest(seconds)) }
                    .onFailure { Log.e(TAG, "Failed to extend interval", it) }
            }
        }
    }

    fun setMode(mode: String) {
        viewModelScope.launch {
            modeGuard.tryExecuteSuspend {
                if (mode == "emulate") {
                    runCatching { api.setEmulate(EmulateRequest(true)) }
                        .onFailure { Log.e(TAG, "Failed to set emulate mode", it) }
                } else {
                    runCatching { api.setProxy(ProxyRequest(true)) }
                        .onFailure { Log.e(TAG, "Failed to set proxy mode", it) }
                }
            }
        }
    }

    fun adjustDuration(deltaSeconds: Int) {
        viewModelScope.launch {
            runCatching { api.adjustDuration(AdjustDurationRequest(deltaSeconds)) }
                .onFailure { Log.e(TAG, "Failed to adjust duration", it) }
        }
    }

    fun quickStart(speed: Double = 3.0, incline: Double = 0.0, durationMinutes: Int = 60) {
        viewModelScope.launch {
            startGuard.tryExecuteSuspend {
                runCatching { api.quickStart(QuickStartRequest(speed, incline, durationMinutes)) }
                    .onFailure {
                        Log.e(TAG, "Failed to quick start", it)
                        _toast.tryEmit("Failed to start workout")
                    }
            }
        }
    }

    fun selectHrmDevice(address: String) {
        viewModelScope.launch {
            runCatching { api.selectHrmDevice(HrmSelectRequest(address)) }
                .onFailure { Log.e(TAG, "Failed to select HRM device", it) }
        }
    }

    fun forgetHrmDevice() {
        viewModelScope.launch {
            runCatching { api.forgetHrmDevice() }
                .onFailure { Log.e(TAG, "Failed to forget HRM device", it) }
        }
    }

    fun scanHrmDevices() {
        viewModelScope.launch {
            runCatching { api.scanHrmDevices() }
                .onFailure { Log.e(TAG, "Failed to scan HRM devices", it) }
        }
    }

    // --- Profile actions ---

    fun fetchProfiles() {
        viewModelScope.launch {
            runCatching { api.getProfiles() }
                .onSuccess { _profiles.value = it }
                .onFailure { Log.e(TAG, "Failed to fetch profiles", it) }
        }
    }

    fun fetchActiveProfile() {
        viewModelScope.launch {
            runCatching { api.getActiveProfile() }
                .onSuccess {
                    _activeProfile.value = it.profile
                    _guestMode.value = it.guestMode
                }
                .onFailure { Log.e(TAG, "Failed to fetch active profile", it) }
        }
    }

    fun stopAndReset(onDone: () -> Unit) {
        viewModelScope.launch {
            runCatching { api.stopProgram() }
            runCatching { api.reset() }
            onDone()
        }
    }

    fun selectProfile(id: String, onSuccess: () -> Unit = {}, onError: (String) -> Unit = {}) {
        viewModelScope.launch {
            runCatching { api.selectProfile(SelectProfileRequest(id)) }
                .onSuccess {
                    if (it.ok) {
                        fetchActiveProfile()
                        onSuccess()
                    } else {
                        onError(it.error ?: "Failed to select profile")
                    }
                }
                .onFailure {
                    Log.e(TAG, "Failed to select profile", it)
                    onError(it.message ?: "Network error")
                }
        }
    }

    fun startGuest(onSuccess: () -> Unit = {}, onError: (String) -> Unit = {}) {
        viewModelScope.launch {
            runCatching { api.startGuest() }
                .onSuccess {
                    if (it.ok) {
                        _guestMode.value = true
                        _activeProfile.value = null
                        onSuccess()
                    } else {
                        onError(it.error ?: "Failed to start guest mode")
                    }
                }
                .onFailure {
                    Log.e(TAG, "Failed to start guest mode", it)
                    onError(it.message ?: "Network error")
                }
        }
    }

    fun createProfile(
        name: String,
        color: String? = null,
        initials: String? = null,
        weightLbs: Double? = null,
        onSuccess: (Profile) -> Unit = {},
        onError: (String) -> Unit = {},
    ) {
        viewModelScope.launch {
            runCatching { api.createProfile(CreateProfileRequest(name, color, initials, weightLbs)) }
                .onSuccess {
                    fetchProfiles()
                    onSuccess(it)
                }
                .onFailure {
                    Log.e(TAG, "Failed to create profile", it)
                    onError(it.message ?: "Network error")
                }
        }
    }

    fun renameProfile(id: String, name: String, onSuccess: () -> Unit = {}, onError: (String) -> Unit = {}) {
        viewModelScope.launch {
            runCatching { api.updateProfile(id, UpdateProfileRequest(name = name)) }
                .onSuccess {
                    fetchActiveProfile()
                    onSuccess()
                }
                .onFailure {
                    Log.e(TAG, "Failed to rename profile", it)
                    onError(it.message ?: "Network error")
                }
        }
    }

    fun deleteProfile(id: String, onSuccess: () -> Unit = {}, onError: (String) -> Unit = {}) {
        viewModelScope.launch {
            runCatching { api.deleteProfile(id) }
                .onSuccess {
                    if (it.ok) {
                        fetchProfiles()
                        fetchActiveProfile()
                        onSuccess()
                    } else {
                        onError(it.error ?: "Cannot delete profile")
                    }
                }
                .onFailure {
                    Log.e(TAG, "Failed to delete profile", it)
                    onError(it.message ?: "Network error")
                }
        }
    }

    fun uploadAvatar(id: String, imageBytes: ByteArray, onSuccess: () -> Unit = {}, onError: (String) -> Unit = {}) {
        viewModelScope.launch {
            runCatching {
                val requestBody = imageBytes.toRequestBody("image/jpeg".toMediaType())
                val part = okhttp3.MultipartBody.Part.createFormData("file", "avatar.jpg", requestBody)
                api.uploadAvatar(id, part)
            }
                .onSuccess {
                    if (it.ok) {
                        fetchActiveProfile()
                        fetchProfiles()
                        onSuccess()
                    } else {
                        onError(it.error ?: "Upload failed")
                    }
                }
                .onFailure {
                    Log.e(TAG, "Failed to upload avatar", it)
                    onError(it.message ?: "Network error")
                }
        }
    }

    fun deleteAvatar(id: String, onSuccess: () -> Unit = {}, onError: (String) -> Unit = {}) {
        viewModelScope.launch {
            runCatching { api.deleteAvatar(id) }
                .onSuccess {
                    if (it.ok) {
                        fetchActiveProfile()
                        fetchProfiles()
                        onSuccess()
                    } else {
                        onError(it.error ?: "Failed to remove avatar")
                    }
                }
                .onFailure {
                    Log.e(TAG, "Failed to delete avatar", it)
                    onError(it.message ?: "Network error")
                }
        }
    }

    fun updateProfileColor(id: String, color: String, onSuccess: () -> Unit = {}, onError: (String) -> Unit = {}) {
        viewModelScope.launch {
            runCatching { api.updateProfile(id, UpdateProfileRequest(color = color)) }
                .onSuccess {
                    fetchActiveProfile()
                    fetchProfiles()
                    onSuccess()
                }
                .onFailure {
                    Log.e(TAG, "Failed to update profile color", it)
                    onError(it.message ?: "Network error")
                }
        }
    }

    fun convertGuest(
        name: String,
        color: String? = null,
        initials: String? = null,
        weightLbs: Double? = null,
        onSuccess: (Profile) -> Unit = {},
        onError: (String) -> Unit = {},
    ) {
        viewModelScope.launch {
            runCatching { api.convertGuest(ConvertGuestRequest(name, color, initials, weightLbs)) }
                .onSuccess {
                    _activeProfile.value = it
                    _guestMode.value = false
                    fetchProfiles()
                    onSuccess(it)
                }
                .onFailure {
                    Log.e(TAG, "Failed to convert guest", it)
                    onError(it.message ?: "Network error")
                }
        }
    }

    override fun onCleared() {
        super.onCleared()
        webSocket.disconnect()
    }

    // --- Elevation profile computation (port from useProgram.ts) ---

    private fun computeDerivedProgram(pgm: ProgramState): DerivedProgram {
        val intervals = pgm.program?.intervals ?: emptyList()
        val totalDur = if (pgm.totalDuration > 0) pgm.totalDuration else intervals.sumOf { it.duration.toDouble() }

        // Build staircase segments: one per interval with x, width, y
        var x = 0f
        val segments = mutableListOf<ElevationSegment>()
        var maxInc = 0.0
        if (totalDur > 0) {
            for (iv in intervals) {
                if (iv.incline > maxInc) maxInc = iv.incline
            }
        }
        // Autoscale Y-axis to smallest nice ceiling above max incline
        val yAxisMax = when {
            maxInc <= 0 -> 5f
            maxInc <= 5 -> 5f
            maxInc <= 10 -> 10f
            else -> 15f
        }
        if (totalDur > 0) {
            for (iv in intervals) {
                val segW = (iv.duration / totalDur * ELEV_W).toFloat()
                val y = (ELEV_H - ELEV_PAD - (iv.incline / yAxisMax) * (ELEV_H - ELEV_PAD * 2)).toFloat()
                segments.add(ElevationSegment(x, segW, y))
                x += segW
            }
        }

        val intervalBoundaryXs = if (totalDur > 0 && intervals.isNotEmpty()) {
            val xs = FloatArray(intervals.size + 1)
            var cumX = 0f
            for ((idx, iv) in intervals.withIndex()) {
                xs[idx] = cumX
                cumX += (iv.duration / totalDur * ELEV_W).toFloat()
            }
            xs[intervals.size] = ELEV_W
            xs
        } else {
            FloatArray(0)
        }

        val currentIv = if (pgm.program != null && pgm.currentInterval < intervals.size)
            intervals[pgm.currentInterval] else null
        val nextIv = if (pgm.program != null && pgm.currentInterval + 1 < intervals.size)
            intervals[pgm.currentInterval + 1] else null
        val ivRemaining = if (currentIv != null) max(0.0, currentIv.duration - pgm.intervalElapsed) else 0.0
        val totalRemaining = max(0.0, totalDur - pgm.totalElapsed)
        val ivPct = if (currentIv != null && currentIv.duration > 0)
            min(100.0, pgm.intervalElapsed / currentIv.duration * 100) else 0.0
        val timelinePos = if (totalDur > 0) min(100.0, pgm.totalElapsed / totalDur * 100) else 0.0
        val elevPosX = min(ELEV_W, (timelinePos / 100 * ELEV_W).toFloat())
        val elevPosY = if (segments.isNotEmpty()) evalStaircaseY(segments, elevPosX) else ELEV_H / 2

        return DerivedProgram(
            program = pgm.program,
            running = pgm.running,
            paused = pgm.paused,
            completed = pgm.completed,
            currentInterval = pgm.currentInterval,
            intervalElapsed = pgm.intervalElapsed,
            totalElapsed = pgm.totalElapsed,
            totalDuration = totalDur,
            currentIv = currentIv,
            nextIv = nextIv,
            ivRemaining = ivRemaining,
            totalRemaining = totalRemaining,
            ivPct = ivPct,
            timelinePos = timelinePos,
            segments = segments,
            elevPosX = elevPosX,
            elevPosY = elevPosY,
            maxIncline = maxInc,
            yAxisMax = yAxisMax,
            intervalCount = intervals.size,
            intervalBoundaryXs = intervalBoundaryXs,
        )
    }

    companion object {
        const val ELEV_WIDTH = ELEV_W
        const val ELEV_HEIGHT = ELEV_H
        const val ELEV_PADDING = ELEV_PAD

        /** Ramp width in chart units — represents incline transition time.
         *  The ramp starts at the interval boundary and slopes into the next interval. */
        private const val RAMP_W = 8f

        /** Evaluate staircase-with-ramps Y at a given x position. */
        fun evalStaircaseY(segments: List<ElevationSegment>, x: Float): Float {
            val n = segments.size
            if (n == 0) return ELEV_H / 2
            if (n == 1) return segments[0].y

            for (i in 0 until n - 1) {
                val seg = segments[i]
                val next = segments[i + 1]
                val boundary = seg.x + seg.w

                if (x <= boundary) return seg.y

                if (seg.y != next.y) {
                    val ramp = minOf(RAMP_W, next.w * 0.3f)
                    if (x < boundary + ramp) {
                        val t = (x - boundary) / ramp
                        return seg.y + t * (next.y - seg.y)
                    }
                }
            }

            return segments[n - 1].y
        }
    }
}
