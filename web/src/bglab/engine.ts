import { rgbToOklch, oklchToRgb, rgbToOklab, oklabDeltaE, type Oklch, type Rgb } from './color';
import { IVORY, CHARCOAL, ROLE_TARGET_LC, MAX_REGION_SCRIM, type RegionStats, type Theme, type TintCandidate, type BeautyWeights } from './types';
import { apcaLc } from './apca';

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

export interface RegionFit { scrimAlpha: number; lc: number; met: boolean; }

/** Blend the scrim (theme.tint at alpha) over the region bg, then measure text Lc. */
function composite(bg: Rgb, tint: Rgb, alpha: number): Rgb {
  return {
    r: bg.r * (1 - alpha) + tint.r * alpha,
    g: bg.g * (1 - alpha) + tint.g * alpha,
    b: bg.b * (1 - alpha) + tint.b * alpha,
  };
}

export function fitRegion(theme: Theme, region: RegionStats): RegionFit {
  const target = ROLE_TARGET_LC[region.role];
  let alpha = theme.baseScrimAlpha;
  const step = 0.04;
  for (; alpha <= MAX_REGION_SCRIM + 1e-9; alpha += step) {
    const bg = composite(region.avg, theme.tint, Math.min(alpha, MAX_REGION_SCRIM));
    const lc = Math.abs(apcaLc(theme.text, bg));
    if (lc >= target) return { scrimAlpha: Math.min(alpha, MAX_REGION_SCRIM), lc, met: true };
  }
  const bg = composite(region.avg, theme.tint, MAX_REGION_SCRIM);
  return { scrimAlpha: MAX_REGION_SCRIM, lc: Math.abs(apcaLc(theme.text, bg)), met: false };
}

export { IVORY, CHARCOAL };
