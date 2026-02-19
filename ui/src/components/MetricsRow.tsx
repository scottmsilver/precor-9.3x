import type React from 'react';
import { motion } from 'motion/react';
import { useSession } from '../state/useSession';

export default function MetricsRow(): React.ReactElement {
  const sess = useSession();

  return (
    <motion.div
      className="metrics-ro"
      animate={{ height: sess.active ? 'auto' : 0, opacity: sess.active ? 1 : 0 }}
      transition={{ type: 'spring', stiffness: 400, damping: 28 }}
      style={{
        overflow: 'hidden',
        display: 'flex', justifyContent: 'center', gap: 20,
        padding: '0 16px 4px', flexShrink: 0,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
        <span className="metric-value" style={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums', color: 'var(--teal)' }}>{sess.pace}</span>
        <span className="metric-label" style={{ fontSize: 10, color: 'var(--text3)' }}>min/mi</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
        <span className="metric-value" style={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{sess.distDisplay}</span>
        <span className="metric-label" style={{ fontSize: 10, color: 'var(--text3)' }}>miles</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
        <span className="metric-value" style={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums', color: 'var(--orange)' }}>{sess.vertDisplay}</span>
        <span className="metric-label" style={{ fontSize: 10, color: 'var(--text3)' }}>vert ft</span>
      </div>
    </motion.div>
  );
}
