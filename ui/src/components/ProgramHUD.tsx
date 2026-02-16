import React, { useState, useCallback, useEffect } from 'react';
import { useProgram } from '../state/useProgram';
import { useTreadmillActions } from '../state/TreadmillContext';
import { haptic } from '../utils/haptics';
import ElevationProfile from './ElevationProfile';

const skipBtn: React.CSSProperties = {
  flex: 1, height: 44, borderRadius: 14,
  border: 'none', background: 'rgba(30,29,27,0.7)',
  backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)',
  color: 'var(--text)', fontSize: 15, fontWeight: 600,
  fontFamily: 'inherit', cursor: 'pointer',
  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
  WebkitTapHighlightColor: 'transparent',
};

export default function ProgramHUD(): React.ReactElement | null {
  const pgm = useProgram();
  const actions = useTreadmillActions();
  const [expanded, setExpanded] = useState(false);

  // Reset expanded when paused
  useEffect(() => {
    if (pgm.paused) setExpanded(false);
  }, [pgm.paused]);

  const handleSingleTap = useCallback(() => {
    if (!pgm.paused) {
      setExpanded(v => !v);
      haptic(10);
    }
  }, [pgm.paused]);

  if (!pgm.program || !pgm.running) return null;

  const currentIv = pgm.currentIv;
  if (!currentIv) return null;

  return (
    <div className="pgm-section" style={{
      padding: '0 16px 4px', display: 'flex', flexDirection: 'column',
      flex: 1, minHeight: 0,
    }}>
      {/* Elevation profile card — fills available space */}
      <div
        className="pgm-hud-card"
        style={{
          position: 'relative', borderRadius: 'var(--r-lg)',
          background: 'var(--card)', overflow: 'hidden',
          flex: 1, minHeight: 0,
          display: 'flex', flexDirection: 'column',
        }}
      >
        {/* Elevation SVG fills the card */}
        <div style={{ padding: '6px 4px 2px', flex: 1, minHeight: 0 }}>
          <ElevationProfile onSingleTap={handleSingleTap} />
        </div>

        {/* Position counter */}
        {pgm.intervalCount > 1 && (
          <div style={{
            position: 'absolute', top: 8, right: 10,
            fontSize: 11, color: 'var(--text3)',
            background: 'rgba(30,29,27,0.6)', padding: '2px 8px', borderRadius: 4,
            pointerEvents: 'none',
          }}>
            {pgm.currentInterval + 1} of {pgm.intervalCount}
          </div>
        )}

        {/* Skip overlay — shown on single tap while running (not paused) */}
        {expanded && !pgm.paused && (
          <div style={{
            position: 'absolute', bottom: 8, left: 8, right: 8,
            display: 'flex', gap: 8,
            animation: 'toastSlideUp 150ms var(--ease-decel) forwards',
          }}>
            <button style={skipBtn} onClick={() => { actions.prevInterval(); haptic(25); }}>
              \u00ab Prev
            </button>
            <button style={skipBtn} onClick={() => { actions.skipInterval(); haptic(25); }}>
              Next \u00bb
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
