import React, { useState, useEffect, useCallback } from 'react';
import type { HistoryEntry } from '../state/types';
import * as api from '../state/api';
import { useToast } from '../state/TreadmillContext';
import { haptic } from '../utils/haptics';
import HistoryCard from './HistoryCard';
import { MicIcon } from './shared';

interface HistoryListProps {
  variant: 'lobby' | 'compact';
  onAfterLoad?: () => void;
  onVoice?: (prompt?: string) => void;
}

export default function HistoryList({ variant, onAfterLoad, onVoice }: HistoryListProps): React.ReactElement | null {
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

  const handleCustomWorkout = useCallback(async () => {
    if (!onVoice) return;
    haptic(20);
    try {
      const prompt = await api.getVoicePrompt('custom-workout');
      onVoice(prompt);
    } catch {
      // Fallback: start voice without prompt
      onVoice();
    }
  }, [onVoice]);

  if (variant === 'lobby') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: '0 16px' }}>
        {onVoice && (
          <div
            onClick={handleCustomWorkout}
            style={{
              width: '100%', flexShrink: 0,
              display: 'flex', alignItems: 'center', gap: 10,
              background: 'var(--card)', borderRadius: 'var(--r-md)', padding: 12,
              cursor: 'pointer', WebkitTapHighlightColor: 'transparent',
              transition: 'transform 100ms var(--ease), opacity 100ms var(--ease)',
            }}
          >
            <div style={{
              width: 32, height: 32, borderRadius: 8,
              background: 'rgba(139,127,160,0.15)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: 'var(--purple)', flexShrink: 0,
            }}>
              <MicIcon size={16} />
            </div>
            <div>
              <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--purple)' }}>
                Tell us your own
              </div>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>
                Describe a workout by voice
              </div>
            </div>
          </div>
        )}
        {history.map(h => (
          <HistoryCard key={h.id} entry={h} variant="lobby" onLoad={handleLoad} />
        ))}
      </div>
    );
  }

  if (history.length === 0) return null;

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
