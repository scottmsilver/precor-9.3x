package com.precor.treadmill.voice

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.util.Base64
import android.util.Log
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Queued audio player for 24kHz mono PCM16 playback.
 * Supports flushing for barge-in (interrupt).
 */
class AudioPlayer(
    private val sampleRate: Int = 24000,
    private val onPlaybackStart: (() -> Unit)? = null,
    private val onPlaybackEnd: (() -> Unit)? = null,
) {
    companion object {
        private const val TAG = "AudioPlayer"
    }

    private val queue = ConcurrentLinkedQueue<ByteArray>()
    private var audioTrack: AudioTrack? = null
    private var playbackThread: Thread? = null
    private val isPlaying = AtomicBoolean(false)
    private val isFlushing = AtomicBoolean(false)

    private fun ensureTrack(): AudioTrack {
        audioTrack?.let { return it }

        val minBuffer = AudioTrack.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )

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
            .setBufferSizeInBytes(minBuffer * 2)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()

        audioTrack = track
        return track
    }

    fun enqueue(pcmBase64: String) {
        val bytes = Base64.decode(pcmBase64, Base64.DEFAULT)
        queue.add(bytes)
        startPlaybackThread()
    }

    private fun startPlaybackThread() {
        if (isPlaying.getAndSet(true)) return

        playbackThread = Thread({
            val track = ensureTrack()
            track.play()
            onPlaybackStart?.invoke()

            while (!isFlushing.get()) {
                val chunk = queue.poll()
                if (chunk == null) {
                    // No more data — wait briefly for new chunks
                    Thread.sleep(10)
                    if (queue.isEmpty()) break
                    continue
                }
                track.write(chunk, 0, chunk.size)
            }

            track.stop()
            isPlaying.set(false)
            isFlushing.set(false)
            onPlaybackEnd?.invoke()
        }, "AudioPlayer").also { it.start() }
    }

    /** Flush all queued and playing audio (barge-in). */
    fun flush() {
        queue.clear()
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
        Log.d(TAG, "Audio flushed")
    }

    fun release() {
        flush()
        audioTrack?.release()
        audioTrack = null
    }
}
