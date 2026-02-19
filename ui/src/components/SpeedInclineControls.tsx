import React, { useRef, useCallback, useState, useEffect } from 'react';
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
  width: 38, height: 54, borderRadius: 10, boxSizing: 'border-box' as const,
  border: 'none', background: 'var(--fill)',
  cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
  WebkitTapHighlightColor: 'transparent',
  fontFamily: 'inherit',
};

type PulseDir = 'up' | 'down' | null;

/** Track a value and return which direction it pulsed, auto-clearing after 500ms. */
function usePulse(value: number): PulseDir {
  const prev = useRef(value);
  const [dir, setDir] = useState<PulseDir>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    if (value !== prev.current) {
      setDir(value > prev.current ? 'up' : 'down');
      prev.current = value;
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setDir(null), 500);
    }
    return () => clearTimeout(timer.current);
  }, [value]);

  return dir;
}

export default function SpeedInclineControls(): React.ReactElement {
  const { status } = useTreadmillState();
  const actions = useTreadmillActions();
  const repeatTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const repeatCount = useRef(0);
  const activeBtn = useRef<string | null>(null);

  const speedPulse = usePulse(status.emuSpeed);
  const inclinePulse = usePulse(status.emuIncline);

  const startRepeat = useCallback((type: 'speed' | 'incline', delta: number, btnId: string) => {
    repeatCount.current = 0;
    activeBtn.current = btnId;
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
    activeBtn.current = null;
  }, []);

  const ph = (type: 'speed' | 'incline', delta: number, btnId: string) => ({
    onPointerDown: () => startRepeat(type, delta, btnId),
    onPointerUp: stopRepeat,
    onPointerLeave: stopRepeat,
  });

  // Pulse class helper — only pulses the specific button that was tapped
  const pulseBtn = (pulse: PulseDir, dir: 'up' | 'down', btnId: string) =>
    pulse === dir && activeBtn.current === btnId ? 'pulse-btn' : '';
  const pulseVal = (pulse: PulseDir) =>
    pulse ? 'pulse-value' : '';

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
        border: '1px solid rgba(255,255,255,0.25)',
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <button key={`su1-${status.emuSpeed}`} className={`ctrl-btn ${pulseBtn(speedPulse, 'up', 'su1')}`} style={{ ...btn, color: 'var(--text3)' }} {...ph('speed', 1, 'su1')}>
            <ChevronUp />
          </button>
          <button key={`sd1-${status.emuSpeed}`} className={`ctrl-btn ${pulseBtn(speedPulse, 'down', 'sd1')}`} style={{ ...btn, color: 'var(--text3)' }} {...ph('speed', -1, 'sd1')}>
            <ChevronDown />
          </button>
        </div>
        <div style={{ flex: 1, textAlign: 'center', minWidth: 0, padding: '10px 0' }}>
          <div key={`sv-${status.emuSpeed}`} className={`ctrl-value ${pulseVal(speedPulse)}`} style={{
            fontSize: 26, fontWeight: 600, fontVariantNumeric: 'tabular-nums',
            lineHeight: 1.1, color: 'var(--green)',
          }}>
            {(status.emuSpeed / 10).toFixed(1)}
          </div>
          <div className="ctrl-label" style={{ fontSize: 10, color: 'var(--text3)', marginTop: 1 }}>mph</div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <button key={`su10-${status.emuSpeed}`} className={`ctrl-btn ${pulseBtn(speedPulse, 'up', 'su10')}`} style={{ ...btn, color: 'var(--green)' }} {...ph('speed', 10, 'su10')}>
            <DoubleChevronUp />
          </button>
          <button key={`sd10-${status.emuSpeed}`} className={`ctrl-btn ${pulseBtn(speedPulse, 'down', 'sd10')}`} style={{ ...btn, color: 'var(--green)' }} {...ph('speed', -10, 'sd10')}>
            <DoubleChevronDown />
          </button>
        </div>
      </div>

      {/* Incline panel */}
      <div style={{
        flex: 1, display: 'flex', alignItems: 'center', gap: 3,
        background: 'var(--card)', borderRadius: 'var(--r-lg)', padding: '6px 5px',
        border: '1px solid rgba(255,255,255,0.25)',
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <button key={`iu1-${status.emuIncline}`} className={`ctrl-btn ${pulseBtn(inclinePulse, 'up', 'iu1')}`} style={{ ...btn, color: 'var(--text3)' }} {...ph('incline', 1, 'iu1')}>
            <ChevronUp />
          </button>
          <button key={`id1-${status.emuIncline}`} className={`ctrl-btn ${pulseBtn(inclinePulse, 'down', 'id1')}`} style={{ ...btn, color: 'var(--text3)' }} {...ph('incline', -1, 'id1')}>
            <ChevronDown />
          </button>
        </div>
        <div style={{ flex: 1, textAlign: 'center', minWidth: 0, padding: '10px 0' }}>
          <div key={`iv-${status.emuIncline}`} className={`ctrl-value ${pulseVal(inclinePulse)}`} style={{
            fontSize: 26, fontWeight: 600, fontVariantNumeric: 'tabular-nums',
            lineHeight: 1.1, color: 'var(--orange)',
          }}>
            {status.emuIncline}%
          </div>
          <div className="ctrl-label" style={{ fontSize: 10, color: 'var(--text3)', marginTop: 1 }}>incline</div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <button key={`iu5-${status.emuIncline}`} className={`ctrl-btn ${pulseBtn(inclinePulse, 'up', 'iu5')}`} style={{ ...btn, color: 'var(--orange)' }} {...ph('incline', 5, 'iu5')}>
            <DoubleChevronUp />
          </button>
          <button key={`id5-${status.emuIncline}`} className={`ctrl-btn ${pulseBtn(inclinePulse, 'down', 'id5')}`} style={{ ...btn, color: 'var(--orange)' }} {...ph('incline', -5, 'id5')}>
            <DoubleChevronDown />
          </button>
        </div>
      </div>
    </div>
  );
}
