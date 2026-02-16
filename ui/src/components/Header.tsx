import React, { useRef, useCallback } from 'react';
import { useLocation } from 'wouter';
import { useTreadmillState } from '../state/TreadmillContext';
import { haptic } from '../utils/haptics';

interface HeaderProps {
  onSettingsToggle?: () => void;
}

export default function Header({ onSettingsToggle }: HeaderProps): React.ReactElement {
  const { wsConnected, status } = useTreadmillState();
  const [location, setLocation] = useLocation();
  const debugTaps = useRef<number[]>([]);

  const handleDotClick = useCallback(() => {
    const now = Date.now();
    debugTaps.current.push(now);
    debugTaps.current = debugTaps.current.filter(t => now - t < 500);
    if (debugTaps.current.length >= 3) {
      debugTaps.current = [];
      haptic(50);
      setLocation(location === '/debug' ? '/' : '/debug');
    }
  }, [location, setLocation]);

  const showModeBadge = location === '/debug';
  const isRunning = location === '/run';

  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '4px 16px', flexShrink: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div
          onClick={handleDotClick}
          style={{
            width: 8, height: 8, borderRadius: '50%',
            background: wsConnected ? 'var(--green)' : 'var(--red)',
            transition: 'background 0.3s',
            animation: wsConnected ? 'breathe 2.4s ease-in-out infinite' : 'none',
            cursor: 'pointer',
          }}
        />
        {showModeBadge && (
          <div style={{
            fontSize: 11, fontWeight: 600, textTransform: 'uppercase' as const,
            letterSpacing: '0.02em', padding: '3px 10px', borderRadius: 6,
            background: status.emulate ? 'rgba(139,127,160,0.15)' : status.proxy ? 'rgba(107,200,155,0.15)' : 'var(--fill2)',
            color: status.emulate ? 'var(--purple)' : status.proxy ? 'var(--green)' : 'var(--text3)',
          }}>
            {status.emulate ? 'Emulate' : status.proxy ? 'Proxy' : 'Off'}
          </div>
        )}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        {isRunning && (
          <button
            onClick={() => { setLocation('/'); haptic(25); }}
            style={{
              background: 'none', border: 'none', color: 'var(--text3)', fontSize: '1rem',
              cursor: 'pointer', padding: 8, borderRadius: '50%',
              WebkitTapHighlightColor: 'transparent',
            }}
            title="Home"
          >
            &#8962;
          </button>
        )}
        <button
          onClick={() => { onSettingsToggle?.(); haptic(15); }}
          style={{
            background: 'none', border: 'none', color: 'var(--text3)', fontSize: '1.2rem',
            cursor: 'pointer', padding: 8, borderRadius: '50%',
            WebkitTapHighlightColor: 'transparent',
          }}
        >
          &#9881;
        </button>
      </div>
    </div>
  );
}
