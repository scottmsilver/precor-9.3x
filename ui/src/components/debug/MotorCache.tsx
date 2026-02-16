import React from 'react';

interface MotorCacheProps {
  motor: Record<string, string>;
}

export default function MotorCache({ motor }: MotorCacheProps) {
  const keys = Object.keys(motor).sort();

  return (
    <div style={{
      flexShrink: 0,
      borderBottom: '2px solid var(--fill2)',
      padding: '6px 8px',
    }}>
      <div style={{
        color: 'var(--orange)',
        fontSize: 10,
        fontWeight: 600,
        marginBottom: 4,
      }}>
        MOTOR LAST VALUES
      </div>
      <div style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '2px 10px',
      }}>
        {keys.map(key => (
          <span key={key}>
            <span style={{ color: 'var(--text4)' }}>{key}</span>
            <span style={{ color: 'var(--text4)' }}>:</span>
            <span style={{ color: 'var(--orange)' }}>{motor[key]}</span>
          </span>
        ))}
        {keys.length === 0 && (
          <span style={{ color: 'var(--text4)' }}>Waiting...</span>
        )}
      </div>
    </div>
  );
}
