import React, { useRef } from 'react';
import { useLocation } from 'wouter';
import { useTreadmillState, useTreadmillActions } from '../state/TreadmillContext';
import { useProgram } from '../state/useProgram';
import * as api from '../state/api';
import { haptic } from '../utils/haptics';
import MiniStatusCard from '../components/MiniStatusCard';
import HistoryList from '../components/HistoryList';

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  return 'Good evening';
}

const lobbyBtn: React.CSSProperties = {
  height: 48, padding: '0 24px', borderRadius: 'var(--r-pill)',
  border: 'none', fontSize: 15, fontWeight: 600,
  fontFamily: 'inherit', cursor: 'pointer',
  WebkitTapHighlightColor: 'transparent',
  display: 'flex', alignItems: 'center', gap: 8,
};

export default function Lobby(): React.ReactElement {
  const { session, program } = useTreadmillState();
  const actions = useTreadmillActions();
  const pgm = useProgram();
  const [, setLocation] = useLocation();

  const workoutActive = session.active || program.running;
  const quickStartGuard = useRef(false);

  return (
    <div className="lobby-content" style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Greeting + action buttons */}
      <div style={{ textAlign: 'center', padding: '24px 16px 12px' }}>
        <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>
          {greeting()}
        </div>
        <div style={{ fontSize: 14, color: 'var(--text3)', marginBottom: 16 }}>
          Ready for a run?
        </div>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'center', alignItems: 'center' }}>
          {workoutActive && (
            <button
              onClick={() => { setLocation('/run'); haptic(25); }}
              style={{ ...lobbyBtn, background: 'var(--green)', color: '#000', fontWeight: 700 }}
            >
              Return to Workout
            </button>
          )}
          {!workoutActive && (
            <>
              <button
                onClick={() => { setLocation('/run'); haptic(25); }}
                style={{ ...lobbyBtn, background: 'var(--fill)', color: 'var(--text)' }}
              >
                Quick
              </button>
              <button
                onClick={() => {
                  if (quickStartGuard.current) return;
                  quickStartGuard.current = true;
                  setTimeout(() => { quickStartGuard.current = false; }, 1000);
                  api.quickStart(3.0, 0, 60);
                  haptic([25, 30, 25]);
                  setLocation('/run');
                }}
                style={{ ...lobbyBtn, background: 'var(--green)', color: '#000', fontWeight: 700 }}
              >
                Manual
              </button>
            </>
          )}
          {pgm.program && !pgm.running && (
            <button
              onClick={() => {
                actions.startProgram();
                haptic([25, 30, 25]);
                setLocation('/run');
              }}
              style={{ ...lobbyBtn, background: 'var(--green)', color: '#000', fontWeight: 700 }}
            >
              Start {pgm.program.name || 'Program'}
            </button>
          )}
        </div>
      </div>

      {/* Mini status when workout is active */}
      <MiniStatusCard />

      {/* History */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '0' }}>
        <div style={{
          fontSize: 13, fontWeight: 600, color: 'var(--text3)',
          textTransform: 'uppercase' as const, letterSpacing: '0.02em',
          padding: '12px 16px 8px',
        }}>
          Your Programs
        </div>
        <HistoryList variant="lobby" onAfterLoad={() => {
          actions.startProgram();
          haptic([25, 30, 25]);
          setLocation('/run');
        }} />
      </div>

    </div>
  );
}
