/**
 * PCM audio utilities for Gemini Live API.
 * Handles base64 encoding/decoding of PCM16 audio and playback queue.
 */

/** Convert Float32Array (from Web Audio) to 16-bit PCM Uint8Array (little-endian). */
export function float32ToPcm16(float32: Float32Array): Uint8Array {
  const pcm = new Uint8Array(float32.length * 2);
  const view = new DataView(pcm.buffer);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }
  return pcm;
}

/** Encode Uint8Array to base64 string. */
export function uint8ToBase64(bytes: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

/** Decode base64 string to Uint8Array. */
export function base64ToUint8(b64: string): Uint8Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

/** Convert 16-bit PCM (little-endian) to Float32Array for AudioContext playback. */
export function pcm16ToFloat32(pcm: Uint8Array): Float32Array {
  const samples = pcm.length / 2;
  const float32 = new Float32Array(samples);
  const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  for (let i = 0; i < samples; i++) {
    const val = view.getInt16(i * 2, true);
    float32[i] = val / (val < 0 ? 0x8000 : 0x7FFF);
  }
  return float32;
}

/**
 * Audio player queue — plays PCM chunks sequentially via AudioContext.
 * Supports barge-in (flush) to stop playback immediately.
 */
export class AudioPlayerQueue {
  private ctx: AudioContext | null = null;
  private nextStartTime = 0;
  private scheduledSources: AudioBufferSourceNode[] = [];
  private sampleRate: number;

  constructor(sampleRate = 24000) {
    this.sampleRate = sampleRate;
  }

  private ensureContext(): AudioContext {
    if (!this.ctx) {
      this.ctx = new AudioContext({ sampleRate: this.sampleRate });
    }
    return this.ctx;
  }

  /** Enqueue a PCM16 base64 chunk for playback. Returns the AudioBufferSourceNode. */
  enqueue(pcmBase64: string): AudioBufferSourceNode {
    const ctx = this.ensureContext();
    const pcmBytes = base64ToUint8(pcmBase64);
    const float32 = pcm16ToFloat32(pcmBytes);

    const buffer = ctx.createBuffer(1, float32.length, this.sampleRate);
    buffer.copyToChannel(float32, 0);

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);

    const now = ctx.currentTime;
    const startAt = Math.max(now, this.nextStartTime);
    source.start(startAt);
    this.nextStartTime = startAt + buffer.duration;

    this.scheduledSources.push(source);
    source.onended = () => {
      const idx = this.scheduledSources.indexOf(source);
      if (idx >= 0) this.scheduledSources.splice(idx, 1);
    };

    return source;
  }

  /** Flush all scheduled audio (barge-in). */
  flush(): void {
    for (const src of this.scheduledSources) {
      try { src.stop(); } catch (_) { /* already stopped */ }
    }
    this.scheduledSources = [];
    this.nextStartTime = 0;
  }

  /** True if audio is currently playing or scheduled. */
  get isPlaying(): boolean {
    return this.scheduledSources.length > 0;
  }

  /** Resume AudioContext (needed after user gesture). */
  async resume(): Promise<void> {
    const ctx = this.ensureContext();
    if (ctx.state === 'suspended') {
      await ctx.resume();
    }
  }

  dispose(): void {
    this.flush();
    if (this.ctx) {
      this.ctx.close().catch(() => {});
      this.ctx = null;
    }
  }
}
