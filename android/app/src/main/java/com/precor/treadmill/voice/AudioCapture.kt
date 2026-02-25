package com.precor.treadmill.voice

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.util.Base64
import android.util.Log

/**
 * Captures 16kHz mono PCM16 audio from the microphone and emits
 * base64-encoded chunks via callback.
 */
class AudioCapture(
    private val onAudioChunk: (pcmBase64: String) -> Unit,
) {
    companion object {
        private const val TAG = "AudioCapture"
        private const val SAMPLE_RATE = 16000
        private const val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
        private const val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT
        private const val BUFFER_SIZE_SAMPLES = 4096
    }

    private var audioRecord: AudioRecord? = null
    private var echoCanceler: AcousticEchoCanceler? = null
    private var captureThread: Thread? = null
    @Volatile
    private var isRecording = false

    @SuppressLint("MissingPermission")
    fun start(): Boolean {
        if (isRecording) return true

        val minBufferSize = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)
        val bufferSize = maxOf(minBufferSize, BUFFER_SIZE_SAMPLES * 2) // bytes for PCM16

        return try {
            val record = AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                SAMPLE_RATE,
                CHANNEL_CONFIG,
                AUDIO_FORMAT,
                bufferSize,
            )

            if (record.state != AudioRecord.STATE_INITIALIZED) {
                Log.e(TAG, "AudioRecord failed to initialize")
                record.release()
                return false
            }

            if (AcousticEchoCanceler.isAvailable()) {
                echoCanceler = AcousticEchoCanceler.create(record.audioSessionId)?.also {
                    it.enabled = true
                    Log.d(TAG, "AcousticEchoCanceler enabled")
                }
            }

            audioRecord = record
            isRecording = true
            record.startRecording()

            captureThread = Thread({
                val buffer = ByteArray(BUFFER_SIZE_SAMPLES * 2) // 2 bytes per sample
                while (isRecording && !Thread.currentThread().isInterrupted) {
                    val bytesRead = record.read(buffer, 0, buffer.size)
                    if (bytesRead > 0) {
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
            false
        }
    }

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
        echoCanceler?.let {
            try { it.release() } catch (_: Exception) {}
        }
        echoCanceler = null
        audioRecord?.let {
            try { it.stop() } catch (_: Exception) {}
            try { it.release() } catch (_: Exception) {}
        }
        audioRecord = null
        Log.d(TAG, "Recording stopped")
    }
}
