export function fmtDur(secs: number | null | undefined): string {
  if (secs == null || secs < 0) secs = 0;
  secs = Math.floor(secs);
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60);
    return `${h}:${String(m % 60).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function paceDisplay(speedMph: number): string {
  if (speedMph <= 0) return '--:--';
  const minPerMile = 60 / speedMph;
  const m = Math.floor(minPerMile);
  const s = Math.round((minPerMile - m) * 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function ivColor(iv: { name: string; speed: number; incline: number } | null): string {
  if (!iv) return 'rgba(107,200,155,0.3)';
  const n = (iv.name || '').toLowerCase();
  if (n.includes('warm') || n.includes('cool')) return 'rgba(107,200,155,0.4)';
  const t = (iv.speed / 12 + iv.incline / 15) / 2;
  const alpha = (0.3 + t * 0.7).toFixed(2);
  return `rgba(107,200,155,${alpha})`;
}
