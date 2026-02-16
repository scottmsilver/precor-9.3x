import type React from 'react';
import { useSession } from '../state/useSession';

export default function MetricsRow(): React.ReactElement {
  const sess = useSession();

  return (
    <div className="metrics-ro" style={{
      display: 'flex', justifyContent: 'center', gap: 20,
      padding: '0 16px 4px', flexShrink: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
        <span style={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums', color: 'var(--teal)' }}>{sess.pace}</span>
        <span style={{ fontSize: 10, color: 'var(--text3)' }}>min/mi</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
        <span style={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{sess.distDisplay}</span>
        <span style={{ fontSize: 10, color: 'var(--text3)' }}>miles</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
        <span style={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums', color: 'var(--orange)' }}>{sess.vertDisplay}</span>
        <span style={{ fontSize: 10, color: 'var(--text3)' }}>vert ft</span>
      </div>
    </div>
  );
}
