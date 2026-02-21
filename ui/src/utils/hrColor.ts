export function hrColor(bpm: number): string {
  if (bpm >= 170) return 'var(--red)';
  if (bpm >= 150) return 'var(--orange)';
  if (bpm >= 120) return 'var(--yellow)';
  return 'var(--green)';
}
