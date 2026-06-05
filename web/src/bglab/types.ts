import type { Rgb } from './color';

export interface RegionStats {
  /** id of the text block, e.g. 'timer', 'metric.speed' */
  id: string;
  role: Role;
  avg: Rgb;
  dominant: Rgb;
  /** 0..255 mean luma (Rec.601), kept for quick fallbacks */
  luma: number;
}

export type Role = 'hero' | 'body' | 'muted';

export const ROLE_TARGET_LC: Record<Role, number> = { hero: 75, body: 60, muted: 45 };

export interface TintCandidate { color: Rgb; paletteDistance: number; }

export interface Theme {
  tint: Rgb;
  text: Rgb;          // the global ivory/charcoal choice
  blurDp: number;
  baseScrimAlpha: number;
}

export interface AdvicePrior {
  paletteHue?: number;                 // OKLCH degrees
  suggestedPolarity?: 'light' | 'dark';
  mood?: string;
  busyZones?: { x: number; y: number; w: number; h: number; note?: string }[];
}

export const IVORY: Rgb = { r: 242, g: 236, b: 223 };
export const CHARCOAL: Rgb = { r: 30, g: 32, b: 30 };

export interface BeautyWeights {
  scrim: number; blur: number; palette: number; charcoalOnDark: number;
}
export const DEFAULT_WEIGHTS: BeautyWeights = { scrim: 1.0, blur: 0.5, palette: 0.6, charcoalOnDark: 0.3 };

export const SCRIM_STEPS = [0.18, 0.28, 0.38, 0.5, 0.62];
export const BLUR_STEPS = [0, 1, 2, 3];
export const MAX_REGION_SCRIM = 0.72;
