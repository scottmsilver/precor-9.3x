import React, { useState } from 'react';
import { useLocation } from 'wouter';
import { useSession } from '../state/useSession';
import { useProgram } from '../state/useProgram';
import { useVoice } from '../state/useVoice';
import * as api from '../state/api';
import { fmtDur } from '../utils/formatters';
import { haptic } from '../utils/haptics';
import MetricsRow from '../components/MetricsRow';
import ProgramHUD from '../components/ProgramHUD';
import ProgramComplete from '../components/ProgramComplete';
import HistoryList from '../components/HistoryList';
import BottomBar from '../components/BottomBar';
import { pillBtn, HomeIcon, MicIcon } from '../components/shared';

function PathIcon() {
  return (
    <svg width="48" height="32" viewBox="0 0 48 32" fill="none" style={{ opacity: 0.15 }}>
      <path d="M2,28 C10,28 12,8 20,8 C28,8 26,20 34,20 C40,20 42,12 46,4"
        stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function EmptyRunCard({ onVoice }: { onVoice: () => void }) {
  const [, navigate] = useLocation();
  return (
    <div style={{
      margin: '0 16px 8px', flex: 1,
      borderRadius: 'var(--r-lg)', background: 'var(--card)',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      gap: 16, minHeight: 0,
    }}>
      <PathIcon />
      <div style={{ textAlign: 'center' }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--text2)' }}>
          Ready when you are
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 4 }}>
          Set your speed below to start
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button style={pillBtn} onClick={() => navigate('/')}>
          <HomeIcon /> Home
        </button>
        <button style={pillBtn} onClick={onVoice}>
          <MicIcon /> Voice
        </button>
      </div>
    </div>
  );
}

export default function Running(): React.ReactElement {
  const sess = useSession();
  const pgm = useProgram();
  const { toggle: toggleVoice } = useVoice();
  const [durationEditOpen, setDurationEditOpen] = useState(false);

  const isActive = sess.active || pgm.running;
  const isManual = pgm.program?.manual === true;

  const handleTimeTap = () => {
    if (isManual && pgm.running) {
      setDurationEditOpen(v => !v);
      haptic(10);
    }
  };

  const adjustDuration = (deltaMins: number) => {
    api.adjustDuration(deltaMins * 60);
    haptic(25);
  };

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Hero time with ambient glow */}
      <div style={{
        textAlign: 'center', padding: '4px 16px', flexShrink: 0,
        position: 'relative',
      }}>
        <div style={{
          position: 'absolute', top: '50%', left: '50%',
          width: 200, height: 140,
          transform: 'translate(-50%, -55%)',
          background: 'radial-gradient(ellipse, var(--teal) 0%, transparent 70%)',
          opacity: isActive ? 0.25 : 0,
          filter: 'blur(50px)',
          pointerEvents: 'none', zIndex: 0,
          transition: 'opacity 0.6s var(--ease)',
          willChange: 'opacity',
        }} />
        <div
          className="hero-time"
          onClick={handleTimeTap}
          style={{
            position: 'relative', zIndex: 1,
            fontSize: 96, fontWeight: 700, lineHeight: 1,
            fontVariantNumeric: 'tabular-nums', letterSpacing: '-0.02em',
            color: isActive ? 'var(--text)' : 'var(--text2)',
            transition: 'color 0.35s, font-size 0.3s var(--ease)',
            cursor: isManual && pgm.running ? 'pointer' : 'default',
            WebkitTapHighlightColor: 'transparent',
          }}
        >
          {sess.elapsedDisplay}
        </div>

        {isManual && pgm.running && (
          <div style={{
            fontSize: 12, color: 'var(--text3)', marginTop: 2,
            fontVariantNumeric: 'tabular-nums',
          }}>
            {fmtDur(pgm.totalRemaining)} remaining of {fmtDur(pgm.totalDuration)}
          </div>
        )}

        {durationEditOpen && isManual && pgm.running && (
          <div style={{
            display: 'flex', gap: 8, justifyContent: 'center', marginTop: 8,
            animation: 'toastSlideUp 150ms var(--ease-decel) forwards',
          }}>
            {[-10, -5, 5, 10].map(d => (
              <button
                key={d}
                onClick={(e) => { e.stopPropagation(); adjustDuration(d); }}
                style={{
                  height: 36, padding: '0 14px', borderRadius: 'var(--r-pill)',
                  border: '0.5px solid var(--separator)',
                  background: 'var(--card)', color: d > 0 ? 'var(--green)' : 'var(--text3)',
                  fontSize: 13, fontWeight: 600, fontFamily: 'inherit',
                  cursor: 'pointer', WebkitTapHighlightColor: 'transparent',
                }}
              >
                {d > 0 ? '+' : ''}{d}m
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Metrics row */}
      <MetricsRow />

      {/* Elevation profile or empty state — fills available vertical space */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', marginTop: 6 }}>
        {pgm.program && pgm.running ? (
          <ProgramHUD />
        ) : pgm.completed ? (
          <ProgramComplete onVoice={() => { haptic(20); toggleVoice(); }} />
        ) : (
          <EmptyRunCard onVoice={() => { haptic(20); toggleVoice(); }} />
        )}
      </div>

      {pgm.completed && !pgm.running && (
        <HistoryList variant="compact" />
      )}

      <BottomBar />
    </div>
  );
}
