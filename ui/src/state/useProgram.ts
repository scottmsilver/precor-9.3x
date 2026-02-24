import { useMemo } from 'react';
import { useTreadmillState } from './TreadmillContext';
import type { Interval } from './types';

const W = 400, H = 140, PAD = 10;

/** Fritsch-Carlson monotone cubic interpolation — never overshoots */
function monotoneCubicPaths(
  points: { x: number; y: number }[],
): { outline: string; area: string } {
  const n = points.length;
  if (n === 0) return { outline: '', area: '' };
  if (n === 1) {
    const p = points[0];
    const outline = `M0,${p.y} L${W},${p.y}`;
    return { outline, area: outline + ` L${W},${H} L0,${H} Z` };
  }

  // 1. Compute slopes between successive points
  const dx: number[] = [];
  const dy: number[] = [];
  const m: number[] = []; // secant slopes
  for (let i = 0; i < n - 1; i++) {
    dx.push(points[i + 1].x - points[i].x);
    dy.push(points[i + 1].y - points[i].y);
    m.push(dx[i] === 0 ? 0 : dy[i] / dx[i]);
  }

  // 2. Compute tangents using Fritsch-Carlson
  const tangents: number[] = new Array(n);
  tangents[0] = m[0];
  tangents[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) {
    if (m[i - 1] * m[i] <= 0) {
      // Sign change or zero — flat tangent (monotonicity constraint)
      tangents[i] = 0;
    } else {
      // Harmonic mean of adjacent slopes
      tangents[i] = (m[i - 1] + m[i]) / 2;
    }
  }

  // 3. Fritsch-Carlson: clamp tangents to ensure monotonicity
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) {
      tangents[i] = 0;
      tangents[i + 1] = 0;
    } else {
      const alpha = tangents[i] / m[i];
      const beta = tangents[i + 1] / m[i];
      // Restrict to circle of radius 3 for monotonicity
      const s = alpha * alpha + beta * beta;
      if (s > 9) {
        const tau = 3 / Math.sqrt(s);
        tangents[i] = tau * alpha * m[i];
        tangents[i + 1] = tau * beta * m[i];
      }
    }
  }

  // 4. Build SVG cubic bezier commands from Hermite tangents
  // For each segment, convert Hermite (p0, p1, t0, t1) to cubic bezier control points
  let outline = `M0,${points[0].y} L${points[0].x},${points[0].y}`;
  for (let i = 0; i < n - 1; i++) {
    const p0 = points[i];
    const p1 = points[i + 1];
    const d = p1.x - p0.x;
    // Hermite → Bezier control points
    const cp1x = p0.x + d / 3;
    const cp1y = p0.y + (tangents[i] * d) / 3;
    const cp2x = p1.x - d / 3;
    const cp2y = p1.y - (tangents[i + 1] * d) / 3;
    outline += ` C${cp1x},${cp1y} ${cp2x},${cp2y} ${p1.x},${p1.y}`;
  }
  outline += ` L${W},${points[n - 1].y}`;

  const area = outline + ` L${W},${H} L0,${H} Z`;
  return { outline, area };
}

/** Evaluate the monotone cubic spline at a given x position */
function evalSplineY(
  points: { x: number; y: number }[],
  tangents: number[],
  x: number,
): number {
  const n = points.length;
  if (n === 0) return H / 2;
  if (x <= points[0].x) return points[0].y;
  if (x >= points[n - 1].x) return points[n - 1].y;

  // Find segment
  let i = 0;
  for (; i < n - 2; i++) {
    if (x < points[i + 1].x) break;
  }

  const p0 = points[i];
  const p1 = points[i + 1];
  const d = p1.x - p0.x;
  if (d === 0) return p0.y;

  // Hermite basis evaluation
  const t = (x - p0.x) / d;
  const t2 = t * t;
  const t3 = t2 * t;
  const h00 = 2 * t3 - 3 * t2 + 1;
  const h10 = t3 - 2 * t2 + t;
  const h01 = -2 * t3 + 3 * t2;
  const h11 = t3 - t2;

  return h00 * p0.y + h10 * (tangents[i] * d) + h01 * p1.y + h11 * (tangents[i + 1] * d);
}

/** Compute tangents (same algorithm as monotoneCubicPaths, extracted for dot tracking) */
function computeTangents(points: { x: number; y: number }[]): number[] {
  const n = points.length;
  if (n < 2) return new Array(n).fill(0);

  const m: number[] = [];
  const dx: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    const d = points[i + 1].x - points[i].x;
    dx.push(d);
    m.push(d === 0 ? 0 : (points[i + 1].y - points[i].y) / d);
  }

  const tangents: number[] = new Array(n);
  tangents[0] = m[0];
  tangents[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) {
    if (m[i - 1] * m[i] <= 0) {
      tangents[i] = 0;
    } else {
      tangents[i] = (m[i - 1] + m[i]) / 2;
    }
  }

  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) {
      tangents[i] = 0;
      tangents[i + 1] = 0;
    } else {
      const alpha = tangents[i] / m[i];
      const beta = tangents[i + 1] / m[i];
      const s = alpha * alpha + beta * beta;
      if (s > 9) {
        const tau = 3 / Math.sqrt(s);
        tangents[i] = tau * alpha * m[i];
        tangents[i + 1] = tau * beta * m[i];
      }
    }
  }

  return tangents;
}

export function useProgram() {
  const { program: pgm } = useTreadmillState();

  const { program, running, paused, completed, currentInterval, intervalElapsed, totalElapsed, totalDuration } = pgm;
  const intervals = program?.intervals ?? [];

  // Static elevation paths — only recompute when the interval list changes
  const { elevOutline, elevAreaPath, pts, tangents, totalDur, maxIncline } = useMemo(() => {
    const dur = totalDuration || intervals.reduce((s, iv) => s + iv.duration, 0);

    // Build data points: one point at each interval midpoint
    let x = 0;
    const points: { x: number; y: number }[] = [];
    let maxInc = 0;
    if (dur) {
      for (const iv of intervals) {
        if (iv.incline > maxInc) maxInc = iv.incline;
      }
    }
    // Autoscale: Y-axis max = smallest nice tick ceiling above maxInc
    const yAxisMax = maxInc <= 0 ? 5 : maxInc <= 5 ? 5 : maxInc <= 10 ? 10 : 15;
    if (dur) {
      for (const iv of intervals) {
        const segW = (iv.duration / dur) * W;
        const midX = x + segW / 2;
        const y = H - PAD - (iv.incline / yAxisMax) * (H - PAD * 2);
        points.push({ x: midX, y });
        x += segW;
      }
    }

    const intervalBoundaryXs: number[] = [];
    if (dur && intervals.length > 0) {
      let cumX = 0;
      for (const iv of intervals) {
        intervalBoundaryXs.push(cumX);
        cumX += (iv.duration / dur) * W;
      }
      intervalBoundaryXs.push(W);
    }

    const t = computeTangents(points);
    const { outline, area } = monotoneCubicPaths(points);

    return { elevOutline: outline, elevAreaPath: area, pts: points, tangents: t, totalDur: dur, maxIncline: maxInc, yAxisMax, intervalBoundaryXs };
  }, [intervals, totalDuration]);

  // Dynamic position — recomputes every tick
  const timelinePos = totalDur ? Math.min(100, (totalElapsed / totalDur) * 100) : 0;
  const elevPosX = Math.min(W, (timelinePos / 100) * W);

  // Evaluate spline at current X for dot tracking
  const elevPosY = pts.length ? evalSplineY(pts, tangents, elevPosX) : H / 2;

  const currentIv: Interval | null =
    program && currentInterval < intervals.length ? intervals[currentInterval] : null;
  const nextIv: Interval | null =
    program && currentInterval + 1 < intervals.length ? intervals[currentInterval + 1] : null;
  const ivRemaining = currentIv ? Math.max(0, currentIv.duration - intervalElapsed) : 0;
  const totalRemaining = Math.max(0, totalDur - totalElapsed);
  const ivPct = currentIv && currentIv.duration ? Math.min(100, (intervalElapsed / currentIv.duration) * 100) : 0;

  return {
    program,
    running,
    paused,
    completed,
    currentInterval,
    intervalElapsed,
    totalElapsed,
    totalDuration: totalDur,
    currentIv,
    nextIv,
    ivRemaining,
    totalRemaining,
    ivPct,
    timelinePos,
    elevOutline,
    elevAreaPath,
    elevPosX,
    elevPosY,
    intervalCount: intervals.length,
    maxIncline,
    yAxisMax,
    intervalBoundaryXs,
  };
}
