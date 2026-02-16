import type React from 'react';
import { useSession } from '../state/useSession';
import { useProgram } from '../state/useProgram';
import { fmtDur } from '../utils/formatters';

export default function MiniStatusCard(): React.ReactElement | null {
  const sess = useSession();
  const pgm = useProgram();

  if (!sess.active && !pgm.running) return null;

  return (
    <div style={{
      background: 'var(--card)', borderRadius: 'var(--r-md)', padding: '10px 14px',
      margin: '0 16px 8px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text2)' }}>
            {pgm.running && pgm.currentIv ? pgm.currentIv.name : 'Running'}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>
            {sess.speedMph.toFixed(1)} mph &middot; {sess.pace} min/mi
          </div>
        </div>
        <div style={{
          fontSize: 20, fontWeight: 700, fontVariantNumeric: 'tabular-nums',
          color: 'var(--text)',
        }}>
          {sess.active ? sess.elapsedDisplay : fmtDur(pgm.totalElapsed)}
        </div>
      </div>
    </div>
  );
}
