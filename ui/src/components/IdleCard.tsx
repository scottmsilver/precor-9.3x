import type React from 'react';
import { useLocation } from 'wouter';
import { useTreadmillActions } from '../state/TreadmillContext';
import { useMotivation } from '../state/useMotivation';
import { haptic } from '../utils/haptics';
import { renderGlowText } from './Toast';
import HistoryList from './HistoryList';

interface IdleCardProps {
  onVoice?: (prompt?: string) => void;
}

export default function IdleCard({ onVoice }: IdleCardProps): React.ReactElement {
  const [, setLocation] = useLocation();
  const actions = useTreadmillActions();
  const motivation = useMotivation(true);

  return (
    <div style={{
      margin: '0 16px 8px', flex: 1,
      borderRadius: 'var(--r-lg)', background: 'var(--card)',
      border: '1px solid rgba(255,255,255,0.25)',
      display: 'flex', flexDirection: 'column',
      minHeight: 0, overflow: 'hidden',
    }}>
      {/* Motivation + subtitle — top padding clears the floating icons */}
      <div style={{
        textAlign: 'center', padding: '56px 24px 12px',
        flexShrink: 0,
      }}>
        <div style={{
          fontSize: 22, fontWeight: 600, color: 'var(--text2)',
          lineHeight: 1.3,
        }}>
          {renderGlowText(motivation)}
        </div>
        <div style={{
          fontSize: 13, color: 'var(--text3)', marginTop: 6,
        }}>
          Set your speed, touch mic, or pick a program to start
        </div>
      </div>

      {/* History — scrollable */}
      <div style={{
        flex: 1, overflowY: 'auto', minHeight: 0,
        WebkitOverflowScrolling: 'touch',
        paddingBottom: 8,
      }}>
        <HistoryList variant="lobby" onVoice={onVoice} onAfterLoad={() => {
          actions.startProgram();
          haptic([25, 30, 25]);
          setLocation('/run');
        }} />
      </div>
    </div>
  );
}
