import React from 'react';
import { useTreadmillState, useTreadmillActions } from '../state/TreadmillContext';
import { haptic } from '../utils/haptics';

export default function StopButton() {
  const { status, program } = useTreadmillState();
  const actions = useTreadmillActions();

  const visible = status.emulate && (status.emuSpeed > 0 || program.running);

  if (!visible) return null;

  return (
    <div className="stop-area" style={{
      position: 'sticky', bottom: 36, zIndex: 10,
      padding: '4px 16px', flexShrink: 0,
      background: 'linear-gradient(transparent, var(--bg) 40%)',
    }}>
      <button
        onClick={() => { actions.emergencyStop(); haptic([50, 30, 50]); }}
        style={{
          width: '100%', height: 56, borderRadius: 14,
          border: 'none', background: 'var(--red)', color: '#fff',
          fontSize: 17, fontWeight: 600, fontFamily: 'inherit',
          letterSpacing: '-0.005em',
          cursor: 'pointer', WebkitTapHighlightColor: 'transparent',
        }}
      >
        Stop
      </button>
    </div>
  );
}
