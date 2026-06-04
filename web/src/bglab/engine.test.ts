import { describe, it, expect } from 'vitest';
import { harmonizePalette, beautyCost } from './engine';
import { DEFAULT_WEIGHTS, type Theme } from './types';
import { rgbToOklch } from './color';

const stats = { avg: { r: 60, g: 90, b: 70 }, dominant: { r: 40, g: 80, b: 60 }, luma: 80 } as any;

describe('harmonizePalette', () => {
  it('biases toward the prior hue and always includes a neutral fallback', () => {
    const pal = harmonizePalette({ avg: stats.avg, dominant: stats.dominant }, { paletteHue: 200 });
    expect(pal.length).toBeGreaterThanOrEqual(3);
    const nearPrior = pal.some(c => Math.abs(((rgbToOklch(c.color).h - 200 + 540) % 360) - 180) < 40);
    expect(nearPrior).toBe(true);
    const neutral = pal.some(c => rgbToOklch(c.color).C < 0.03);
    expect(neutral).toBe(true);
  });
});

describe('beautyCost', () => {
  it('increases monotonically with scrim and blur', () => {
    const base: Theme = { tint: { r: 40, g: 80, b: 60 }, text: { r: 242, g: 236, b: 223 }, blurDp: 0, baseScrimAlpha: 0.2 };
    const heavier: Theme = { ...base, baseScrimAlpha: 0.5 };
    const blurry: Theme = { ...base, blurDp: 3 };
    const palRef = { color: base.tint, paletteDistance: 0 };
    expect(beautyCost(heavier, [palRef], DEFAULT_WEIGHTS, 'cool')).toBeGreaterThan(beautyCost(base, [palRef], DEFAULT_WEIGHTS, 'cool'));
    expect(beautyCost(blurry, [palRef], DEFAULT_WEIGHTS, 'cool')).toBeGreaterThan(beautyCost(base, [palRef], DEFAULT_WEIGHTS, 'cool'));
  });
});
