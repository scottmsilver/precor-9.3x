import React, { useState, useEffect, useCallback } from 'react';
import type { HistoryEntry } from '../state/types';
import * as api from '../state/api';
import { useToast } from '../state/TreadmillContext';
import { haptic } from '../utils/haptics';
import HistoryCard from './HistoryCard';

interface HistoryListProps {
  variant: 'lobby' | 'compact';
  onAfterLoad?: () => void;
}

export default function HistoryList({ variant, onAfterLoad }: HistoryListProps): React.ReactElement | null {
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const showToast = useToast();

  useEffect(() => {
    api.getHistory().then(setHistory).catch(() => {});
  }, []);

  const handleLoad = useCallback(async (id: string) => {
    try {
      const res = await api.loadFromHistory(id);
      if (res?.ok && res.program) {
        haptic(25);
        onAfterLoad?.();
      }
    } catch (_e) {
      showToast('Failed to load program');
    }
  }, [showToast, onAfterLoad]);

  if (history.length === 0) return null;

  if (variant === 'lobby') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '0 16px', overflowX: 'visible' }}>
        {history.map(h => (
          <HistoryCard key={h.id} entry={h} variant="lobby" onLoad={handleLoad} />
        ))}
      </div>
    );
  }

  // compact: horizontal scroll
  return (
    <div className="history-section" style={{ padding: '8px 0', flexShrink: 0 }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '0 16px 8px',
      }}>
        <div style={{
          fontSize: 13, fontWeight: 600, color: 'var(--text3)',
          textTransform: 'uppercase' as const, letterSpacing: '0.02em',
        }}>Recent Programs</div>
      </div>
      <div style={{
        display: 'flex', gap: 8, overflowX: 'auto', padding: '0 16px 8px',
        WebkitOverflowScrolling: 'touch', scrollbarWidth: 'none',
      }}>
        {history.map(h => (
          <HistoryCard key={h.id} entry={h} variant="compact" onLoad={handleLoad} />
        ))}
      </div>
    </div>
  );
}
