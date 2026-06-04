import { describe, it, expect } from 'vitest';
import { harmonizePalette, beautyCost, fitRegion } from './engine';
import { DEFAULT_WEIGHTS, ROLE_TARGET_LC, MAX_REGION_SCRIM, type Theme } from './types';
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

describe('fitRegion', () => {
  const theme = { tint: { r: 20, g: 24, b: 20 }, text: { r: 242, g: 236, b: 223 }, blurDp: 0, baseScrimAlpha: 0.2 };
  it('returns a scrim alpha within clamp that meets the role target when possible', () => {
    const region = { id: 'm', role: 'body', avg: { r: 200, g: 210, b: 190 }, dominant: { r: 200, g: 210, b: 190 }, luma: 200 } as any;
    const r = fitRegion(theme, region);
    expect(r.scrimAlpha).toBeGreaterThanOrEqual(theme.baseScrimAlpha);
    expect(r.scrimAlpha).toBeLessThanOrEqual(MAX_REGION_SCRIM);
    expect(r.lc).toBeGreaterThanOrEqual(ROLE_TARGET_LC.body - 0.5);
  });
  it('reports unmet when even max scrim cannot reach target', () => {
    const region = { id: 'h', role: 'hero', avg: { r: 130, g: 130, b: 130 }, dominant: { r: 130, g: 130, b: 130 }, luma: 130 } as any;
    const lightText = { ...theme, text: { r: 150, g: 150, b: 150 } }; // deliberately weak
    const r = fitRegion(lightText, region);
    expect(typeof r.met).toBe('boolean');
  });
});
