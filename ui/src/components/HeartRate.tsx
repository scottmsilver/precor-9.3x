import React, { useEffect } from 'react';
import { useTreadmillState } from '../state/TreadmillContext';
import { hrColor } from '../utils/hrColor';

// Inject pulse keyframes once
let styleInjected = false;
function injectPulseStyle() {
  if (styleInjected) return;
  styleInjected = true;
  const style = document.createElement('style');
  style.textContent = `
    @keyframes hr-pulse {
      0%, 100% { transform: scale(1); }
      15% { transform: scale(1.18); }
      30% { transform: scale(1); }
      45% { transform: scale(1.12); }
      60% { transform: scale(1); }
    }
  `;
  document.head.appendChild(style);
}

export default function HeartRate(): React.ReactElement | null {
  const { status } = useTreadmillState();

  useEffect(() => {
    injectPulseStyle();
  }, []);

  if (!status.hrmConnected) return null;

  const bpm = status.heartRate;
  const color = hrColor(bpm);
  // Scale animation duration inversely with BPM for a realistic pulse
  const pulseDuration = bpm > 0 ? Math.max(0.4, 60 / bpm) : 1;

  return (
    <div
      role="status"
      aria-label={`Heart rate: ${bpm} beats per minute`}
      style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}
    >
      <span
        style={{
          display: 'inline-block',
          color,
          fontSize: 14,
          lineHeight: 1,
          animation: `hr-pulse ${pulseDuration}s ease-in-out infinite`,
          willChange: 'transform',
        }}
        aria-hidden="true"
      >
        {'\u2665'}
      </span>
      <span
        className="metric-value"
        style={{
          fontSize: 15,
          fontWeight: 600,
          fontVariantNumeric: 'tabular-nums',
          color,
        }}
      >
        {bpm > 0 ? bpm : '---'}
      </span>
      <span className="metric-label" style={{ fontSize: 10, color: 'var(--text3)' }}>bpm</span>
    </div>
  );
}
