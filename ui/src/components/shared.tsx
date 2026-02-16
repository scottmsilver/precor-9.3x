import React from 'react';

/** Pill-shaped button style used by EmptyRunCard, ProgramComplete, etc. */
export const pillBtn: React.CSSProperties = {
  height: 36, padding: '0 16px', borderRadius: 18,
  border: 'none', background: 'var(--fill)',
  color: 'var(--text2)', fontSize: 13, fontWeight: 500,
  fontFamily: 'inherit', cursor: 'pointer',
  display: 'flex', alignItems: 'center', gap: 6,
  WebkitTapHighlightColor: 'transparent',
};

export function HomeIcon({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  );
}

export function MicIcon({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="1" width="6" height="12" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0" />
      <line x1="12" y1="17" x2="12" y2="21" />
    </svg>
  );
}
