/**
 * Voice feedback overlay — shows when voice is active.
 * Minimal visual feedback at the top of screen: listening or speaking indicator.
 */
import React from 'react';
import type { VoiceState } from '../state/useVoice';

interface VoiceOverlayProps {
  voiceState: VoiceState;
}

const overlayBase: React.CSSProperties = {
  position: 'fixed',
  top: 0,
  left: 0,
  right: 0,
  zIndex: 25,
  display: 'flex',
  justifyContent: 'center',
  pointerEvents: 'none',
  transition: 'opacity 0.3s var(--ease), transform 0.3s var(--ease)',
};

const pillBase: React.CSSProperties = {
  marginTop: 'env(safe-area-inset-top, 8px)',
  padding: '6px 16px',
  borderRadius: 'var(--r-pill)',
  fontSize: 13,
  fontWeight: 500,
  fontFamily: 'inherit',
  letterSpacing: '-0.01em',
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  backdropFilter: 'blur(20px)',
  WebkitBackdropFilter: 'blur(20px)',
};

function PulseDot({ color }: { color: string }) {
  return (
    <>
      <style>{`
        @keyframes overlayPulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>
      <span style={{
        width: 8,
        height: 8,
        borderRadius: '50%',
        background: color,
        animation: 'overlayPulse 1.2s ease-in-out infinite',
        flexShrink: 0,
      }} />
    </>
  );
}

function WaveformBars() {
  return (
    <svg width="20" height="14" viewBox="0 0 20 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ color: 'var(--purple)' }}>
      <line x1="2" y1="4" x2="2" y2="10" style={{ animation: 'voiceBar 0.8s ease-in-out infinite' }} />
      <line x1="6" y1="2" x2="6" y2="12" style={{ animation: 'voiceBar 0.8s ease-in-out 0.15s infinite' }} />
      <line x1="10" y1="1" x2="10" y2="13" style={{ animation: 'voiceBar 0.8s ease-in-out 0.3s infinite' }} />
      <line x1="14" y1="2" x2="14" y2="12" style={{ animation: 'voiceBar 0.8s ease-in-out 0.45s infinite' }} />
      <line x1="18" y1="4" x2="18" y2="10" style={{ animation: 'voiceBar 0.8s ease-in-out 0.6s infinite' }} />
    </svg>
  );
}

export default function VoiceOverlay({ voiceState }: VoiceOverlayProps) {
  const active = voiceState === 'listening' || voiceState === 'speaking';

  if (!active) return null;

  const isListening = voiceState === 'listening';

  return (
    <div style={{
      ...overlayBase,
      opacity: active ? 1 : 0,
      transform: active ? 'translateY(0)' : 'translateY(-20px)',
    }}>
      <div style={{
        ...pillBase,
        background: isListening
          ? 'rgba(196,92,82,0.15)'
          : 'rgba(139,127,160,0.15)',
        color: isListening ? 'var(--red)' : 'var(--purple)',
      }}>
        {isListening ? <PulseDot color="var(--red)" /> : <WaveformBars />}
        <span>{isListening ? 'Listening...' : 'Speaking...'}</span>
      </div>
    </div>
  );
}
