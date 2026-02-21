import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { useLocation } from 'wouter';
import { useSession } from '../state/useSession';
import { useProgram } from '../state/useProgram';
import { useTreadmillState } from '../state/TreadmillContext';
import { useVoiceContext } from '../state/VoiceContext';
import * as api from '../state/api';
import { fmtDur } from '../utils/formatters';
import { haptic } from '../utils/haptics';
import MetricsRow from '../components/MetricsRow';
import ProgramHUD from '../components/ProgramHUD';
import ProgramComplete from '../components/ProgramComplete';
import IdleCard from '../components/IdleCard';
import HistoryList from '../components/HistoryList';
import BottomBar from '../components/BottomBar';
import { HomeIcon, MicIcon } from '../components/shared';

const iconBtn: React.CSSProperties = {
  width: 44, height: 44,
  border: 'none', cursor: 'pointer',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  WebkitTapHighlightColor: 'transparent',
  borderRadius: 'var(--r-md)',
};

const spring = { type: 'spring' as const, stiffness: 400, damping: 28 };
const springBouncy = { type: 'spring' as const, stiffness: 300, damping: 20 };

export default function Running(): React.ReactElement {
  const [, setLocation] = useLocation();
  const sess = useSession();
  const pgm = useProgram();
  const { status } = useTreadmillState();
  const { voiceState, toggle: toggleVoice } = useVoiceContext();
  const [durationEditOpen, setDurationEditOpen] = useState(false);

  const isManual = pgm.program?.manual === true;

  // Delay the idle→HUD visual swap for manual programs so the user can
  // finish tapping speed/incline without the layout shifting under them.
  // Each speed/incline change restarts the settle timer.
  const [visualActive, setVisualActive] = React.useState(false);
  const settleTimer = React.useRef<ReturnType<typeof setTimeout>>();
  const physicalActive = sess.active || pgm.running;

  React.useEffect(() => {
    clearTimeout(settleTimer.current);
    if (physicalActive && isManual && !visualActive) {
      settleTimer.current = setTimeout(() => setVisualActive(true), 1200);
    } else if (physicalActive && !isManual) {
      setVisualActive(true);
    } else if (!physicalActive) {
      setVisualActive(false);
    }
    return () => clearTimeout(settleTimer.current);
  }, [physicalActive, isManual, visualActive, status.emuSpeed, status.emuIncline]);

  const isActive = visualActive;

  const handleTimeTap = () => {
    if (isManual && pgm.running) {
      setDurationEditOpen(v => !v);
      haptic(10);
    }
  };

  const adjustDurationGuard = React.useRef(false);
  const adjustDuration = (deltaMins: number) => {
    if (adjustDurationGuard.current) return;
    adjustDurationGuard.current = true;
    setTimeout(() => { adjustDurationGuard.current = false; }, 400);
    api.adjustDuration(deltaMins * 60);
    haptic(25);
  };

  const homeButton = (
    <motion.button
      layoutId="home-icon"
      onClick={() => { setLocation('/'); haptic(15); }}
      style={{
        ...iconBtn,
        background: isActive ? 'none' : 'var(--card)',
        border: isActive ? 'none' : '1px solid rgba(255,255,255,0.25)',
        color: 'var(--text3)', opacity: 0.7,
      }}
      transition={{ layout: springBouncy }}
      aria-label="Home"
    >
      <HomeIcon size={20} />
    </motion.button>
  );

  const voiceButton = (
    <motion.button
      layoutId="voice-icon"
      onClick={() => { haptic(voiceState === 'idle' ? 20 : 10); toggleVoice(); }}
      style={{
        ...iconBtn,
        background: isActive ? 'none' : 'var(--card)',
        border: isActive ? 'none' : '1px solid rgba(255,255,255,0.25)',
        color: voiceState === 'listening' ? 'var(--red)'
          : voiceState === 'speaking' ? 'var(--purple)'
          : 'var(--text3)',
        opacity: voiceState === 'idle' ? 0.7 : 1,
        transition: 'color 0.2s, opacity 0.2s',
      }}
      transition={{ layout: springBouncy }}
      aria-label={voiceState === 'idle' ? 'Voice' : voiceState === 'listening' ? 'Listening' : 'Speaking'}
    >
      <MicIcon size={20} />
    </motion.button>
  );

  return (
    <div className="run-screen" style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', position: 'relative' }}>
      {/* Hero time with ambient glow */}
      <div className="run-header" style={{
        textAlign: 'center', padding: '12px 16px 4px', flexShrink: 0,
        position: 'relative',
      }}>
        {/* Home & Mic in header when active */}
        {isActive && (
          <>
            <div style={{ position: 'absolute', top: 6, left: 16, zIndex: 2 }}>
              {homeButton}
            </div>
            <div style={{ position: 'absolute', top: 6, right: 16, zIndex: 2 }}>
              {voiceButton}
            </div>
          </>
        )}
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
        <motion.div
          animate={{ height: isActive ? 'auto' : 0, opacity: isActive ? 1 : 0 }}
          transition={spring}
          style={{ overflow: 'hidden', position: 'relative', zIndex: 1 }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {isActive && (
              <motion.div
                className="hero-time font-timer"
                initial={{ opacity: 0, scale: 0.8, y: 8 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                transition={springBouncy}
                onClick={handleTimeTap}
                style={{
                  fontSize: 96,
                  fontWeight: 600,
                  lineHeight: 1,
                  letterSpacing: '-0.02em',
                  color: 'var(--text)',
                  cursor: isManual && pgm.running ? 'pointer' : 'default',
                  WebkitTapHighlightColor: 'transparent',
                }}
              >
                {sess.elapsedDisplay}
              </motion.div>
            )}
          </div>
        </motion.div>

        {isManual && pgm.running && (
          <div className="font-timer" style={{
            fontSize: 12, color: 'var(--text3)', marginTop: 2,
          }}>
            {fmtDur(pgm.totalRemaining)} remaining of {fmtDur(pgm.totalDuration)}
          </div>
        )}

        <AnimatePresence>
          {durationEditOpen && isManual && pgm.running && (
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 12 }}
              transition={spring}
              style={{
                display: 'flex', gap: 8, justifyContent: 'center', marginTop: 8,
              }}
            >
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
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Metrics row */}
      <MetricsRow />

      {/* Elevation profile or empty state — fills available vertical space */}
      <div className="run-viz" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', marginTop: 6, position: 'relative' }}>
        <AnimatePresence mode="wait">
          {pgm.program && pgm.running ? (
            <motion.div
              key="hud"
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.96 }}
              transition={spring}
              style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
            >
              <ProgramHUD />
            </motion.div>
          ) : pgm.completed ? (
            <motion.div
              key="complete"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={springBouncy}
              style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
            >
              <ProgramComplete onVoice={() => { haptic(20); toggleVoice(); }} />
            </motion.div>
          ) : (
            <motion.div
              key="idle"
              initial={{ opacity: 0, scale: 0.92, y: 16 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96 }}
              transition={springBouncy}
              style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, position: 'relative' }}
            >
              {/* Floating icons in the viz area when idle */}
              <div className="idle-icon idle-icon-left">
                {homeButton}
              </div>
              <div className="idle-icon idle-icon-right">
                {voiceButton}
              </div>
              <IdleCard onVoice={(prompt) => { haptic(20); toggleVoice(prompt); }} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {pgm.completed && !pgm.running && (
        <HistoryList variant="compact" />
      )}

      <BottomBar />
    </div>
  );
}
