import { rgbToOklch, oklchToRgb, rgbToOklab, oklabDeltaE, type Oklch } from './color';
import { IVORY, CHARCOAL, type RegionStats, type Theme, type TintCandidate, type BeautyWeights } from './types';

function muted(lch: Oklch): Oklch {
  // keep tints dark and gently saturated so they read as "scrim" not "paint"
  return { L: Math.min(lch.L, 0.32), C: Math.min(lch.C, 0.06), h: lch.h };
}

export function harmonizePalette(
  stats: Pick<RegionStats, 'avg' | 'dominant'>,
  prior: { paletteHue?: number },
): TintCandidate[] {
  const domH = rgbToOklch(stats.dominant).h;
  const hues: number[] = [domH];
  if (prior.paletteHue != null) hues.push(prior.paletteHue);
  hues.push((domH + 180) % 360); // complementary, muted
  const out: TintCandidate[] = hues.map(h => {
    const color = oklchToRgb(muted({ L: 0.26, C: 0.05, h }));
    const dist = oklabDeltaE(rgbToOklab(color), rgbToOklab(stats.dominant));
    return { color, paletteDistance: dist };
  });
  // neutral charcoal fallback (always legible, never off-palette)
  out.push({ color: { r: 26, g: 26, b: 26 }, paletteDistance: 0.0 });
  return out;
}

export function beautyCost(
  theme: Theme,
  palette: TintCandidate[],
  w: BeautyWeights,
  mood: 'cool' | 'warm' | 'dark' | 'neutral',
): number {
  const tintDist = Math.min(...palette.map(c =>
    oklabDeltaE(rgbToOklab(c.color), rgbToOklab(theme.tint))));
  let cost = w.scrim * theme.baseScrimAlpha + w.blur * (theme.blurDp / 3) + w.palette * tintDist;
  const isCharcoalText = theme.text === CHARCOAL || (theme.text.r < 80 && theme.text.g < 80);
  if (isCharcoalText && mood === 'dark') cost += w.charcoalOnDark;
  return cost;
}

export { IVORY, CHARCOAL };
