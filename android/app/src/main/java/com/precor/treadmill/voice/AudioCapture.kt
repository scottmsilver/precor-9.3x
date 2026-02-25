package com.precor.treadmill.voice

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.os.SystemClock
import android.util.Base64
import android.util.Log
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Captures 16kHz mono PCM16 audio from the microphone and emits
 * base64-encoded chunks via callback.
 *
 * Supports a pre-warm lifecycle: warmUp() creates AudioRecord + AEC
 * without starting capture, so start() is near-instant.
 */
class AudioCapture(
    private var onAudioChunk: (pcmBase64: String) -> Unit,
) {
    companion object {
        private const val TAG = "AudioCapture"
        private const val SAMPLE_RATE = 16000
        private const val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
        private const val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT
        private const val BUFFER_SIZE_SAMPLES = 4096
        private const val SILENCE_RMS_THRESHOLD = 800   // PCM16 amplitude; speech is typically 2000+
        private const val SILENCE_CHUNKS_REQUIRED = 2    // consecutive silent chunks before "went silent"
    }

    private var audioRecord: AudioRecord? = null
    private var echoCanceler: AcousticEchoCanceler? = null
    private var captureThread: Thread? = null
    @Volatile
    private var isRecording = false

    // Speech/silence tracking — timestamps accessible from GeminiLiveClient
    @Volatile
    var lastSpeechMs: Long = 0L          // last time speech was detected
        private set
    @Volatile
    var silenceStartMs: Long = 0L        // when speech→silence transition happened
        private set
    @Volatile
    var isSpeaking: Boolean = false       // currently above threshold
        private set

    /** Set before start() to save raw PCM to a file for offline replay testing. */
    var recordingPath: String? = null
    private var recordingStream: BufferedOutputStream? = null

    fun updateCallback(cb: (String) -> Unit) {
        onAudioChunk = cb
    }

    /** Pre-create AudioRecord + AEC without starting recording. Makes start() near-instant. */
    @SuppressLint("MissingPermission")
    fun warmUp(): Boolean {
        if (audioRecord != null) return true

        val minBufferSize = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)
        val bufferSize = maxOf(minBufferSize, BUFFER_SIZE_SAMPLES * 2)

        return try {
            val record = AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                SAMPLE_RATE,
                CHANNEL_CONFIG,
                AUDIO_FORMAT,
                bufferSize,
            )

            if (record.state != AudioRecord.STATE_INITIALIZED) {
                Log.e(TAG, "AudioRecord failed to initialize during warmUp")
                record.release()
                return false
            }

            if (AcousticEchoCanceler.isAvailable()) {
                echoCanceler = AcousticEchoCanceler.create(record.audioSessionId)?.also {
                    it.enabled = true
                    Log.d(TAG, "AcousticEchoCanceler enabled (warmUp)")
                }
            }

            audioRecord = record
            Log.d(TAG, "AudioRecord warmed up at ${SAMPLE_RATE}Hz")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to warm up AudioRecord", e)
            false
        }
    }

    @SuppressLint("MissingPermission")
    fun start(): Boolean {
        if (isRecording) return true
        if (audioRecord == null && !warmUp()) return false

        val record = audioRecord ?: return false

        return try {
            isRecording = true
            record.startRecording()

            // Open recording file if path is set
            recordingPath?.let { path ->
                try {
                    val file = File(path)
                    file.parentFile?.mkdirs()
                    recordingStream = BufferedOutputStream(FileOutputStream(file))
                    Log.i(TAG, "Recording PCM to: $path")
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to open recording file: $path", e)
                }
            }

            captureThread = Thread({
                val buffer = ByteArray(BUFFER_SIZE_SAMPLES * 2) // 2 bytes per sample
                var silentChunks = 0
                while (isRecording && !Thread.currentThread().isInterrupted) {
                    val bytesRead = record.read(buffer, 0, buffer.size)
                    if (bytesRead > 0) {
                        // Save raw PCM for replay testing
                        recordingStream?.write(buffer, 0, bytesRead)

                        // RMS amplitude for speech/silence detection
                        val rms = computeRms(buffer, bytesRead)
                        val now = SystemClock.elapsedRealtime()
                        if (rms >= SILENCE_RMS_THRESHOLD) {
                            if (!isSpeaking) {
                                isSpeaking = true
                                Log.i("VoiceTiming", "SPEECH_START: rms=$rms")
                            }
                            lastSpeechMs = now
                            silentChunks = 0
                        } else {
                            silentChunks++
                            if (isSpeaking && silentChunks >= SILENCE_CHUNKS_REQUIRED) {
                                isSpeaking = false
                                silenceStartMs = now
                                Log.i("VoiceTiming", "SPEECH_END: silent after ${now - lastSpeechMs}ms, rms=$rms")
                            }
                        }

                        val encoded = Base64.encodeToString(
                            buffer, 0, bytesRead, Base64.NO_WRAP
                        )
                        try {
                            onAudioChunk(encoded)
                        } catch (e: Exception) {
                            Log.e(TAG, "Error in onAudioChunk callback", e)
                            break
                        }
                    }
                }
            }, "AudioCapture").also { it.start() }

            Log.d(TAG, "Recording started at ${SAMPLE_RATE}Hz")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start recording", e)
            isRecording = false
            false
        }
    }

    /** Stop recording but keep AudioRecord + AEC alive for reuse. */
    fun stop() {
        isRecording = false
        captureThread?.let { thread ->
            thread.join(2000)
            if (thread.isAlive) {
                Log.w(TAG, "Capture thread did not exit, interrupting")
                thread.interrupt()
            }
        }
        captureThread = null
        try { audioRecord?.stop() } catch (_: Exception) {}
        recordingStream?.let {
            try { it.flush(); it.close() } catch (_: Exception) {}
            Log.i(TAG, "Recording saved to: $recordingPath")
        }
        recordingStream = null
        Log.d(TAG, "Recording stopped (AudioRecord kept for reuse)")
    }

    /** Compute RMS amplitude of PCM16 LE samples. */
    private fun computeRms(buffer: ByteArray, bytesRead: Int): Int {
        val shortBuf = ByteBuffer.wrap(buffer, 0, bytesRead)
            .order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
        var sumSquares = 0L
        val count = shortBuf.remaining()
        for (i in 0 until count) {
            val sample = shortBuf.get(i).toLong()
            sumSquares += sample * sample
        }
        return if (count > 0) kotlin.math.sqrt(sumSquares.toDouble() / count).toInt() else 0
    }

    /** Fully release all resources. Call when done with this capture instance. */
    fun release() {
        stop()
        echoCanceler?.let {
            try { it.release() } catch (_: Exception) {}
        }
        echoCanceler = null
        audioRecord?.let {
            try { it.release() } catch (_: Exception) {}
        }
        audioRecord = null
        Log.d(TAG, "AudioCapture released")
    }
}
