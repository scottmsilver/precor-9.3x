import React, { useState, useEffect, useCallback } from 'react';
import { getLog } from '../../state/api';

export default function LogViewer() {
  const [lines, setLines] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchLog = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getLog(200);
      setLines(data.lines);
    } catch {
      setLines(['(failed to fetch log)']);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchLog();
  }, [fetchLog]);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '6px 8px',
        borderBottom: '1px solid var(--fill2)',
      }}>
        <button
          onClick={fetchLog}
          disabled={loading}
          style={{
            background: 'var(--fill)',
            border: 'none',
            color: 'var(--text2)',
            fontSize: 11,
            fontFamily: "'SF Mono', Menlo, monospace",
            padding: '4px 10px',
            borderRadius: 6,
            cursor: loading ? 'default' : 'pointer',
            opacity: loading ? 0.5 : 1,
            minHeight: 28,
            WebkitTapHighlightColor: 'transparent',
          }}
        >
          {loading ? 'Loading...' : 'Refresh'}
        </button>
        <span style={{ fontSize: 10, color: 'var(--text4)' }}>
          {lines.length} lines
        </span>
      </div>
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '4px 8px',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-all',
        fontSize: 11,
        lineHeight: 1.5,
        color: 'var(--text2)',
      }}>
        {lines.map((line, i) => (
          <div key={i}>{line}</div>
        ))}
      </div>
    </div>
  );
}
