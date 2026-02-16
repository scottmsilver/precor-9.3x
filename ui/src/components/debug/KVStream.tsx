import React, { useRef, useEffect } from 'react';
import type { KVEntry } from '../../state/types';

interface KVStreamProps {
  kvLog: KVEntry[];
}

export default function KVStream({ kvLog }: KVStreamProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const isNearBottom = useRef(true);

  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    isNearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  }

  useEffect(() => {
    const el = scrollRef.current;
    if (el && isNearBottom.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [kvLog.length]);

  return (
    <div
      ref={scrollRef}
      onScroll={handleScroll}
      style={{ flex: 1, overflowY: 'auto', padding: 0 }}
    >
      {kvLog.map((entry, i) => (
        <div key={i} style={{
          display: 'flex',
          alignItems: 'baseline',
          padding: '1px 8px',
          borderBottom: '1px solid rgba(255,255,255,0.02)',
        }}>
          <span style={{
            width: 50,
            flexShrink: 0,
            color: 'var(--text4)',
            fontSize: 10,
          }}>
            {entry.ts}
          </span>
          <span style={{
            width: 12,
            flexShrink: 0,
            fontSize: 9,
            textAlign: 'center',
            color: entry.src === 'motor' ? 'var(--orange)' : 'var(--teal)',
          }}>
            {entry.src === 'motor' ? '\u25C2' : '\u25B8'}
          </span>
          <span style={{
            width: 42,
            flexShrink: 0,
            color: 'var(--text3)',
          }}>
            {entry.key}
          </span>
          <span style={{
            color: entry.src === 'motor' ? 'var(--orange)' : 'var(--teal)',
          }}>
            {entry.value}
          </span>
        </div>
      ))}
    </div>
  );
}
