import React from 'react';
import { useLocation } from 'wouter';
import { useProgram } from '../state/useProgram';
import { useSession } from '../state/useSession';
import { fmtDur } from '../utils/formatters';
import { haptic } from '../utils/haptics';
import { pillBtn, HomeIcon, MicIcon } from './shared';

interface ProgramCompleteProps {
  onVoice?: () => void;
}

export default function ProgramComplete({ onVoice }: ProgramCompleteProps): React.ReactElement | null {
  const pgm = useProgram();
  const sess = useSession();
  const [, navigate] = useLocation();

  if (!pgm.completed || pgm.running || !pgm.program) return null;

  return (
    <div className="pgm-section" style={{
      margin: '0 16px 8px', flex: 1,
      borderRadius: 'var(--r-lg)', background: 'var(--card)',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      gap: 12, minHeight: 0,
    }}>
      <div style={{
        width: 48, height: 48, borderRadius: '50%',
        background: 'rgba(107,200,155,0.15)', color: 'var(--green)',
        fontSize: 24, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      }}>&#10003;</div>

      <div style={{ textAlign: 'center' }}>
        <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 6 }}>Workout Complete</div>
        <div style={{ display: 'flex', gap: 16, justifyContent: 'center', fontSize: 13, color: 'var(--text3)' }}>
          <span>{fmtDur(pgm.totalElapsed)}</span>
          <span>{sess.distDisplay} mi</span>
          <span>{sess.vertDisplay} ft</span>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
        <button style={pillBtn} onClick={() => { navigate('/'); haptic(25); }}>
          <HomeIcon /> Home
        </button>
        {onVoice && (
          <button style={pillBtn} onClick={onVoice}>
            <MicIcon /> Voice
          </button>
        )}
      </div>
    </div>
  );
}
