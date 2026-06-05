import type { NormRect } from './sample';
import type { Role } from './types';

export interface Block { id: string; role: Role; rect: NormRect; label: string; }

// Relative positions mirroring RunningScreen.kt (timer top-center, metric tiles row, button row).
export const RUNNING_BLOCKS: Block[] = [
  { id: 'timer', role: 'hero', rect: { x: 0.30, y: 0.06, w: 0.40, h: 0.18 }, label: '24:18' },
  { id: 'speed', role: 'body', rect: { x: 0.08, y: 0.30, w: 0.26, h: 0.12 }, label: '6.2 mph' },
  { id: 'incline', role: 'body', rect: { x: 0.37, y: 0.30, w: 0.26, h: 0.12 }, label: '5.0%' },
  { id: 'distance', role: 'body', rect: { x: 0.66, y: 0.30, w: 0.26, h: 0.12 }, label: '1.4 mi' },
  { id: 'hint', role: 'muted', rect: { x: 0.30, y: 0.84, w: 0.40, h: 0.08 }, label: 'tap to pause' },
];
