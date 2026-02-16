/**
 * Floating action button for voice control.
 * 3 visual states: idle (mic), listening (red pulse), speaking (waveform).
 * Positioned bottom-right, above the stop button area.
 */
import React from 'react';
import type { VoiceState } from '../state/useVoice';
import { haptic } from '../utils/haptics';
import { MicIcon } from './shared';

interface VoiceFABProps {
  voiceState: VoiceState;
  onTap: () => void;
}

// Waveform bars animation for speaking state
function WaveformIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
      <line x1="4" y1="8" x2="4" y2="16" style={{ animation: 'voiceBar 0.8s ease-in-out infinite' }} />
      <line x1="8" y1="5" x2="8" y2="19" style={{ animation: 'voiceBar 0.8s ease-in-out 0.1s infinite' }} />
      <line x1="12" y1="3" x2="12" y2="21" style={{ animation: 'voiceBar 0.8s ease-in-out 0.2s infinite' }} />
      <line x1="16" y1="5" x2="16" y2="19" style={{ animation: 'voiceBar 0.8s ease-in-out 0.3s infinite' }} />
      <line x1="20" y1="8" x2="20" y2="16" style={{ animation: 'voiceBar 0.8s ease-in-out 0.4s infinite' }} />
    </svg>
  );
}

const fabBase: React.CSSProperties = {
  position: 'fixed',
  bottom: 170,
  right: 20,
  zIndex: 20,
  width: 56,
  height: 56,
  borderRadius: '50%',
  border: 'none',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  cursor: 'pointer',
  WebkitTapHighlightColor: 'transparent',
  transition: 'background 0.2s var(--ease), box-shadow 0.2s var(--ease), transform 0.15s var(--ease-spring)',
  fontFamily: 'inherit',
};

const stateStyles: Record<VoiceState, React.CSSProperties> = {
  idle: {
    background: 'var(--elevated)',
    color: 'var(--text2)',
    boxShadow: '0 2px 12px rgba(0,0,0,0.3)',
  },
  connecting: {
    background: 'var(--elevated)',
    color: 'var(--text3)',
    boxShadow: '0 2px 12px rgba(0,0,0,0.3)',
    opacity: 0.7,
  },
  listening: {
    background: 'var(--red)',
    color: '#fff',
    boxShadow: '0 2px 20px rgba(196,92,82,0.5)',
  },
  speaking: {
    background: 'var(--purple)',
    color: '#fff',
    boxShadow: '0 2px 20px rgba(139,127,160,0.5)',
  },
};

export default function VoiceFAB({ voiceState, onTap }: VoiceFABProps) {
  const handleClick = () => {
    haptic(voiceState === 'idle' ? 20 : 10);
    onTap();
  };

  const style: React.CSSProperties = {
    ...fabBase,
    ...stateStyles[voiceState],
  };

  return (
    <>
      <style>{`
        @keyframes voiceBar {
          0%, 100% { transform: scaleY(0.4); }
          50% { transform: scaleY(1); }
        }
        @keyframes voicePulse {
          0% { transform: scale(1); opacity: 0.5; }
          100% { transform: scale(1.8); opacity: 0; }
        }
      `}</style>
      <button
        onClick={handleClick}
        style={style}
        aria-label={
          voiceState === 'idle' ? 'Start voice' :
          voiceState === 'listening' ? 'Stop voice' :
          voiceState === 'speaking' ? 'Interrupt' :
          'Connecting...'
        }
      >
        {/* Pulse ring for listening state */}
        {voiceState === 'listening' && (
          <span style={{
            position: 'absolute',
            inset: 0,
            borderRadius: '50%',
            border: '2px solid var(--red)',
            animation: 'voicePulse 1.5s ease-out infinite',
            pointerEvents: 'none',
          }} />
        )}
        {voiceState === 'speaking' ? <WaveformIcon /> : <MicIcon size={24} />}
        {voiceState === 'connecting' && (
          <span style={{
            position: 'absolute',
            inset: 0,
            borderRadius: '50%',
            border: '2px solid var(--text3)',
            borderTopColor: 'transparent',
            animation: 'spin 0.8s linear infinite',
            pointerEvents: 'none',
          }} />
        )}
      </button>
    </>
  );
}
