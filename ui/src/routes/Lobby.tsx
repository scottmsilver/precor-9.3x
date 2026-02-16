import type React from 'react';
import { useLocation } from 'wouter';
import { useTreadmillState, useTreadmillActions } from '../state/TreadmillContext';
import { useProgram } from '../state/useProgram';
import * as api from '../state/api';
import { haptic } from '../utils/haptics';
import MiniStatusCard from '../components/MiniStatusCard';
import HistoryList from '../components/HistoryList';

export default function Lobby(): React.ReactElement {
  const { session, program } = useTreadmillState();
  const actions = useTreadmillActions();
  const pgm = useProgram();
  const [, setLocation] = useLocation();

  const workoutActive = session.active || program.running;

  return (
    <div className="lobby-content" style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Lobby prompt */}
      <div style={{ textAlign: 'center', padding: '24px 16px 12px' }}>
        <div style={{ marginBottom: 16, fontSize: 15, color: 'var(--text3)', fontWeight: 500 }}>
          Choose a program or speak to your coach
        </div>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'center', alignItems: 'center' }}>
          {workoutActive && (
            <button
              onClick={() => { setLocation('/run'); haptic(25); }}
              style={{
                height: 48, padding: '0 28px', borderRadius: 'var(--r-pill)',
                border: 'none', background: 'var(--green)', color: '#000',
                fontSize: 15, fontWeight: 700, fontFamily: 'inherit',
                cursor: 'pointer', WebkitTapHighlightColor: 'transparent',
              }}
            >
              Return to Workout
            </button>
          )}
          {!workoutActive && (
            <button
              onClick={() => {
                api.quickStart(3.0, 0, 60);
                haptic([25, 30, 25]);
                setLocation('/run');
              }}
              style={{
                height: 48, padding: '0 28px', borderRadius: 'var(--r-pill)',
                border: 'none', background: 'var(--fill)', color: 'var(--text)',
                fontSize: 15, fontWeight: 600, fontFamily: 'inherit',
                cursor: 'pointer', WebkitTapHighlightColor: 'transparent',
              }}
            >
              Just Start
            </button>
          )}
          {pgm.program && !pgm.running && (
            <button
              onClick={() => {
                actions.startProgram();
                haptic([25, 30, 25]);
                setLocation('/run');
              }}
              style={{
                height: 48, padding: '0 28px', borderRadius: 'var(--r-pill)',
                border: 'none', background: 'var(--green)', color: '#000',
                fontSize: 15, fontWeight: 700, fontFamily: 'inherit',
                cursor: 'pointer', WebkitTapHighlightColor: 'transparent',
              }}
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
