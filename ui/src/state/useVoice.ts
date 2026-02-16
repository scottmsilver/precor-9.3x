/**
 * React hook managing voice lifecycle for Gemini Live API.
 *
 * States: idle -> connecting -> listening -> speaking
 * Lazy-connects on first mic tap. Persists across route changes.
 */
import { useState, useRef, useCallback, useEffect } from 'react';
import { GeminiLiveClient } from '../voice/GeminiLiveClient';
import type { ClientState, GeminiLiveCallbacks } from '../voice/GeminiLiveClient';
import { AudioPlayerQueue } from '../voice/audioUtils';
import { float32ToPcm16, uint8ToBase64 } from '../voice/audioUtils';
import { getConfig } from './api';
import type { AppConfig } from './types';
import { useTreadmillState, useToast } from './TreadmillContext';

export type VoiceState = 'idle' | 'connecting' | 'listening' | 'speaking';

export interface UseVoiceReturn {
  voiceState: VoiceState;
  toggle: () => void;
  interrupt: () => void;
}

// Mic capture config
const MIC_SAMPLE_RATE = 16000;
const MIC_BUFFER_SIZE = 4096;

export function useVoice(): UseVoiceReturn {
  const [voiceState, setVoiceState] = useState<VoiceState>('idle');
  const showToast = useToast();
  const clientRef = useRef<GeminiLiveClient | null>(null);
  const playerRef = useRef<AudioPlayerQueue | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const micProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const micContextRef = useRef<AudioContext | null>(null);
  const configRef = useRef<AppConfig | null>(null);
  const treadmillState = useTreadmillState();

  // Build state context string for system prompt
  const stateContextRef = useRef('');
  useEffect(() => {
    const s = treadmillState.status;
    const p = treadmillState.program;
    const parts: string[] = [];
    if (s.emulate) {
      parts.push(`Speed: ${(s.emuSpeed / 10).toFixed(1)} mph`);
      parts.push(`Incline: ${s.emuIncline}%`);
    } else if (s.speed != null) {
      parts.push(`Speed: ${s.speed.toFixed(1)} mph`);
      if (s.incline != null) parts.push(`Incline: ${s.incline}%`);
    }
    parts.push(`Mode: ${s.emulate ? 'emulate' : 'proxy'}`);
    if (p.running) {
      parts.push(`Program: "${p.program?.name ?? 'unnamed'}" running`);
      if (p.program) {
        const iv = p.program.intervals[p.currentInterval];
        if (iv) {
          parts.push(`Current interval: "${iv.name}" (${iv.speed} mph, ${iv.incline}%)`);
          parts.push(`Interval time: ${p.intervalElapsed}/${iv.duration}s`);
        }
      }
      parts.push(`Total: ${p.totalElapsed}/${p.totalDuration}s`);
      if (p.paused) parts.push('PAUSED');
    }
    stateContextRef.current = parts.join('\n');

    // Update live client if connected
    if (clientRef.current) {
      clientRef.current.updateStateContext(stateContextRef.current);
    }
  }, [treadmillState]);

  const ensurePlayer = useCallback((): AudioPlayerQueue => {
    if (!playerRef.current) {
      playerRef.current = new AudioPlayerQueue(24000);
    }
    return playerRef.current;
  }, []);

  const stopMic = useCallback(() => {
    if (micProcessorRef.current) {
      micProcessorRef.current.disconnect();
      micProcessorRef.current = null;
    }
    if (micContextRef.current) {
      micContextRef.current.close().catch(() => {});
      micContextRef.current = null;
    }
    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach(t => t.stop());
      micStreamRef.current = null;
    }
  }, []);

  const startMic = useCallback(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: MIC_SAMPLE_RATE,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
      },
    });
    micStreamRef.current = stream;

    const ctx = new AudioContext({ sampleRate: MIC_SAMPLE_RATE });
    micContextRef.current = ctx;
    const source = ctx.createMediaStreamSource(stream);

    // ScriptProcessorNode for PCM capture (createScriptProcessor is deprecated
    // but AudioWorklet requires a separate file; this works fine for our use case)
    const processor = ctx.createScriptProcessor(MIC_BUFFER_SIZE, 1, 1);
    micProcessorRef.current = processor;

    let audioChunkCount = 0;
    processor.onaudioprocess = (e) => {
      const client = clientRef.current;
      if (!client?.isConnected) return;
      const inputData = e.inputBuffer.getChannelData(0);
      const pcm = float32ToPcm16(inputData);
      const b64 = uint8ToBase64(pcm);
      client.sendAudio(b64);
      audioChunkCount++;
      if (audioChunkCount === 1 || audioChunkCount % 50 === 0) {
        console.log(`[Voice] Sent ${audioChunkCount} audio chunks`);
      }
    };

    source.connect(processor);
    processor.connect(ctx.destination); // required for processing to work
  }, []);

  const connect = useCallback(async () => {
    // Fetch config if not cached
    if (!configRef.current) {
      try {
        configRef.current = await getConfig();
      } catch {
        setVoiceState('idle');
        return;
      }
    }
    const config = configRef.current;
    if (!config.gemini_api_key) {
      showToast('No Gemini API key configured');
      setVoiceState('idle');
      return;
    }

    setVoiceState('connecting');

    const player = ensurePlayer();

    const callbacks: GeminiLiveCallbacks = {
      onStateChange: (s: ClientState) => {
        if (s === 'connected') {
          // Start mic capture once connected
          startMic().then(() => {
            console.log('[Voice] Mic started, listening');
            setVoiceState('listening');
          }).catch((err) => {
            console.error('[Voice] Mic failed:', err);
            const isInsecure = window.location.protocol === 'http:';
            if (isInsecure) {
              showToast('Mic requires HTTPS. In Chrome: chrome://flags → "Insecure origins treated as secure" → add this URL');
            } else {
              showToast('Microphone access denied');
            }
            setVoiceState('idle');
            clientRef.current?.disconnect();
          });
        } else if (s === 'disconnected' || s === 'error') {
          stopMic();
          player.flush();
          setVoiceState('idle');
        }
      },
      onAudioChunk: (pcmBase64: string) => {
        player.resume().then(() => {
          player.enqueue(pcmBase64);
        });
      },
      onSpeakingStart: () => {
        setVoiceState('speaking');
      },
      onSpeakingEnd: () => {
        // Back to listening after Gemini finishes speaking
        setVoiceState('listening');
      },
      onInterrupted: () => {
        player.flush();
        setVoiceState('listening');
      },
      onError: (_msg: string) => {
        // Error handling — state change will clean up
      },
    };

    const client = new GeminiLiveClient(
      config.gemini_api_key,
      config.gemini_live_model || 'gemini-2.5-flash-native-audio-preview-12-2025',
      config.gemini_voice || 'Kore',
      callbacks,
      stateContextRef.current,
    );
    clientRef.current = client;
    client.connect();
  }, [ensurePlayer, startMic, stopMic]);

  const disconnectAll = useCallback(() => {
    stopMic();
    clientRef.current?.disconnect();
    clientRef.current = null;
    playerRef.current?.flush();
    setVoiceState('idle');
  }, [stopMic]);

  const interrupt = useCallback(() => {
    playerRef.current?.flush();
    setVoiceState('listening');
  }, []);

  const toggle = useCallback(() => {
    switch (voiceState) {
      case 'idle':
        connect();
        break;
      case 'connecting':
        disconnectAll();
        break;
      case 'listening':
        disconnectAll();
        break;
      case 'speaking':
        // Barge-in: stop playback, stay listening
        interrupt();
        break;
    }
  }, [voiceState, connect, disconnectAll, interrupt]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopMic();
      clientRef.current?.disconnect();
      playerRef.current?.dispose();
    };
  }, [stopMic]);

  return { voiceState, toggle, interrupt };
}
