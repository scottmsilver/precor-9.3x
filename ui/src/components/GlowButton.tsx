import React from 'react';

interface GlowButtonProps {
  color: string;       // hex color for glow, e.g. '#7C9A82'
  children: React.ReactNode;
  style?: React.CSSProperties;
  onPointerDown?: () => void;
  onPointerUp?: () => void;
  onPointerLeave?: () => void;
}

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [
    parseInt(h.substring(0, 2), 16),
    parseInt(h.substring(2, 4), 16),
    parseInt(h.substring(4, 6), 16),
  ];
}

export default function GlowButton({
  color, children, style, onPointerDown, onPointerUp, onPointerLeave,
}: GlowButtonProps): React.ReactElement {
  const [r, g, b] = hexToRgb(color);
  // Lighter tint for icon color and top highlight
  const lr = Math.min(255, r + 60);
  const lg = Math.min(255, g + 60);
  const lb = Math.min(255, b + 60);

  return (
    <button
      style={{
        position: 'relative',
        borderRadius: 10,
        // Colored border — subtle accent ring
        border: `1px solid rgba(${r},${g},${b},0.35)`,
        // Layered gradient: accent tint over neutral dark surface
        background: [
          `linear-gradient(180deg, rgba(${lr},${lg},${lb},0.18) 0%, rgba(${r},${g},${b},0.08) 100%)`,
          'linear-gradient(180deg, rgba(66,64,58,1) 0%, rgba(36,34,30,1) 100%)',
        ].join(', '),
        boxShadow: [
          // ── Inner luminescence ──
          `inset 0 1px 0 rgba(${lr},${lg},${lb},0.35)`,   // bright top edge
          `inset 0 0 6px rgba(${r},${g},${b},0.2)`,       // internal color wash
          'inset 0 -1px 0 rgba(0,0,0,0.4)',                // dark bottom edge
          // ── Outer glow (the hero effect) ──
          `0 0 8px 1px rgba(${r},${g},${b},0.5)`,         // tight hot core
          `0 0 18px 4px rgba(${r},${g},${b},0.3)`,        // medium bloom
          `0 0 36px 10px rgba(${r},${g},${b},0.12)`,      // wide soft bloom
          // ── 3D lift ──
          '0 4px 10px rgba(0,0,0,0.55)',                   // drop shadow
          '0 1px 3px rgba(0,0,0,0.45)',                    // contact shadow
        ].join(', '),
        cursor: 'pointer',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        WebkitTapHighlightColor: 'transparent',
        transition: 'transform 60ms var(--ease), box-shadow 150ms var(--ease)',
        // Icon color = lighter tint of accent
        color: `rgb(${lr},${lg},${lb})`,
        fontFamily: 'inherit',
        ...style,
      }}
      onPointerDown={onPointerDown}
      onPointerUp={onPointerUp}
      onPointerLeave={onPointerLeave}
    >
      {children}
    </button>
  );
}
