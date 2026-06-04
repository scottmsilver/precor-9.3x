import type { Rgb } from './color';
import type { RegionStats, Role } from './types';

export interface NormRect { x: number; y: number; w: number; h: number; }

export function sampleRegion(img: ImageData, rect: NormRect, id: string, role: Role): RegionStats {
  const x0 = Math.floor(rect.x * img.width), y0 = Math.floor(rect.y * img.height);
  const x1 = Math.min(img.width, Math.ceil((rect.x + rect.w) * img.width));
  const y1 = Math.min(img.height, Math.ceil((rect.y + rect.h) * img.height));
  let sr = 0, sg = 0, sb = 0, n = 0;
  const buckets = new Map<number, number>();
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const i = (y * img.width + x) * 4;
      const r = img.data[i], g = img.data[i+1], b = img.data[i+2];
      sr += r; sg += g; sb += b; n++;
      const key = ((r >> 5) << 6) | ((g >> 5) << 3) | (b >> 5); // 8x8x8 quantize
      buckets.set(key, (buckets.get(key) ?? 0) + 1);
    }
  }
  n = Math.max(1, n);
  const avg: Rgb = { r: sr / n, g: sg / n, b: sb / n };
  let bestKey = 0, bestCount = -1;
  for (const [k, c] of buckets) if (c > bestCount) { bestCount = c; bestKey = k; }
  const dominant: Rgb = {
    r: ((bestKey >> 6) & 7) * 32 + 16,
    g: ((bestKey >> 3) & 7) * 32 + 16,
    b: (bestKey & 7) * 32 + 16,
  };
  const luma = 0.299 * avg.r + 0.587 * avg.g + 0.114 * avg.b;
  return { id, role, avg, dominant, luma };
}
