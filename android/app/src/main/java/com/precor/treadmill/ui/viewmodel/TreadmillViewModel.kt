package com.precor.treadmill.ui.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.precor.treadmill.data.preferences.ServerPreferences
import com.precor.treadmill.data.remote.TreadmillApi
import com.precor.treadmill.data.remote.TreadmillWebSocket
import com.precor.treadmill.data.remote.models.*
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
    val pace: String,
    val speedMph: Double,
    val endReason: String?,
)

data class ElevationPoint(val x: Float, val y: Float)

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
    val points: List<ElevationPoint>,
    val tangents: FloatArray,
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
                timelinePos == other.timelinePos && points == other.points &&
                tangents.contentEquals(other.tangents) && elevPosX == other.elevPosX &&
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

    // --- Local clock interpolation ---
    private var lastServerElapsed = 0.0
    private var lastServerTimestamp = SystemClock.elapsedRealtime()

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

        // Interpolate elapsed locally between server updates
        val displayElapsed = if (sess.active && !pgm.paused) {
            val delta = (now - lastServerTimestamp) / 1000.0
            val interpolated = lastServerElapsed + delta
            // Clamp: never go backwards, cap at server + 1.5s tolerance
            max(lastServerElapsed, min(interpolated, lastServerElapsed + 1.5))
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
            pace = paceDisplay(speedMph),
            speedMph = speedMph,
            endReason = sess.endReason,
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), DerivedSession(
        active = false, elapsed = 0.0, elapsedDisplay = "0:00", distance = 0.0,
        distDisplay = "0.00", vertFeet = 0.0, vertDisplay = "0", pace = "--:--",
        speedMph = 0.0, endReason = null,
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
        // Update local clock anchor for interpolation
        lastServerElapsed = msg.elapsed
        lastServerTimestamp = SystemClock.elapsedRealtime()

        _session.update {
            SessionState(
                active = msg.active,
                elapsed = msg.elapsed,
                distance = msg.distance,
                vertFeet = msg.vertFeet,
                wallStartedAt = msg.wallStartedAt,
                endReason = msg.endReason,
            )
        }
        if (!msg.active && msg.endReason != null) {
            val toastMsg = when (msg.endReason) {
                "watchdog" -> "Belt stopped — heartbeat lost"
                "auto_proxy" -> "Belt stopped — console took over"
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

    override fun onCleared() {
        super.onCleared()
        webSocket.disconnect()
    }

    // --- Elevation profile computation (port from useProgram.ts) ---

    private fun computeDerivedProgram(pgm: ProgramState): DerivedProgram {
        val intervals = pgm.program?.intervals ?: emptyList()
        val totalDur = if (pgm.totalDuration > 0) pgm.totalDuration else intervals.sumOf { it.duration.toDouble() }

        // Build data points at interval midpoints
        var x = 0f
        val points = mutableListOf<ElevationPoint>()
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
                val midX = x + segW / 2
                val y = (ELEV_H - ELEV_PAD - (iv.incline / yAxisMax) * (ELEV_H - ELEV_PAD * 2)).toFloat()
                points.add(ElevationPoint(midX, y))
                x += segW
            }
        }

        val tangents = computeTangents(points)

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
        val elevPosY = if (points.isNotEmpty()) evalSplineY(points, tangents, elevPosX) else ELEV_H / 2

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
            points = points,
            tangents = tangents,
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

        /** Fritsch-Carlson monotone cubic tangent computation. */
        fun computeTangents(points: List<ElevationPoint>): FloatArray {
            val n = points.size
            if (n < 2) return FloatArray(n) { 0f }

            val m = FloatArray(n - 1)
            val dx = FloatArray(n - 1)
            for (i in 0 until n - 1) {
                val d = points[i + 1].x - points[i].x
                dx[i] = d
                m[i] = if (d == 0f) 0f else (points[i + 1].y - points[i].y) / d
            }

            val tangents = FloatArray(n)
            tangents[0] = m[0]
            tangents[n - 1] = m[n - 2]
            for (i in 1 until n - 1) {
                tangents[i] = if (m[i - 1] * m[i] <= 0) 0f else (m[i - 1] + m[i]) / 2
            }

            // Fritsch-Carlson: clamp tangents for monotonicity
            for (i in 0 until n - 1) {
                if (m[i] == 0f) {
                    tangents[i] = 0f
                    tangents[i + 1] = 0f
                } else {
                    val alpha = tangents[i] / m[i]
                    val beta = tangents[i + 1] / m[i]
                    val s = alpha * alpha + beta * beta
                    if (s > 9) {
                        val tau = 3f / sqrt(s)
                        tangents[i] = tau * alpha * m[i]
                        tangents[i + 1] = tau * beta * m[i]
                    }
                }
            }

            return tangents
        }

        /** Evaluate the monotone cubic spline at a given x position. */
        fun evalSplineY(points: List<ElevationPoint>, tangents: FloatArray, x: Float): Float {
            val n = points.size
            if (n == 0) return ELEV_H / 2
            if (x <= points[0].x) return points[0].y
            if (x >= points[n - 1].x) return points[n - 1].y

            // Find segment
            var i = 0
            while (i < n - 2 && x >= points[i + 1].x) i++

            val p0 = points[i]
            val p1 = points[i + 1]
            val d = p1.x - p0.x
            if (d == 0f) return p0.y

            // Hermite basis evaluation
            val t = (x - p0.x) / d
            val t2 = t * t
            val t3 = t2 * t
            val h00 = 2 * t3 - 3 * t2 + 1
            val h10 = t3 - 2 * t2 + t
            val h01 = -2 * t3 + 3 * t2
            val h11 = t3 - t2

            return h00 * p0.y + h10 * (tangents[i] * d) + h01 * p1.y + h11 * (tangents[i + 1] * d)
        }
    }
}
