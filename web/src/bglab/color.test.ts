import { describe, it, expect } from 'vitest';
import { hexToRgb, rgbToOklab, oklabToRgb, oklchToRgb, rgbToOklch } from './color';

describe('color', () => {
  it('parses hex', () => {
    expect(hexToRgb('#FFFFFF')).toEqual({ r: 255, g: 255, b: 255 });
    expect(hexToRgb('#000000')).toEqual({ r: 0, g: 0, b: 0 });
  });
  it('round-trips rgb->oklab->rgb', () => {
    const c = { r: 120, g: 200, b: 90 };
    const back = oklabToRgb(rgbToOklab(c));
    expect(Math.abs(back.r - c.r)).toBeLessThan(2);
    expect(Math.abs(back.g - c.g)).toBeLessThan(2);
    expect(Math.abs(back.b - c.b)).toBeLessThan(2);
  });
  it('round-trips oklch hue', () => {
    const lch = rgbToOklch({ r: 40, g: 120, b: 90 });
    const rgb = oklchToRgb(lch);
    const lch2 = rgbToOklch(rgb);
    expect(Math.abs(lch2.h - lch.h)).toBeLessThan(2);
  });
});
