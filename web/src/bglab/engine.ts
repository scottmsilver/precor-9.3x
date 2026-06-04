import { rgbToOklch, oklchToRgb, rgbToOklab, oklabDeltaE, type Oklch, type Rgb } from './color';
import { IVORY, CHARCOAL, ROLE_TARGET_LC, MAX_REGION_SCRIM, SCRIM_STEPS, BLUR_STEPS, DEFAULT_WEIGHTS, type RegionStats, type Theme, type TintCandidate, type BeautyWeights, type Role, type AdvicePrior } from './types';
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

export interface ThemeChoice { theme: Theme; cost: number; runnersUp: { theme: Theme; cost: number }[]; }

function moodOf(prior: AdvicePrior, avgLuma: number): 'cool' | 'warm' | 'dark' | 'neutral' {
  if (prior.mood?.includes('dark') || avgLuma < 70) return 'dark';
  if (prior.mood?.includes('cool')) return 'cool';
  if (prior.mood?.includes('warm')) return 'warm';
  return 'neutral';
}

export function chooseTheme(
  regions: RegionStats[],
  prior: AdvicePrior,
  _targets: Record<Role, number>,
  weights: BeautyWeights = DEFAULT_WEIGHTS,
): ThemeChoice {
  const globalAvg: Rgb = {
    r: regions.reduce((s, r) => s + r.avg.r, 0) / regions.length,
    g: regions.reduce((s, r) => s + r.avg.g, 0) / regions.length,
    b: regions.reduce((s, r) => s + r.avg.b, 0) / regions.length,
  };
  const palette = harmonizePalette({ avg: globalAvg, dominant: regions[0].dominant }, prior);
  const avgLuma = regions.reduce((s, r) => s + r.luma, 0) / regions.length;
  const mood = moodOf(prior, avgLuma);
  const textOrder = prior.suggestedPolarity === 'dark' ? [CHARCOAL, IVORY] : [IVORY, CHARCOAL];

  const scored: { theme: Theme; cost: number }[] = [];
  for (const tintC of palette) {
    for (const text of textOrder) {
      for (const baseScrimAlpha of SCRIM_STEPS) {
        for (const blurDp of BLUR_STEPS) {
          const theme: Theme = { tint: tintC.color, text, blurDp, baseScrimAlpha };
          const allMet = regions.every(r => fitRegion(theme, r).met);
          if (!allMet) continue;
          scored.push({ theme, cost: beautyCost(theme, palette, weights, mood) });
        }
      }
    }
  }

  if (scored.length === 0) {
    // pathological: maximum legibility fallback (neutral dark scrim, ivory text)
    const theme: Theme = { tint: { r: 18, g: 18, b: 18 }, text: IVORY, blurDp: 2, baseScrimAlpha: MAX_REGION_SCRIM };
    return { theme, cost: Infinity, runnersUp: [] };
  }
  scored.sort((a, b) => a.cost - b.cost);
  return { theme: scored[0].theme, cost: scored[0].cost, runnersUp: scored.slice(1, 4) };
}

export { IVORY, CHARCOAL };
