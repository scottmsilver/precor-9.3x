// --- Server message types ---

export interface KVMessage {
  type: 'kv';
  source: 'motor' | 'console' | 'emulate';
  key: string;
  value: string;
  ts?: number;
}

export interface StatusMessage {
  type: 'status';
  proxy: boolean;
  emulate: boolean;
  emu_speed: number;
  emu_speed_mph: number;
  emu_incline: number;
  speed: number | null;
  incline: number | null;
  motor: Record<string, string>;
  treadmill_connected: boolean;
}

export interface SessionMessage {
  type: 'session';
  active: boolean;
  elapsed: number;
  distance: number;
  vert_feet: number;
  wall_started_at: string;
  end_reason: 'user_stop' | 'watchdog' | 'auto_proxy' | 'disconnect' | null;
}

export interface Interval {
  name: string;
  duration: number;
  speed: number;
  incline: number;
}

export interface Program {
  name: string;
  manual?: boolean;
  intervals: Interval[];
}

export interface ProgramMessage {
  type: 'program';
  program: Program | null;
  running: boolean;
  paused: boolean;
  completed: boolean;
  current_interval: number;
  interval_elapsed: number;
  total_elapsed: number;
  total_duration: number;
  encouragement?: string;
}

export interface ConnectionMessage {
  type: 'connection';
  connected: boolean;
}

export type ServerMessage = KVMessage | StatusMessage | SessionMessage | ProgramMessage | ConnectionMessage;

// --- Client state ---

export interface TreadmillStatus {
  proxy: boolean;
  emulate: boolean;
  emuSpeed: number;       // tenths of mph
  emuIncline: number;
  speed: number | null;   // live motor speed mph
  incline: number | null;  // live motor incline
  motor: Record<string, string>;
  treadmillConnected: boolean;
}

export interface SessionState {
  active: boolean;
  elapsed: number;
  distance: number;
  vertFeet: number;
  wallStartedAt: string;
  endReason: string | null;
}

export interface ProgramState {
  program: Program | null;
  running: boolean;
  paused: boolean;
  completed: boolean;
  currentInterval: number;
  intervalElapsed: number;
  totalElapsed: number;
  totalDuration: number;
}

export interface AppState {
  wsConnected: boolean;
  status: TreadmillStatus;
  session: SessionState;
  program: ProgramState;
  kvLog: KVEntry[];
}

export interface KVEntry {
  ts: string;
  src: string;
  key: string;
  value: string;
}

// --- History ---

export interface HistoryEntry {
  id: string;
  prompt: string;
  program: Program;
  created_at: string;
  total_duration: number;
}

// --- Chat ---

export interface ChatResponse {
  text: string;
  actions: Array<{ name: string; args: Record<string, unknown>; result: string }>;
  transcription?: string;
}

// --- Config ---

export interface AppConfig {
  gemini_api_key: string;
  gemini_model: string;
  gemini_live_model: string;
  gemini_voice: string;
}
