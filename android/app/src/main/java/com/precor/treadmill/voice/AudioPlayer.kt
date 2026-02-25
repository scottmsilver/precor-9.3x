package com.precor.treadmill.voice

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Build
import android.os.Process
import android.util.Base64
import android.util.Log
import java.io.ByteArrayOutputStream
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * Audio player for 24kHz mono PCM16 from Gemini Live.
 *
 * Key design decisions:
 * - 2-second AudioTrack buffer (large enough that the HAL never starves)
 * - 300ms pre-buffer before calling play() (head start)
 * - Batch small chunks into >=200ms writes (avoids sub-HAL-period writes)
 * - URGENT_AUDIO thread priority
 */
class AudioPlayer(
    private val sampleRate: Int = 24000,
    private val onPlaybackStart: (() -> Unit)? = null,
    private val onPlaybackEnd: (() -> Unit)? = null,
) {
    companion object {
        private const val TAG = "AudioPlayer"
        // Batch at least 200ms of audio before writing to AudioTrack.
        // This avoids writing tiny 40ms chunks that may be smaller than
        // the HAL buffer period, which can cause stuttering on some devices.
        private const val BATCH_MIN_BYTES = 9600  // 200ms at 24kHz mono PCM16
        private const val MAX_QUEUE_BYTES = 480000  // 10 seconds at 24kHz mono PCM16
    }

    private val queue = ConcurrentLinkedQueue<ByteArray>()
    private var audioTrack: AudioTrack? = null
    private var playbackThread: Thread? = null
    private val isPlaying = AtomicBoolean(false)
    private val isFlushing = AtomicBoolean(false)
    private val queuedBytes = AtomicInteger(0)
    private val playbackLock = Any()

    private fun ensureTrack(): AudioTrack {
        audioTrack?.let { return it }

        val minBuffer = AudioTrack.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )

        // 2-second buffer — large enough that the HAL never underruns
        val bufSize = maxOf(minBuffer, sampleRate * 2 * 2)
        Log.d(TAG, "AudioTrack: minBuffer=${minBuffer}B, bufSize=${bufSize}B (${bufSize * 1000 / (sampleRate * 2)}ms)")

        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(sampleRate)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .build()
            )
            .setBufferSizeInBytes(bufSize)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()

        audioTrack = track
        return track
    }

    fun enqueue(pcmBase64: String) {
        val bytes = try {
            Base64.decode(pcmBase64, Base64.DEFAULT)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to decode audio chunk", e)
            return
        }

        if (queuedBytes.get() + bytes.size > MAX_QUEUE_BYTES) {
            Log.w(TAG, "Queue overflow (${queuedBytes.get()}B), dropping chunk")
            return
        }

        queue.add(bytes)
        queuedBytes.addAndGet(bytes.size)
        synchronized(playbackLock) {
            if (playbackThread?.isAlive != true) {
                isPlaying.set(true)
                startPlaybackThread()
            }
        }
    }

    private fun startPlaybackThread() {
        playbackThread = Thread({
            Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
            val track = ensureTrack()

            // Pre-buffer: wait for 300ms of audio before starting playback
            val prebufferBytes = sampleRate * 2 * 300 / 1000  // 14400 bytes
            var waited = 0
            while (queuedBytes.get() < prebufferBytes && waited < 1000 && !isFlushing.get()) {
                Thread.sleep(10)
                waited += 10
            }
            Log.d(TAG, "Pre-buffer: waited=${waited}ms, queued=${queuedBytes.get()}B")

            val underrunsBefore = if (Build.VERSION.SDK_INT >= 24) track.underrunCount else -1
            track.play()
            onPlaybackStart?.invoke()

            val accumulator = ByteArrayOutputStream(BATCH_MIN_BYTES * 2)
            var emptyMs = 0
            var bytesWritten = 0L
            var writesCount = 0

            while (!isFlushing.get()) {
                val chunk = queue.poll()
                if (chunk != null) {
                    queuedBytes.addAndGet(-chunk.size)
                    accumulator.write(chunk)
                    emptyMs = 0

                    // Write when we have enough data, OR when the queue has drained
                    // (for the large first chunk, accumulator.size() will exceed the
                    // threshold immediately so it writes without delay)
                    if (accumulator.size() >= BATCH_MIN_BYTES || queue.isEmpty()) {
                        val batch = accumulator.toByteArray()
                        track.write(batch, 0, batch.size)
                        bytesWritten += batch.size
                        writesCount++
                        accumulator.reset()
                    }
                } else {
                    // Queue empty — flush any remaining accumulated data first
                    if (accumulator.size() > 0) {
                        val batch = accumulator.toByteArray()
                        track.write(batch, 0, batch.size)
                        bytesWritten += batch.size
                        writesCount++
                        accumulator.reset()
                    }
                    Thread.sleep(10)
                    emptyMs += 10
                    if (emptyMs >= 1500) break
                }
            }

            // Final flush of accumulator
            if (accumulator.size() > 0) {
                val batch = accumulator.toByteArray()
                track.write(batch, 0, batch.size)
                bytesWritten += batch.size
                writesCount++
            }

            track.stop()

            val underrunsAfter = if (Build.VERSION.SDK_INT >= 24) track.underrunCount else -1
            val audioDurMs = bytesWritten * 1000 / (sampleRate * 2)
            val halUnderruns = if (underrunsBefore >= 0) underrunsAfter - underrunsBefore else -1
            Log.d(TAG, "Playback done: $writesCount writes, ${bytesWritten}B (${audioDurMs}ms audio), HAL underruns=$halUnderruns")

            isPlaying.set(false)
            isFlushing.set(false)
            onPlaybackEnd?.invoke()
        }, "AudioPlayer").also { it.start() }
    }

    /** Flush all queued and playing audio (barge-in). */
    fun flush() {
        queue.clear()
        queuedBytes.set(0)
        isFlushing.set(true)
        audioTrack?.let {
            try {
                it.pause()
                it.flush()
            } catch (_: Exception) { }
        }
        playbackThread?.join(500)
        playbackThread = null
        isPlaying.set(false)
        isFlushing.set(false)
    }

    fun release() {
        flush()
        audioTrack?.release()
        audioTrack = null
    }
}
