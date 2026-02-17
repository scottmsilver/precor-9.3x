import React from 'react';
import { useTreadmillState, useTreadmillActions } from '../state/TreadmillContext';
import { useProgram } from '../state/useProgram';
import { haptic } from '../utils/haptics';
import SpeedInclineControls from './SpeedInclineControls';

const actionBtn: React.CSSProperties = {
  height: 50, borderRadius: 14, border: 'none',
  fontWeight: 600, fontFamily: 'inherit',
  cursor: 'pointer', WebkitTapHighlightColor: 'transparent',
};

export default function BottomBar(): React.ReactElement {
  const { status, program } = useTreadmillState();
  const actions = useTreadmillActions();
  const pgm = useProgram();

  const isRunning = status.emulate && (status.emuSpeed > 0 || (program.running && !pgm.paused));

  return (
    <div style={{ flexShrink: 0, paddingTop: 6, paddingBottom: 12 }}>
      <div style={{ marginBottom: 12 }}>
        <SpeedInclineControls />
      </div>

      <div style={{
        display: 'flex', gap: 8, padding: '0 12px',
        alignItems: 'stretch',
      }}>
        {pgm.paused ? (
          <>
            <button
              onClick={() => { actions.pauseProgram(); haptic(25); }}
              style={{ ...actionBtn, flex: 2, background: 'var(--green)', color: '#fff', fontSize: 17 }}
            >
              Resume
            </button>
            <button
              onClick={() => { actions.resetAll(); haptic([50, 30, 50]); }}
              style={{ ...actionBtn, flex: 1, background: 'rgba(196,92,82,0.15)', color: 'var(--red)', fontSize: 15 }}
            >
              Reset
            </button>
          </>
        ) : (
          <button
            onClick={isRunning ? () => { actions.pauseProgram(); haptic([50, 30, 50]); } : undefined}
            disabled={!isRunning}
            style={{
              ...actionBtn, flex: 1, fontSize: 17,
              background: isRunning ? 'var(--red)' : 'var(--fill)',
              color: isRunning ? '#fff' : 'var(--text3)',
              cursor: isRunning ? 'pointer' : 'default',
              opacity: isRunning ? 1 : 0.4,
            }}
          >
            Stop
          </button>
        )}
      </div>
    </div>
  );
}
