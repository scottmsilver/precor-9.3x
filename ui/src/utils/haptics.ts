export function haptic(pattern: number | number[] = 10): void {
  if (navigator.vibrate) {
    navigator.vibrate(pattern);
  }
}
