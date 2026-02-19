import React from 'react';
import { useLocation } from 'wouter';
import { useTreadmillState } from '../state/TreadmillContext';
import { haptic } from '../utils/haptics';
import { MicIcon } from './shared';
import type { VoiceState } from '../state/useVoice';

interface TabBarProps {
  voiceState: VoiceState;
  onVoiceToggle: () => void;
  onSettingsToggle: () => void;
}

function TabItem({ icon, label, active, onClick, dot, color }: {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
  dot?: 'green' | 'red';
  color?: string;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 2,
        height: 60,
        background: 'none',
        border: 'none',
        color: color || (active ? 'var(--text)' : 'var(--text3)'),
        cursor: 'pointer',
        WebkitTapHighlightColor: 'transparent',
        fontFamily: 'inherit',
        padding: 0,
        position: 'relative',
      }}
    >
      <div style={{ position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {icon}
        {dot && (
          <div style={{
            position: 'absolute',
            top: -1,
            right: -4,
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: dot === 'green' ? 'var(--green)' : 'var(--red)',
          }} />
        )}
      </div>
      <span className="tab-label" style={{ fontSize: 10, fontWeight: 500, lineHeight: 1 }}>{label}</span>
    </button>
  );
}

export default function TabBar({ voiceState, onVoiceToggle, onSettingsToggle }: TabBarProps): React.ReactElement {
  const { wsConnected } = useTreadmillState();
  const [location, setLocation] = useLocation();

  const voiceActive = voiceState === 'listening' || voiceState === 'speaking';
  const isHome = location === '/' || location === '';
  const isRun = location.startsWith('/run');

  // Hide tab bar on /run — BottomBar has its own Home affordance
  if (isRun) return null as unknown as React.ReactElement;

  const voiceColor = voiceActive
    ? (voiceState === 'listening' ? 'var(--red)' : 'var(--purple)')
    : undefined;

  return (
    <nav style={{
      position: 'fixed',
      bottom: 0, left: 0, right: 0,
      zIndex: 100,
      background: 'var(--card)',
      borderTop: '1px solid var(--separator)',
      paddingBottom: 'env(safe-area-inset-bottom, 0px)',
    }}>
      <div style={{ display: 'flex', height: 60 }}>
        <TabItem
          icon={<HomeIcon />}
          label="Home"
          active={isHome}
          dot={wsConnected ? 'green' : 'red'}
          onClick={() => { setLocation('/'); haptic(15); }}
        />

        <TabItem
          icon={<RunIcon />}
          label="Run"
          active={isRun}
          onClick={() => { setLocation('/run'); haptic(15); }}
        />

        <TabItem
          icon={<SettingsIcon />}
          label="Settings"
          active={false}
          onClick={() => { onSettingsToggle(); haptic(15); }}
        />

        <TabItem
          icon={<MicIcon size={22} />}
          label={voiceActive ? (voiceState === 'listening' ? 'Listening' : 'Speaking') : 'Voice'}
          active={voiceActive}
          color={voiceColor}
          onClick={() => { haptic(voiceState === 'idle' ? 20 : 10); onVoiceToggle(); }}
        />
      </div>
    </nav>
  );
}

function HomeIcon() {
  return (
    <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  );
}

function RunIcon() {
  return (
    <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  );
}

function SettingsIcon() {
  return (
    <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}
