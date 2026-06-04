import { hexToRgb, type Rgb } from './color';

const Rco = 0.2126729, Gco = 0.7151522, Bco = 0.0721750;
const normBG = 0.56, normTXT = 0.57, revTXT = 0.62, revBG = 0.65;
const blkThrs = 0.022, blkClmp = 1.414, loClip = 0.1, deltaYmin = 0.0005;
const scaleBoW = 1.14, loBoWoffset = 0.027, scaleWoB = 1.14, loWoBoffset = 0.027;

function toY({ r, g, b }: Rgb): number {
  const lin = (c: number) => Math.pow(c / 255, 2.4);
  let y = Rco * lin(r) + Gco * lin(g) + Bco * lin(b);
  if (y < blkThrs) y += Math.pow(blkThrs - y, blkClmp);
  return y;
}

/** APCA Lc. Positive = dark text on light bg; negative = light text on dark bg. */
export function apcaLc(text: string | Rgb, bg: string | Rgb): number {
  const txtY = toY(typeof text === 'string' ? hexToRgb(text) : text);
  const bgY = toY(typeof bg === 'string' ? hexToRgb(bg) : bg);
  if (Math.abs(bgY - txtY) < deltaYmin) return 0;
  let out: number;
  if (bgY > txtY) {
    const s = (Math.pow(bgY, normBG) - Math.pow(txtY, normTXT)) * scaleBoW;
    out = s < loClip ? 0 : s - loBoWoffset;
  } else {
    const s = (Math.pow(bgY, revBG) - Math.pow(txtY, revTXT)) * scaleWoB;
    out = s > -loClip ? 0 : s + loWoBoffset;
  }
  return out * 100;
}
