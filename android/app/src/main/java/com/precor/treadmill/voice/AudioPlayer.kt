package com.precor.treadmill.voice

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Build
import android.os.Process
import android.os.SystemClock
import android.util.Base64
import android.util.Log
import java.io.ByteArrayOutputStream
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

/**
 * Audio player for 24kHz mono PCM16 from Gemini Live.
 *
 * Single persistent playback thread (producer-consumer). All AudioTrack
 * operations happen on the playback thread only — no cross-thread races.
 * Uses non-blocking writes so the thread can respond to flush() instantly.
 */
class AudioPlayer(
    private val sampleRate: Int = 24000,
    private val onPlaybackStart: (() -> Unit)? = null,
    private val onPlaybackEnd: (() -> Unit)? = null,
) {
    companion object {
        private const val TAG = "AudioPlayer"
        private const val BATCH_MIN_BYTES = 9600  // 200ms at 24kHz mono PCM16
        private const val MAX_QUEUE_BYTES = 480000  // 10 seconds
        private const val PREBUFFER_MS = 150
    }

    private val queue = ConcurrentLinkedQueue<ByteArray>()
    private var audioTrack: AudioTrack? = null
    private var playbackThread: Thread? = null
    private val queuedBytes = AtomicInteger(0)
    private val firstEnqueueMs = AtomicLong(0)

    private val threadRunning = AtomicBoolean(false)
    @Volatile private var released = false

    // Flush signal — only the playback thread touches AudioTrack
    private val flushRequested = AtomicBoolean(false)

    private val sessionActive = AtomicBoolean(false)

    private fun ensureTrack(): AudioTrack {
        audioTrack?.let { return it }

        val minBuffer = AudioTrack.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )

        val bufSize = maxOf(minBuffer, sampleRate * 2 * 2)  // 2 seconds
        Log.d(TAG, "AudioTrack: minBuffer=${minBuffer}B, bufSize=${bufSize}B")

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

    /**
     * Write all of [data] to [track] using non-blocking writes.
     * Checks [flushRequested] between retries so we never get stuck.
     */
    private fun writeAll(track: AudioTrack, data: ByteArray): Boolean {
        var offset = 0
        while (offset < data.size && !flushRequested.get()) {
            val written = track.write(data, offset, data.size - offset, AudioTrack.WRITE_NON_BLOCKING)
            if (written > 0) {
                offset += written
            } else if (written == 0) {
                // Buffer full — wait briefly, then retry (checking flush flag)
                Thread.sleep(5)
            } else {
                Log.e(TAG, "AudioTrack.write error: $written")
                return false
            }
        }
        return !flushRequested.get()
    }

    private fun ensureThread() {
        if (!threadRunning.compareAndSet(false, true)) return
        playbackThread = Thread({
            Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
            val track = ensureTrack()
            Log.d(TAG, "Playback thread started")

            while (!released) {
                // Wait for audio to arrive
                if (queue.isEmpty()) {
                    Thread.sleep(5)
                    continue
                }

                // New playback session — pre-buffer then play
                flushRequested.set(false)
                val prebufferBytes = sampleRate * 2 * PREBUFFER_MS / 1000
                var waited = 0
                while (queuedBytes.get() < prebufferBytes && waited < 1000 && !flushRequested.get()) {
                    Thread.sleep(10)
                    waited += 10
                }

                if (flushRequested.get()) {
                    flushRequested.set(false)
                    continue
                }

                val sinceFirstEnqueue = SystemClock.elapsedRealtime() - firstEnqueueMs.get()
                Log.d(TAG, "Pre-buffer: waited=${waited}ms, queued=${queuedBytes.get()}B")
                Log.i("VoiceTiming", "PLAYER_PLAY_START: ${sinceFirstEnqueue}ms after first enqueue (prebuf waited ${waited}ms)")

                val underrunsBefore = if (Build.VERSION.SDK_INT >= 24) track.underrunCount else -1
                track.play()
                sessionActive.set(true)
                onPlaybackStart?.invoke()

                val accumulator = ByteArrayOutputStream(BATCH_MIN_BYTES * 2)
                var emptyMs = 0
                var bytesWritten = 0L
                var writesCount = 0

                // Drain loop — plays until queue empties or flush requested
                while (!flushRequested.get()) {
                    val chunk = queue.poll()
                    if (chunk != null) {
                        queuedBytes.addAndGet(-chunk.size)
                        accumulator.write(chunk)
                        emptyMs = 0

                        if (accumulator.size() >= BATCH_MIN_BYTES || queue.isEmpty()) {
                            val batch = accumulator.toByteArray()
                            if (!writeAll(track, batch)) break  // flushed during write
                            bytesWritten += batch.size
                            writesCount++
                            accumulator.reset()
                        }
                    } else {
                        if (accumulator.size() > 0) {
                            val batch = accumulator.toByteArray()
                            if (!writeAll(track, batch)) break
                            bytesWritten += batch.size
                            writesCount++
                            accumulator.reset()
                        }
                        Thread.sleep(10)
                        emptyMs += 10
                        if (emptyMs >= 1500) break  // session done
                    }
                }

                // End of session — all AudioTrack ops on THIS thread only
                if (flushRequested.get()) {
                    track.pause()
                    track.flush()
                    track.stop()
                    queue.clear()
                    queuedBytes.set(0)
                    sessionActive.set(false)
                    flushRequested.set(false)
                    Log.d(TAG, "Playback flushed (barge-in)")
                } else {
                    track.stop()
                    sessionActive.set(false)
                    val underrunsAfter = if (Build.VERSION.SDK_INT >= 24) track.underrunCount else -1
                    val audioDurMs = bytesWritten * 1000 / (sampleRate * 2)
                    val halUnderruns = if (underrunsBefore >= 0) underrunsAfter - underrunsBefore else -1
                    Log.d(TAG, "Playback done: $writesCount writes, ${bytesWritten}B (${audioDurMs}ms audio), HAL underruns=$halUnderruns")
                    onPlaybackEnd?.invoke()
                }
            }

            threadRunning.set(false)
            Log.d(TAG, "Playback thread exiting")
        }, "AudioPlayer").also { it.start() }
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

        if (!sessionActive.get() && queue.isEmpty()) {
            firstEnqueueMs.set(SystemClock.elapsedRealtime())
            Log.i("VoiceTiming", "PLAYER_FIRST_ENQUEUE: ${bytes.size}B (${bytes.size * 1000 / (sampleRate * 2)}ms audio)")
        }

        queue.add(bytes)
        queuedBytes.addAndGet(bytes.size)
        ensureThread()
    }

    /** Flush all queued and playing audio (barge-in). Returns immediately. */
    fun flush() {
        // Signal the playback thread — it handles all AudioTrack ops
        flushRequested.set(true)
        // Pre-clear queue so new data doesn't accumulate while thread catches up
        queue.clear()
        queuedBytes.set(0)
    }

    fun release() {
        released = true
        flush()
        playbackThread?.join(2000)
        playbackThread = null
        audioTrack?.release()
        audioTrack = null
    }
}
