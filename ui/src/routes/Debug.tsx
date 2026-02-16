import React, { useState } from 'react';
import { useTreadmillState } from '../state/TreadmillContext';
import MotorCache from '../components/debug/MotorCache';
import KVStream from '../components/debug/KVStream';
import LogViewer from '../components/debug/LogViewer';

type Tab = 'stream' | 'log';

export default function Debug() {
  const { kvLog, status } = useTreadmillState();
  const [tab, setTab] = useState<Tab>('stream');

  return (
    <div style={{
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
      fontFamily: "'SF Mono', Menlo, monospace",
      fontSize: 11,
    }}>
      {/* Motor cache — always visible */}
      <MotorCache motor={status.motor} />

      {/* Tab bar */}
      <div style={{
        display: 'flex',
        flexShrink: 0,
        borderBottom: '1px solid var(--fill2)',
      }}>
        {(['stream', 'log'] as const).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              flex: 1,
              background: 'none',
              border: 'none',
              borderBottom: tab === t ? '2px solid var(--teal)' : '2px solid transparent',
              color: tab === t ? 'var(--text)' : 'var(--text4)',
              fontSize: 11,
              fontFamily: "'SF Mono', Menlo, monospace",
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.04em',
              padding: '8px 0',
              cursor: 'pointer',
              minHeight: 44,
              WebkitTapHighlightColor: 'transparent',
              transition: 'color 0.2s, border-color 0.2s',
            }}
          >
            {t === 'stream' ? 'Stream' : 'Log'}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === 'stream' && <KVStream kvLog={kvLog} />}
      {tab === 'log' && <LogViewer />}
    </div>
  );
}
