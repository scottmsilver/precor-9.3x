import React, { useRef, useState, useCallback } from 'react';
import { sampleRegion } from '../bglab/sample';
import { chooseTheme, fitRegion } from '../bglab/engine';
import { ROLE_TARGET_LC, DEFAULT_WEIGHTS, type AdvicePrior, type Theme, type BeautyWeights } from '../bglab/types';
import { rgbToHex } from '../bglab/color';
import { RUNNING_BLOCKS } from '../bglab/layout';

export default function BgLab(): React.ReactElement {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [theme, setTheme] = useState<Theme | null>(null);
  const [fits, setFits] = useState<{ id: string; lc: number; met: boolean; target: number }[]>([]);
  const [weights, setWeights] = useState<BeautyWeights>(DEFAULT_WEIGHTS);
  const [prior, setPrior] = useState<AdvicePrior>({});
  const imgDataRef = useRef<ImageData | null>(null);

  const recompute = useCallback((img: ImageData, p: AdvicePrior, w: BeautyWeights) => {
    const regions = RUNNING_BLOCKS.map(b => sampleRegion(img, b.rect, b.id, b.role));
    const choice = chooseTheme(regions, p, w);
    setTheme(choice.theme);
    setFits(regions.map(r => {
      const f = fitRegion(choice.theme, r);
      return { id: r.id, lc: Math.round(f.lc), met: f.met, target: ROLE_TARGET_LC[r.role] };
    }));
  }, []);

  const onFile = useCallback((file: File) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      const cv = canvasRef.current!; cv.width = image.width; cv.height = image.height;
      const ctx = cv.getContext('2d')!; ctx.drawImage(image, 0, 0);
      const data = ctx.getImageData(0, 0, image.width, image.height);
      imgDataRef.current = data;
      recompute(data, prior, weights);
      URL.revokeObjectURL(url);
    };
    image.src = url;
  }, [prior, weights, recompute]);

  return (
    <div style={{ display: 'flex', gap: 16, padding: 16, fontFamily: 'sans-serif' }}>
      <div style={{ position: 'relative', flex: '0 0 480px' }}>
        <canvas ref={canvasRef} style={{ width: 480, height: 'auto', display: 'block', borderRadius: 12 }} />
        {theme && RUNNING_BLOCKS.map(b => (
          <div key={b.id} style={{
            position: 'absolute', left: `${b.rect.x*100}%`, top: `${b.rect.y*100}%`,
            width: `${b.rect.w*100}%`, height: `${b.rect.h*100}%`,
            background: `rgba(${theme.tint.r},${theme.tint.g},${theme.tint.b},${theme.baseScrimAlpha})`,
            color: rgbToHex(theme.text), display: 'flex', alignItems: 'center', justifyContent: 'center',
            backdropFilter: theme.blurDp ? `blur(${theme.blurDp}px)` : undefined,
            borderRadius: 10, fontSize: b.role === 'hero' ? 28 : 14,
          }}>{b.label}</div>
        ))}
      </div>

      <div style={{ flex: 1, fontSize: 13 }}>
        <input type="file" accept="image/*" onChange={e => e.target.files?.[0] && onFile(e.target.files[0])} />
        <h4>APCA per block</h4>
        <table><tbody>
          {fits.map(f => (
            <tr key={f.id}>
              <td>{f.id}</td>
              <td style={{ color: f.met ? 'green' : 'crimson', fontWeight: 600 }}>Lc {f.lc} / {f.target}</td>
            </tr>
          ))}
        </tbody></table>

        <h4>Beauty weights</h4>
        {(['scrim','blur','palette','charcoalOnDark'] as (keyof BeautyWeights)[]).map(k => (
          <label key={k} style={{ display: 'block' }}>
            {k}: {weights[k].toFixed(2)}
            <input type="range" min={0} max={2} step={0.05} value={weights[k]}
              onChange={e => { const w = { ...weights, [k]: +e.target.value }; setWeights(w); if (imgDataRef.current) recompute(imgDataRef.current, prior, w); }} />
          </label>
        ))}

        <h4>Prior (Gemini advisor)</h4>
        <button onClick={async () => {
          const img = imgDataRef.current; if (!img) return;
          const cv = canvasRef.current!; const b64 = cv.toDataURL('image/jpeg').split(',')[1];
          try {
            const res = await fetch('/api/background/advise', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ image_hash: 'lab-preview', image_b64: b64 }) });
            const p: AdvicePrior = await res.json();
            setPrior(p); recompute(img, p, weights);
          } catch { /* advisor optional */ }
        }}>Fetch advisor prior</button>
        <pre>{JSON.stringify(prior, null, 2)}</pre>

        <h4>Export</h4>
        <pre>{JSON.stringify({ targets: ROLE_TARGET_LC, weights }, null, 2)}</pre>
      </div>
    </div>
  );
}
