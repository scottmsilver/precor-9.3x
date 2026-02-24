import React, { memo, useRef, useCallback, useState } from 'react';
import { useProgram } from '../state/useProgram';
import { useTreadmillActions } from '../state/TreadmillContext';

// Chart area matches useProgram constants
const W = 400, H = 140, PAD = 10;
// Margins for axis labels
const ML = 28, MR = 4, MT = 4, MB = 18;
const VB_W = ML + W + MR;
const VB_H = MT + H + MB;

function inclineY(inc: number, yMax: number): number {
  return MT + H - PAD - (inc / yMax) * (H - PAD * 2);
}

/** D3-style "nice numbers" tick generation for time axis */
function computeTimeTicks(totalSec: number): { sec: number; label: string }[] {
  if (totalSec <= 0) return [];

  const TARGET_TICKS = 6;
  const rawStep = totalSec / TARGET_TICKS;

  const niceSteps = [30, 60, 120, 300, 600, 900, 1800, 3600, 7200];
  let step = niceSteps[0];
  for (const s of niceSteps) {
    step = s;
    if (s >= rawStep) break;
  }

  const ticks: { sec: number; label: string }[] = [];
  const useHourFormat = totalSec >= 3600;

  for (let t = step; t < totalSec; t += step) {
    if (totalSec - t < step * 0.15) continue;

    let label: string;
    if (useHourFormat) {
      const hrs = Math.floor(t / 3600);
      const mins = Math.round((t % 3600) / 60);
      if (hrs === 0) {
        label = `:${String(mins).padStart(2, '0')}`;
      } else if (mins === 0) {
        label = `${hrs}h`;
      } else {
        label = `${hrs}:${String(mins).padStart(2, '0')}`;
      }
    } else {
      label = `${Math.round(t / 60)}`;
    }
    ticks.push({ sec: t, label });
  }

  return ticks;
}

/** Compute Y-axis ticks based on max incline in the program */
function computeInclineTicks(maxIncline: number): number[] {
  if (maxIncline <= 5) return [0, 2, 5];
  if (maxIncline <= 10) return [0, 5, 10];
  return [0, 5, 10, 15];
}

// Shared label style — white for readability
const labelStyle: React.CSSProperties = {
  position: 'absolute', pointerEvents: 'none',
  fontSize: 9, fontWeight: 500, fontFamily: 'inherit',
  color: 'rgba(232,228,223,0.5)',
};

// Tick mark size
const TICK = 4;

// Double-tap detection threshold (ms)
const DOUBLE_TAP_MS = 300;

// Arrow indicator style — circle background for visibility
const arrowStyle: React.CSSProperties = {
  position: 'absolute', top: '50%', transform: 'translateY(-50%)',
  width: 48, height: 48, borderRadius: '50%',
  background: 'rgba(30,29,27,0.7)',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  fontSize: 24, fontWeight: 700, color: 'rgba(232,228,223,0.8)',
  pointerEvents: 'none', zIndex: 2,
  animation: 'arrowFlash 400ms ease-out forwards',
};

interface ElevationProfileProps {
  onSingleTap?: () => void;
}

const ElevationProfile = memo(function ElevationProfile({ onSingleTap }: ElevationProfileProps): React.ReactElement {
  const pgm = useProgram();
  const actions = useTreadmillActions();

  const timeTicks = computeTimeTicks(pgm.totalDuration);
  const yMax = pgm.yAxisMax;
  const inclineTicks = computeInclineTicks(pgm.maxIncline);

  // Position dot as percentage
  const dotLeft = ((ML + pgm.elevPosX) / VB_W) * 100;
  const dotTop = ((MT + pgm.elevPosY) / VB_H) * 100;

  // Double-tap detection
  const lastTapTime = useRef(0);
  const lastTapSide = useRef<'left' | 'right' | null>(null);
  const singleTapTimer = useRef<ReturnType<typeof setTimeout>>();

  // Arrow indicator state
  const [arrow, setArrow] = useState<'left' | 'right' | null>(null);
  const arrowTimer = useRef<ReturnType<typeof setTimeout>>();

  const showArrow = useCallback((dir: 'left' | 'right') => {
    clearTimeout(arrowTimer.current);
    setArrow(dir);
    arrowTimer.current = setTimeout(() => setArrow(null), 400);
  }, []);

  const handleTap = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (!pgm.running) return;

    const rect = e.currentTarget.getBoundingClientRect();
    const relX = (e.clientX - rect.left) / rect.width;
    const side: 'left' | 'right' = relX > 0.5 ? 'right' : 'left';
    const now = Date.now();

    if (now - lastTapTime.current < DOUBLE_TAP_MS && lastTapSide.current === side) {
      // Double-tap detected — cancel pending single tap
      clearTimeout(singleTapTimer.current);
      lastTapTime.current = 0;
      lastTapSide.current = null;

      showArrow(side);
      if (side === 'right') {
        actions.skipInterval();
      } else {
        actions.prevInterval();
      }
    } else {
      // First tap — wait to see if it becomes a double tap
      lastTapTime.current = now;
      lastTapSide.current = side;
      clearTimeout(singleTapTimer.current);
      singleTapTimer.current = setTimeout(() => {
        // Confirmed single tap
        lastTapTime.current = 0;
        lastTapSide.current = null;
        onSingleTap?.();
      }, DOUBLE_TAP_MS);
    }
  }, [pgm.running, actions, onSingleTap, showArrow]);

  return (
    <div
      data-testid="elevation-profile"
      onClick={handleTap}
      style={{ position: 'relative', width: '100%', height: '100%', cursor: pgm.running ? 'pointer' : 'default' }}
    >
      {/* SVG — only stretchable geometry (paths, grid lines) */}
      <svg viewBox={`0 0 ${VB_W} ${VB_H}`} preserveAspectRatio="none" style={{ display: 'block', width: '100%', height: '100%' }}>
        <defs>
          <clipPath id="elev-done">
            <rect x="-1" y="-1" width={pgm.elevPosX + 1} height={H + 2} />
          </clipPath>
          <clipPath id="elev-future">
            <rect x={pgm.elevPosX} y="-1" width={W - pgm.elevPosX + 1} height={H + 2} />
          </clipPath>
        </defs>

        {/* Axis lines (D3-style: left + bottom edges) */}
        <line x1={ML} y1={MT} x2={ML} y2={MT + H}
          stroke="rgba(232,228,223,0.2)" strokeWidth="1" />
        <line x1={ML} y1={MT + H} x2={ML + W} y2={MT + H}
          stroke="rgba(232,228,223,0.2)" strokeWidth="1" />

        {/* Y-axis grid lines */}
        {inclineTicks.map(inc => (
          <line key={inc} x1={ML} y1={inclineY(inc, yMax)} x2={ML + W} y2={inclineY(inc, yMax)}
            stroke="rgba(232,228,223,0.12)" strokeWidth="0.5" strokeDasharray="3,4" />
        ))}

        {/* Y-axis tick marks — extend left from axis line */}
        {inclineTicks.map(inc => (
          <line key={`ytick-${inc}`} x1={ML - TICK} y1={inclineY(inc, yMax)} x2={ML} y2={inclineY(inc, yMax)}
            stroke="rgba(232,228,223,0.25)" strokeWidth="1" />
        ))}

        {/* X-axis grid lines */}
        {timeTicks.map(({ sec, label }) => {
          const svgX = ML + (sec / pgm.totalDuration) * W;
          return (
            <line key={label} x1={svgX} y1={MT} x2={svgX} y2={MT + H}
              stroke="rgba(232,228,223,0.1)" strokeWidth="0.5" strokeDasharray="3,4" />
          );
        })}

        {/* X-axis tick marks — extend down from axis line */}
        {timeTicks.map(({ sec, label }) => {
          const svgX = ML + (sec / pgm.totalDuration) * W;
          return (
            <line key={`xtick-${label}`} x1={svgX} y1={MT + H} x2={svgX} y2={MT + H + TICK}
              stroke="rgba(232,228,223,0.25)" strokeWidth="1" />
          );
        })}

        {/* Elevation area + outline */}
        <g transform={`translate(${ML},${MT})`}>
          {pgm.elevAreaPath && (
            <>
              <path d={pgm.elevAreaPath} fill="rgba(107,200,155,0.08)" clipPath="url(#elev-future)" />
              <path d={pgm.elevAreaPath} fill="rgba(107,200,155,0.18)" clipPath="url(#elev-done)" />
            </>
          )}
          {pgm.elevOutline && (
            <>
              <path d={pgm.elevOutline} fill="none" stroke="rgba(107,200,155,0.18)" strokeWidth="1.5"
                    strokeLinejoin="round" strokeLinecap="round" clipPath="url(#elev-future)" />
              <path d={pgm.elevOutline} fill="none" stroke="rgba(107,200,155,0.5)" strokeWidth="1.5"
                    strokeLinejoin="round" strokeLinecap="round" clipPath="url(#elev-done)" />
            </>
          )}
          {/* Step indicator dots */}
          {pgm.intervalBoundaryXs.length > 2 && (() => {
            const trackY = H - 2;
            return (
              <>
                {/* Track line */}
                <line x1={0} y1={trackY} x2={W} y2={trackY}
                  stroke="rgba(232,228,223,0.1)" strokeWidth="1" />
                {/* Dots at interval boundaries (skip last = end) */}
                {pgm.intervalBoundaryXs.slice(0, -1).map((bx, i) => {
                  const isCompleted = i < pgm.currentInterval;
                  const isCurrent = i === pgm.currentInterval;
                  if (isCurrent) {
                    return (
                      <React.Fragment key={i}>
                        <circle cx={bx} cy={trackY} r={6} fill="rgba(107,200,155,0.15)" />
                        <circle cx={bx} cy={trackY} r={3.5} fill="rgba(107,200,155,1)" />
                      </React.Fragment>
                    );
                  }
                  if (isCompleted) {
                    return <circle key={i} cx={bx} cy={trackY} r={2.5} fill="rgba(107,200,155,0.8)" />;
                  }
                  return <circle key={i} cx={bx} cy={trackY} r={2.5}
                    fill="none" stroke="rgba(232,228,223,0.3)" strokeWidth="1" />;
                })}
              </>
            );
          })()}
        </g>
      </svg>

      {/* HTML overlays — don't stretch */}

      {/* Y-axis incline labels */}
      {inclineTicks.map(inc => {
        const pctY = (inclineY(inc, yMax) / VB_H) * 100;
        return (
          <div key={inc} style={{
            ...labelStyle,
            left: 0,
            width: `${((ML - TICK - 1) / VB_W) * 100}%`,
            top: `${pctY}%`,
            transform: 'translateY(-50%)',
            textAlign: 'right',
          }}>
            {inc}%
          </div>
        );
      })}

      {/* X-axis time labels */}
      {timeTicks.map(({ sec, label }) => {
        const svgX = ML + (sec / pgm.totalDuration) * W;
        const pctX = (svgX / VB_W) * 100;
        const pctTop = ((MT + H + TICK + 1) / VB_H) * 100;
        return (
          <div key={label} style={{
            ...labelStyle,
            left: `${pctX}%`,
            top: `${pctTop}%`,
            transform: 'translateX(-50%)',
          }}>
            {label}
          </div>
        );
      })}

      {/* Double-tap arrow indicator */}
      {arrow && (
        <div key={Date.now()} style={{
          ...arrowStyle,
          ...(arrow === 'right' ? { right: '25%' } : { left: '25%' }),
        }}>
          {arrow === 'right' ? '\u00bb' : '\u00ab'}
        </div>
      )}

      {/* Position dot */}
      <div style={{
        position: 'absolute',
        left: `${dotLeft}%`,
        top: `${dotTop}%`,
        width: 10, height: 10,
        borderRadius: '50%',
        background: 'var(--green)',
        border: '1.5px solid var(--text)',
        transform: 'translate(-50%, -50%)',
        transition: 'left 1s linear, top 1s linear',
        boxShadow: '0 0 6px rgba(232,228,223,0.6), 0 0 10px rgba(107,200,155,0.3)',
        pointerEvents: 'none',
        zIndex: 1,
      }} />
    </div>
  );
});

export default ElevationProfile;
