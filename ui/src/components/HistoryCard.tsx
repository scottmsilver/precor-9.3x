import type React from 'react';
import type { HistoryEntry } from '../state/types';
import { fmtDur } from '../utils/formatters';
import { haptic } from '../utils/haptics';

interface HistoryCardProps {
  entry: HistoryEntry;
  variant: 'lobby' | 'compact';
  onLoad: (id: string) => void;
}

export default function HistoryCard({ entry, variant, onLoad }: HistoryCardProps): React.ReactElement {
  const name = entry.program?.name || 'Workout';
  const intervals = entry.program?.intervals?.length || 0;
  const duration = fmtDur(entry.total_duration);

  if (variant === 'lobby') {
    return (
      <div
        onClick={() => { onLoad(entry.id); haptic(25); }}
        style={{
          width: '100%', flexShrink: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          background: 'var(--card)', borderRadius: 'var(--r-md)', padding: 12,
          cursor: 'pointer', WebkitTapHighlightColor: 'transparent',
          transition: 'transform 100ms var(--ease), opacity 100ms var(--ease)',
        }}
      >
        <div>
          <div style={{
            fontSize: 15, fontWeight: 600, marginBottom: 4,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>{name}</div>
          <div style={{ fontSize: 12, color: 'var(--text3)' }}>
            {duration} &middot; {intervals} intervals
          </div>
        </div>
      </div>
    );
  }

  // compact (horizontal scroll)
  return (
    <div
      onClick={() => { onLoad(entry.id); haptic(25); }}
      style={{
        flexShrink: 0, width: 140, background: 'var(--card)',
        borderRadius: 'var(--r-md)', padding: 12, cursor: 'pointer',
        WebkitTapHighlightColor: 'transparent',
        transition: 'transform 100ms var(--ease), opacity 100ms var(--ease)',
      }}
    >
      <div style={{
        fontSize: 13, fontWeight: 600, marginBottom: 4,
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      }}>{name}</div>
      <div style={{ fontSize: 11, color: 'var(--text3)' }}>
        {duration} &middot; {intervals} intervals
      </div>
    </div>
  );
}
