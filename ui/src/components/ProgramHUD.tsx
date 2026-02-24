import React, { useState, useCallback, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { useProgram } from '../state/useProgram';
import { useTreadmillActions, showBounceMessage } from '../state/TreadmillContext';
import { haptic } from '../utils/haptics';
import ElevationProfile from './ElevationProfile';

export default function ProgramHUD(): React.ReactElement | null {
  const pgm = useProgram();
  const actions = useTreadmillActions();
  const [overlayVisible, setOverlayVisible] = useState(false);
  const autoHideTimer = useRef<ReturnType<typeof setTimeout>>();

  // Cleanup auto-hide timer on unmount
  useEffect(() => {
    return () => clearTimeout(autoHideTimer.current);
  }, []);

  const startAutoHide = useCallback(() => {
    clearTimeout(autoHideTimer.current);
    autoHideTimer.current = setTimeout(() => setOverlayVisible(false), 4000);
  }, []);

  const handlePause = useCallback(() => {
    actions.pauseProgram();
    haptic(25);
    // When pausing: overlay stays (no auto-hide). When resuming: restart auto-hide.
    if (pgm.paused) {
      // Currently paused → will resume → start auto-hide
      startAutoHide();
    } else {
      // Currently playing → will pause → cancel auto-hide so overlay stays
      clearTimeout(autoHideTimer.current);
    }
  }, [actions, pgm.paused, startAutoHide]);

  const handleSingleTap = useCallback(() => {
    setOverlayVisible(v => {
      if (!v) {
        // Opening overlay
        if (!pgm.paused) startAutoHide();
        return true;
      }
      // Closing overlay
      clearTimeout(autoHideTimer.current);
      return false;
    });
    haptic(10);
  }, [pgm.paused, startAutoHide]);

  const handlePrev = useCallback(() => {
    const target = pgm.currentInterval; // 0-based, going back
    actions.prevInterval();
    haptic(25);
    startAutoHide(); // keep overlay, restart timer
    if (target > 0) {
      showBounceMessage(`Back to ${target} of ${pgm.intervalCount}`, 1500);
    }
  }, [actions, pgm.currentInterval, pgm.intervalCount, startAutoHide]);

  const handleNext = useCallback(() => {
    const target = pgm.currentInterval + 2; // 1-based display
    actions.skipInterval();
    haptic(25);
    startAutoHide(); // keep overlay, restart timer
    if (target <= pgm.intervalCount) {
      showBounceMessage(`Skipping to ${target} of ${pgm.intervalCount}`, 1500);
    }
  }, [actions, pgm.currentInterval, pgm.intervalCount, startAutoHide]);

  if (!pgm.program || !pgm.running) return null;

  const currentIv = pgm.currentIv;
  if (!currentIv) return null;

  return (
    <div className="pgm-section" style={{
      padding: '4px 16px 6px', display: 'flex', flexDirection: 'column',
      flex: '1 1 0', minHeight: 0,
    }}>
      {/* Elevation profile card — fills available space */}
      <motion.div
        initial={{ opacity: 0, scale: 0.92, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ type: 'spring', stiffness: 300, damping: 20 }}
        style={{
          position: 'relative', borderRadius: 'var(--r-lg)',
          background: 'var(--card)', overflow: 'hidden',
          border: '1px solid rgba(255,255,255,0.25)',
          flex: 1, minHeight: 0,
          display: 'flex', flexDirection: 'column',
          containerType: 'size',
        } as React.CSSProperties}
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

        {/* Player overlay — dark backdrop + YouTube-style circular controls */}
        <AnimatePresence>
          {overlayVisible && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.25 }}
              style={{
                position: 'absolute', inset: 0,
                background: 'rgba(0,0,0,0.4)',
                backdropFilter: 'blur(6px)', WebkitBackdropFilter: 'blur(6px)',
                borderRadius: 'inherit',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                gap: 'clamp(16px, 4cqw, 32px)',
              }}
            >
              {/* Skip Previous — glass circle */}
              {pgm.intervalCount > 1 && (
                <motion.button
                  style={{
                    width: 'clamp(48px, 14cqh, 68px)',
                    height: 'clamp(48px, 14cqh, 68px)',
                    borderRadius: '50%',
                    border: '1px solid rgba(255,255,255,0.18)',
                    background: 'rgba(255,255,255,0.10)',
                    backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)',
                    boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.12), 0 2px 8px rgba(0,0,0,0.3)',
                    color: 'rgba(255,255,255,0.9)', fontSize: 'clamp(18px, 6cqh, 28px)',
                    fontFamily: 'inherit', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    WebkitTapHighlightColor: 'transparent',
                  }}
                  whileTap={{ scale: 0.85, background: 'rgba(255,255,255,0.18)' }}
                  transition={{ type: 'spring', stiffness: 800, damping: 15 }}
                  onClick={handlePrev}
                >
                  {'\u23EE\uFE0E'}
                </motion.button>
              )}

              {/* Play/Pause — glass circle, large center */}
              <motion.button
                style={{
                  width: 'clamp(68px, 22cqh, 100px)',
                  height: 'clamp(68px, 22cqh, 100px)',
                  borderRadius: '50%',
                  border: '1px solid rgba(255,255,255,0.22)',
                  background: 'rgba(255,255,255,0.10)',
                  backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)',
                  boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.15), 0 4px 12px rgba(0,0,0,0.4)',
                  color: 'rgba(255,255,255,0.9)', fontSize: 'clamp(28px, 10cqh, 44px)',
                  fontFamily: 'inherit', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  WebkitTapHighlightColor: 'transparent',
                }}
                whileTap={{ scale: 0.85, background: 'rgba(255,255,255,0.20)' }}
                transition={{ type: 'spring', stiffness: 800, damping: 15 }}
                onClick={handlePause}
              >
                {pgm.paused ? '\u25b6\uFE0E' : '\u23f8\uFE0E'}
              </motion.button>

              {/* Skip Next — glass circle */}
              {pgm.intervalCount > 1 && (
                <motion.button
                  style={{
                    width: 'clamp(48px, 14cqh, 68px)',
                    height: 'clamp(48px, 14cqh, 68px)',
                    borderRadius: '50%',
                    border: '1px solid rgba(255,255,255,0.18)',
                    background: 'rgba(255,255,255,0.10)',
                    backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)',
                    boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.12), 0 2px 8px rgba(0,0,0,0.3)',
                    color: 'rgba(255,255,255,0.9)', fontSize: 'clamp(18px, 6cqh, 28px)',
                    fontFamily: 'inherit', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    WebkitTapHighlightColor: 'transparent',
                  }}
                  whileTap={{ scale: 0.85, background: 'rgba(255,255,255,0.18)' }}
                  transition={{ type: 'spring', stiffness: 800, damping: 15 }}
                  onClick={handleNext}
                >
                  {'\u23ED\uFE0E'}
                </motion.button>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}
