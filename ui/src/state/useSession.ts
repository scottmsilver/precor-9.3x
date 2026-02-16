import { useMemo } from 'react';
import { useTreadmillState } from './TreadmillContext';
import { fmtDur, paceDisplay } from '../utils/formatters';

export function useSession() {
  const { session, status } = useTreadmillState();

  return useMemo(() => {
    const speedMph = status.emulate
      ? status.emuSpeed / 10
      : (status.speed ?? 0);

    return {
      active: session.active,
      elapsed: session.elapsed,
      elapsedDisplay: fmtDur(session.elapsed),
      distance: session.distance,
      distDisplay: session.distance.toFixed(2),
      vertFeet: session.vertFeet,
      vertDisplay: Math.round(session.vertFeet).toLocaleString(),
      pace: paceDisplay(speedMph),
      speedMph,
      endReason: session.endReason,
    };
  }, [session, status.emulate, status.emuSpeed, status.speed]);
}
