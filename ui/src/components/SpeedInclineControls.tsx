import React, { useRef, useCallback } from 'react';
import { useTreadmillState, useTreadmillActions } from '../state/TreadmillContext';
import { haptic } from '../utils/haptics';

// Chevron SVGs
function ChevronUp({ sw = 2 }: { sw?: number }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <polyline points="3,10 8,5 13,10" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function ChevronDown({ sw = 2 }: { sw?: number }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <polyline points="3,6 8,11 13,6" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function DoubleChevronUp({ sw = 2.5 }: { sw?: number }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <polyline points="3,9 8,4 13,9" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" />
      <polyline points="3,14 8,9 13,14" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function DoubleChevronDown({ sw = 2.5 }: { sw?: number }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <polyline points="3,2 8,7 13,2" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" />
      <polyline points="3,7 8,12 13,7" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// Clean pill button — matches Home/Voice style
const btn: React.CSSProperties = {
  width: 38, height: 40, borderRadius: 10,
  border: 'none', background: 'var(--fill)',
  cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
  WebkitTapHighlightColor: 'transparent',
  fontFamily: 'inherit',
};

export default function SpeedInclineControls(): React.ReactElement {
  const { status } = useTreadmillState();
  const actions = useTreadmillActions();
  const repeatTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const repeatCount = useRef(0);

  const startRepeat = useCallback((type: 'speed' | 'incline', delta: number) => {
    repeatCount.current = 0;
    const action = type === 'speed'
      ? () => { actions.adjustSpeed(delta); haptic(15); }
      : () => { actions.adjustIncline(delta); haptic(15); };
    action();
    repeatTimer.current = setTimeout(() => {
      repeatTimer.current = setInterval(() => {
        repeatCount.current++;
        action();
      }, repeatCount.current > 5 ? 75 : 150) as unknown as ReturnType<typeof setTimeout>;
    }, 400);
  }, [actions]);

  const stopRepeat = useCallback(() => {
    if (repeatTimer.current != null) {
      clearTimeout(repeatTimer.current);
      clearInterval(repeatTimer.current as unknown as ReturnType<typeof setInterval>);
      repeatTimer.current = null;
    }
    repeatCount.current = 0;
  }, []);

  const ph = (type: 'speed' | 'incline', delta: number) => ({
    onPointerDown: () => startRepeat(type, delta),
    onPointerUp: stopRepeat,
    onPointerLeave: stopRepeat,
  });

  return (
    <div className="controls" style={{
      display: 'flex', gap: 10, padding: '0 12px', flexShrink: 0,
      opacity: !status.treadmillConnected ? 0.3 : 1,
      pointerEvents: !status.treadmillConnected ? 'none' : 'auto',
    }}>
      {/* Speed panel */}
      <div style={{
        flex: 1, display: 'flex', alignItems: 'center', gap: 3,
        background: 'var(--card)', borderRadius: 'var(--r-lg)', padding: '6px 5px',
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <button style={{ ...btn, color: 'var(--text3)' }} {...ph('speed', 1)}>
            <ChevronUp />
          </button>
          <button style={{ ...btn, color: 'var(--green)' }} {...ph('speed', 10)}>
            <DoubleChevronUp />
          </button>
        </div>
        <div style={{ flex: 1, textAlign: 'center', minWidth: 0 }}>
          <div style={{
            fontSize: 26, fontWeight: 600, fontVariantNumeric: 'tabular-nums',
            lineHeight: 1.1, color: 'var(--green)',
          }}>
            {(status.emuSpeed / 10).toFixed(1)}
          </div>
          <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 1 }}>mph</div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <button style={{ ...btn, color: 'var(--text3)' }} {...ph('speed', -1)}>
            <ChevronDown />
          </button>
          <button style={{ ...btn, color: 'var(--green)' }} {...ph('speed', -10)}>
            <DoubleChevronDown />
          </button>
        </div>
      </div>

      {/* Incline panel */}
      <div style={{
        flex: 1, display: 'flex', alignItems: 'center', gap: 3,
        background: 'var(--card)', borderRadius: 'var(--r-lg)', padding: '6px 5px',
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <button style={{ ...btn, color: 'var(--text3)' }} {...ph('incline', 1)}>
            <ChevronUp />
          </button>
          <button style={{ ...btn, color: 'var(--orange)' }} {...ph('incline', 5)}>
            <DoubleChevronUp />
          </button>
        </div>
        <div style={{ flex: 1, textAlign: 'center', minWidth: 0 }}>
          <div style={{
            fontSize: 26, fontWeight: 600, fontVariantNumeric: 'tabular-nums',
            lineHeight: 1.1, color: 'var(--orange)',
          }}>
            {status.emuIncline}%
          </div>
          <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 1 }}>incline</div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <button style={{ ...btn, color: 'var(--text3)' }} {...ph('incline', -1)}>
            <ChevronDown />
          </button>
          <button style={{ ...btn, color: 'var(--orange)' }} {...ph('incline', -5)}>
            <DoubleChevronDown />
          </button>
        </div>
      </div>
    </div>
  );
}
