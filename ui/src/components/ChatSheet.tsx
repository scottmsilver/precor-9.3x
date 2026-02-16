import React, { useState, useRef, useCallback, useEffect } from 'react';
import * as api from '../state/api';
import { useToast } from '../state/TreadmillContext';
import { haptic } from '../utils/haptics';
import { fmtDur } from '../utils/formatters';

interface ChatSheetProps {
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
}

export default function ChatSheet({ open, onOpen, onClose }: ChatSheetProps): React.ReactElement {
  const [chatMsg, setChatMsg] = useState('');
  const [thinking, setThinking] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordingDuration, setRecordingDuration] = useState(0);
  const [waveformBars, setWaveformBars] = useState<number[]>(new Array(32).fill(0));
  const [voiceHeardVisible, setVoiceHeardVisible] = useState(false);
  const [voiceHeardText, setVoiceHeardText] = useState('');

  const showToast = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const audioStreamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const waveformRafRef = useRef<number | null>(null);
  const recordingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const silenceStartRef = useRef(0);
  const recordingDurationRef = useRef(0);

  // Focus input when opened
  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [open]);

  const sendChat = useCallback(async () => {
    const msg = chatMsg.trim();
    if (!msg || thinking) return;
    setChatMsg('');
    setThinking(true);
    onClose();
    try {
      const res = await api.sendChat(msg);
      setThinking(false);
      showToast(res?.text || 'No response');
    } catch (_e) {
      setThinking(false);
      showToast('Error connecting to AI');
    }
    haptic(15);
  }, [chatMsg, thinking, onClose, showToast]);

  const cleanupRecording = useCallback(() => {
    if (recordingTimerRef.current) {
      clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
    if (waveformRafRef.current) {
      cancelAnimationFrame(waveformRafRef.current);
      waveformRafRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    analyserRef.current = null;
    if (audioStreamRef.current) {
      audioStreamRef.current.getTracks().forEach(t => t.stop());
      audioStreamRef.current = null;
    }
  }, []);

  const sendVoice = useCallback(async (blob: Blob, mimeType: string) => {
    setThinking(true);
    setVoiceHeardVisible(false);
    setVoiceHeardText('');
    onClose();
    try {
      const base64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve((reader.result as string).split(',')[1]);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
      const res = await api.sendVoiceChat(base64, mimeType);

      // Show transcription
      if (res?.transcription) {
        setVoiceHeardText(res.transcription);
        setVoiceHeardVisible(true);
        await new Promise(r => setTimeout(r, 1500));
        setVoiceHeardVisible(false);
      }

      setThinking(false);
      showToast(res?.text || 'No response');
    } catch (_e) {
      setThinking(false);
      setVoiceHeardVisible(false);
      showToast('Error processing voice');
    }
    haptic(15);
  }, [onClose, showToast]);

  const updateWaveform = useCallback(() => {
    if (!analyserRef.current) return;
    const data = new Uint8Array(analyserRef.current.frequencyBinCount);
    analyserRef.current.getByteFrequencyData(data);

    const bars: number[] = [];
    const binCount = Math.min(data.length, 32);
    let maxLevel = 0;
    for (let i = 0; i < 32; i++) {
      const idx = Math.floor((i / 32) * binCount);
      const level = data[idx] / 255;
      bars.push(level);
      if (level > maxLevel) maxLevel = level;
    }
    setWaveformBars(bars);

    // Silence detection
    if (recordingDurationRef.current >= 1) {
      if (maxLevel < 0.01) {
        if (!silenceStartRef.current) silenceStartRef.current = Date.now();
        else if (Date.now() - silenceStartRef.current > 2000) {
          // Auto-stop
          if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
            mediaRecorderRef.current.stop();
          }
          setRecording(false);
          haptic([25, 30, 25]);
          return;
        }
      } else {
        silenceStartRef.current = 0;
      }
    }

    waveformRafRef.current = requestAnimationFrame(updateWaveform);
  }, []);

  const startRecording = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      showToast('Mic requires HTTPS. In Chrome: chrome://flags -> "Insecure origins treated as secure"');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioStreamRef.current = stream;

      const ctx = new AudioContext();
      audioCtxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 64;
      source.connect(analyser);
      analyserRef.current = analyser;

      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/mp4')
          ? 'audio/mp4'
          : '';
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
      mediaRecorderRef.current = recorder;
      audioChunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        const actualType = recorder.mimeType || 'audio/webm';
        const blob = new Blob(audioChunksRef.current, { type: actualType });
        sendVoice(blob, actualType.split(';')[0]);
        cleanupRecording();
      };

      recorder.start();
      setRecording(true);
      setRecordingDuration(0);
      recordingDurationRef.current = 0;
      silenceStartRef.current = 0;
      setWaveformBars(new Array(32).fill(0));

      recordingTimerRef.current = setInterval(() => {
        recordingDurationRef.current += 1;
        setRecordingDuration(d => d + 1);
      }, 1000);

      updateWaveform();
      haptic(25);
    } catch (_e) {
      showToast('Microphone access denied');
    }
  }, [showToast, sendVoice, cleanupRecording, updateWaveform]);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
    setRecording(false);
    haptic([25, 30, 25]);
  }, []);

  const toggleRecording = useCallback(() => {
    if (recording) stopRecording();
    else startRecording();
  }, [recording, startRecording, stopRecording]);

  return (
    <>
      {/* Voice recording overlay */}
      {recording && (
        <div style={{
          position: 'fixed', bottom: 56, left: 16, right: 16,
          maxWidth: 480, margin: '0 auto',
          background: 'var(--elevated)',
          border: '0.5px solid var(--separator)',
          borderRadius: 'var(--r-md)', padding: '12px 16px',
          zIndex: 26,
          backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)',
          animation: 'toastSlideUp 200ms var(--ease-decel) forwards',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--red)', display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{
                width: 8, height: 8, borderRadius: '50%', background: 'var(--red)',
                animation: 'breathe 1.2s ease-in-out infinite',
              }} />
              Recording
            </div>
            <div style={{ fontSize: 13, color: 'var(--text2)', fontVariantNumeric: 'tabular-nums' }}>
              {fmtDur(recordingDuration)}
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 2, height: 32 }}>
            {waveformBars.map((level, i) => (
              <div key={i} style={{
                flex: 1, minWidth: 2, borderRadius: 1,
                background: 'var(--red)', opacity: 0.6,
                height: Math.max(2, level * 32),
                transition: 'height 80ms ease-out',
              }} />
            ))}
          </div>
        </div>
      )}

      {/* Voice transcription toast */}
      {voiceHeardVisible && (
        <div style={{
          position: 'fixed', bottom: 56, left: 16, right: 16,
          maxWidth: 480, margin: '0 auto',
          background: 'var(--elevated)',
          border: '0.5px solid var(--separator)',
          borderRadius: 'var(--r-md)', padding: '10px 14px',
          zIndex: 25,
          backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)',
          animation: 'toastSlideUp 200ms var(--ease-decel) forwards',
        }}>
          <div style={{
            fontSize: 11, color: 'var(--text3)', fontWeight: 600,
            textTransform: 'uppercase' as const, letterSpacing: '0.02em',
            marginBottom: 2,
          }}>Heard</div>
          <div style={{ fontSize: 14, color: 'var(--text)', fontStyle: 'italic', lineHeight: 1.4 }}>
            {voiceHeardText}
          </div>
        </div>
      )}

      {/* Thinking indicator */}
      {thinking && !voiceHeardVisible && (
        <div style={{
          position: 'fixed', bottom: 56, left: 16, right: 16,
          maxWidth: 480, margin: '0 auto',
          background: 'var(--elevated)',
          border: '0.5px solid var(--separator)',
          borderRadius: 'var(--r-md)', padding: '10px 14px',
          fontSize: 13, color: 'var(--text2)', lineHeight: 1.4,
          zIndex: 25,
          backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)',
        }}>
          <span style={{
            display: 'inline-block', width: 14, height: 14,
            border: '2px solid var(--text3)', borderTopColor: 'var(--purple)',
            borderRadius: '50%', animation: 'spin 0.7s linear infinite',
            marginRight: 6, verticalAlign: 'middle',
          }} />
          Thinking...
        </div>
      )}

      {/* Chat pill (always visible when sheet closed) */}
      {!open && (
        <div
          className="chat-pill"
          onClick={onOpen}
          style={{
            position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 20,
            display: 'flex', justifyContent: 'center', padding: '8px 0 12px',
            cursor: 'pointer', WebkitTapHighlightColor: 'transparent',
          }}
        >
          <div style={{
            width: 48, height: 4, borderRadius: 2,
            background: 'var(--text4)',
            animation: 'pillFloat 3s ease-in-out infinite',
          }} />
        </div>
      )}

      {/* Chat overlay */}
      {open && (
        <div
          onClick={onClose}
          style={{
            position: 'fixed', inset: 0, zIndex: 29,
            background: 'rgba(18,18,16,0.5)',
            backdropFilter: 'blur(4px)', WebkitBackdropFilter: 'blur(4px)',
          }}
        />
      )}

      {/* Chat bottom sheet */}
      <div
        className="chat-sheet"
        style={{
          position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 30,
          background: 'var(--card)',
          borderRadius: 'var(--r-xl) var(--r-xl) 0 0',
          padding: '12px 16px 16px',
          transform: open ? 'translateY(0)' : 'translateY(100%)',
          transition: 'transform 0.3s var(--ease-decel)',
        }}
      >
        <div
          onClick={onClose}
          style={{
            width: 36, height: 4, borderRadius: 2,
            background: 'var(--text4)', margin: '0 auto 12px',
            cursor: 'pointer',
          }}
        />
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            ref={inputRef}
            type="text"
            value={chatMsg}
            onChange={(e) => setChatMsg(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') sendChat(); }}
            placeholder="Ask your AI coach..."
            disabled={thinking}
            style={{
              flex: 1, height: 40, padding: '8px 16px', borderRadius: 20,
              border: '0.5px solid var(--separator)', background: 'var(--elevated)',
              color: 'var(--text)', fontFamily: 'inherit', fontSize: 15,
              outline: 'none', WebkitAppearance: 'none' as never,
              transition: 'border-color 0.2s var(--ease)',
            }}
          />
          <button
            onClick={toggleRecording}
            disabled={thinking}
            style={{
              width: 40, height: 40, borderRadius: 20, border: 'none',
              background: recording ? 'var(--red)' : 'var(--fill)',
              color: recording ? '#fff' : 'var(--text2)',
              cursor: thinking ? 'default' : 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0, WebkitTapHighlightColor: 'transparent',
              transition: 'all 200ms var(--ease)',
              opacity: thinking ? 0.3 : 1,
              animation: recording ? 'micPulse 1.2s ease-in-out infinite' : 'none',
            }}
          >
            {recording ? (
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
            ) : (
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M12 1a3 3 0 00-3 3v6a3 3 0 006 0V4a3 3 0 00-3-3z"/>
                <path d="M19 10v2a7 7 0 01-14 0v-2"/>
                <path d="M12 19v4"/>
              </svg>
            )}
          </button>
          <button
            onClick={sendChat}
            disabled={thinking || !chatMsg.trim()}
            style={{
              width: 40, height: 40, borderRadius: 20, border: 'none',
              background: 'var(--purple)', color: '#fff', fontSize: 18,
              cursor: (thinking || !chatMsg.trim()) ? 'default' : 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0, WebkitTapHighlightColor: 'transparent',
              transition: 'transform 100ms var(--ease), opacity 150ms var(--ease)',
              opacity: (thinking || !chatMsg.trim()) ? 0.3 : 1,
            }}
          >{'\u2191'}</button>
        </div>
      </div>
    </>
  );
}
