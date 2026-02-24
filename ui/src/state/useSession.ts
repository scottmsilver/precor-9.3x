import { useMemo, useState, useEffect, useRef } from 'react';
import { useTreadmillState } from './TreadmillContext';
import { fmtDur, paceDisplay } from '../utils/formatters';

export function useSession() {
  const { session, status, program } = useTreadmillState();

  // --- Local clock interpolation ---
  // Server sends elapsed at 1Hz. We interpolate locally at ~10Hz
  // so the timer counts smoothly without visible 1-second jumps.
  const serverElapsed = session.elapsed;
  const serverTimestamp = useRef(Date.now());
  const prevServerElapsed = useRef(serverElapsed);
  const [localElapsed, setLocalElapsed] = useState(serverElapsed);
  const tickRef = useRef<ReturnType<typeof setInterval>>();

  // When server sends a new elapsed value, update the anchor
  useEffect(() => {
    if (serverElapsed !== prevServerElapsed.current) {
      prevServerElapsed.current = serverElapsed;
      serverTimestamp.current = Date.now();
      setLocalElapsed(serverElapsed);
    }
  }, [serverElapsed]);

  // Run local ticker when session is active and not paused
  useEffect(() => {
    if (tickRef.current) {
      clearInterval(tickRef.current);
      tickRef.current = undefined;
    }

    if (!session.active || program.paused) {
      setLocalElapsed(serverElapsed);
      return;
    }

    tickRef.current = setInterval(() => {
      const delta = (Date.now() - serverTimestamp.current) / 1000;
      const interpolated = prevServerElapsed.current + delta;
      // Clamp: never go backwards, cap at server + 1.5s tolerance
      const clamped = Math.max(prevServerElapsed.current, Math.min(interpolated, prevServerElapsed.current + 1.5));
      setLocalElapsed(clamped);
    }, 100);

    return () => {
      if (tickRef.current) {
        clearInterval(tickRef.current);
        tickRef.current = undefined;
      }
    };
  }, [session.active, program.paused, serverElapsed]);

  return useMemo(() => {
    const speedMph = status.emulate
      ? status.emuSpeed / 10
      : (status.speed ?? 0);

    return {
      active: session.active,
      elapsed: session.elapsed,
      elapsedDisplay: fmtDur(localElapsed),
      distance: session.distance,
      distDisplay: session.distance.toFixed(2),
      vertFeet: session.vertFeet,
      vertDisplay: Math.round(session.vertFeet).toLocaleString(),
      pace: paceDisplay(speedMph),
      speedMph,
      endReason: session.endReason,
    };
  }, [session, status.emulate, status.emuSpeed, status.speed, localElapsed]);
}
