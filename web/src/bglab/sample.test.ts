import { describe, it, expect } from 'vitest';
import { sampleRegion } from './sample';

function solid(w: number, h: number, r: number, g: number, b: number): ImageData {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) { data[i*4]=r; data[i*4+1]=g; data[i*4+2]=b; data[i*4+3]=255; }
  return { data, width: w, height: h, colorSpace: 'srgb' } as ImageData;
}

describe('sampleRegion', () => {
  it('reports the mean color over the rect', () => {
    const img = solid(100, 100, 80, 160, 120);
    const s = sampleRegion(img, { x: 0.25, y: 0.25, w: 0.5, h: 0.5 }, 'm', 'body');
    expect(s.avg.r).toBeCloseTo(80, 0);
    expect(s.avg.g).toBeCloseTo(160, 0);
    expect(s.luma).toBeGreaterThan(120);
  });
});
