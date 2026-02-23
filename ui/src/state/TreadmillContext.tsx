import React, { createContext, useContext, useReducer, useEffect, useRef, useCallback } from 'react';
import type { AppState, KVEntry, ServerMessage, TreadmillStatus, SessionState, ProgramState } from './types';
import * as api from './api';

// --- Debounce helpers ---

/** Trailing debounce: coalesces rapid calls, fires once after `ms` of quiet. */
function trailingDebounce<T extends (...args: never[]) => void>(fn: T, ms: number): T {
  let timer: ReturnType<typeof setTimeout>;
  return ((...args: Parameters<T>) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  }) as unknown as T;
}

/** Leading-edge guard: fires immediately, then ignores calls for `ms`. */
function leadingGuard<T extends (...args: never[]) => Promise<void>>(fn: T, ms: number): T {
  let blocked = false;
  return (async (...args: Parameters<T>) => {
    if (blocked) return;
    blocked = true;
    setTimeout(() => { blocked = false; }, ms);
    await fn(...args);
  }) as unknown as T;
}

// --- Initial state ---

const initialStatus: TreadmillStatus = {
  proxy: true,
  emulate: false,
  emuSpeed: 0,
  emuIncline: 0,
  speed: null,
  incline: null,
  motor: {},
  treadmillConnected: false,
  heartRate: 0,
  hrmConnected: false,
};

const initialSession: SessionState = {
  active: false,
  elapsed: 0,
  distance: 0,
  vertFeet: 0,
  wallStartedAt: '',
  endReason: null,
};

const initialProgram: ProgramState = {
  program: null,
  running: false,
  paused: false,
  completed: false,
  currentInterval: 0,
  intervalElapsed: 0,
  totalElapsed: 0,
  totalDuration: 0,
};

const initialState: AppState = {
  wsConnected: false,
  status: initialStatus,
  session: initialSession,
  program: initialProgram,
  kvLog: [],
  hrmDevices: [],
  _dirtySpeed: 0,
  _dirtyIncline: 0,
};

// --- Actions ---

type Action =
  | { type: 'WS_CONNECTED' }
  | { type: 'WS_DISCONNECTED' }
  | { type: 'STATUS_UPDATE'; payload: ServerMessage & { type: 'status' } }
  | { type: 'SESSION_UPDATE'; payload: ServerMessage & { type: 'session' } }
  | { type: 'PROGRAM_UPDATE'; payload: ServerMessage & { type: 'program' } }
  | { type: 'CONNECTION_UPDATE'; payload: ServerMessage & { type: 'connection' } }
  | { type: 'KV_UPDATE'; payload: ServerMessage & { type: 'kv' } }
  | { type: 'OPTIMISTIC_SPEED'; payload: number }
  | { type: 'OPTIMISTIC_INCLINE'; payload: number }
  | { type: 'HR_UPDATE'; payload: ServerMessage & { type: 'hr' } }
  | { type: 'SCAN_RESULT'; payload: ServerMessage & { type: 'scan_result' } };

const MAX_KV_LOG = 500;

// Optimistic updates set a dirty timestamp. While dirty, server status
// updates are ignored for that field so they don't snap back to stale values.
const DIRTY_GRACE_MS = 500;

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'WS_CONNECTED':
      return { ...state, wsConnected: true };

    case 'WS_DISCONNECTED':
      return { ...state, wsConnected: false, hrmDevices: [] };

    case 'STATUS_UPDATE': {
      const m = action.payload;
      const now = Date.now();
      const speedDirty = now - state._dirtySpeed < DIRTY_GRACE_MS;
      const inclineDirty = now - state._dirtyIncline < DIRTY_GRACE_MS;
      return {
        ...state,
        status: {
          proxy: m.proxy,
          emulate: m.emulate,
          emuSpeed: speedDirty ? state.status.emuSpeed : (m.emu_speed ?? state.status.emuSpeed),
          emuIncline: inclineDirty ? state.status.emuIncline : (m.emu_incline ?? state.status.emuIncline),
          speed: m.speed ?? state.status.speed,
          incline: m.incline ?? state.status.incline,
          motor: m.motor ?? state.status.motor,
          treadmillConnected: m.treadmill_connected ?? state.status.treadmillConnected,
          heartRate: m.heart_rate ?? state.status.heartRate,
          hrmConnected: m.hrm_connected ?? state.status.hrmConnected,
        },
      };
    }

    case 'SESSION_UPDATE': {
      const m = action.payload;
      return {
        ...state,
        session: {
          active: m.active,
          elapsed: m.elapsed || 0,
          distance: m.distance || 0,
          vertFeet: m.vert_feet || 0,
          wallStartedAt: m.wall_started_at || '',
          endReason: m.end_reason,
        },
      };
    }

    case 'PROGRAM_UPDATE': {
      const m = action.payload;
      return {
        ...state,
        program: {
          program: m.program,
          running: m.running,
          paused: m.paused,
          completed: m.completed,
          currentInterval: m.current_interval,
          intervalElapsed: m.interval_elapsed,
          totalElapsed: m.total_elapsed,
          totalDuration: m.total_duration,
        },
      };
    }

    case 'CONNECTION_UPDATE': {
      const m = action.payload;
      return {
        ...state,
        status: {
          ...state.status,
          treadmillConnected: m.connected,
        },
      };
    }

    case 'KV_UPDATE': {
      const m = action.payload;
      const entry: KVEntry = {
        ts: m.ts != null ? m.ts.toFixed(2) : '',
        src: m.source,
        key: m.key,
        value: m.value,
      };
      const newLog = [...state.kvLog, entry];
      if (newLog.length > MAX_KV_LOG) {
        newLog.splice(0, 100);
      }
      const motor = m.source === 'motor'
        ? { ...state.status.motor, [m.key]: m.value }
        : state.status.motor;
      return {
        ...state,
        kvLog: newLog,
        status: { ...state.status, motor },
      };
    }

    case 'HR_UPDATE': {
      const m = action.payload;
      return {
        ...state,
        status: {
          ...state.status,
          heartRate: m.bpm,
          hrmConnected: m.connected,
        },
      };
    }

    case 'SCAN_RESULT': {
      const m = action.payload;
      return { ...state, hrmDevices: m.devices };
    }

    case 'OPTIMISTIC_SPEED':
      return {
        ...state,
        _dirtySpeed: Date.now(),
        status: { ...state.status, emuSpeed: action.payload },
      };

    case 'OPTIMISTIC_INCLINE':
      return {
        ...state,
        _dirtyIncline: Date.now(),
        status: { ...state.status, emuIncline: action.payload },
      };

    default:
      return state;
  }
}

// --- Contexts ---

const TreadmillStateContext = createContext<AppState>(initialState);

interface TreadmillActions {
  setSpeed: (mph: number) => Promise<void>;
  setIncline: (value: number) => Promise<void>;
  adjustSpeed: (deltaTenths: number) => void;
  adjustIncline: (delta: number) => void;
  emergencyStop: () => Promise<void>;
  resetAll: () => Promise<void>;
  startProgram: () => Promise<void>;
  stopProgram: () => Promise<void>;
  pauseProgram: () => Promise<void>;
  skipInterval: () => Promise<void>;
  prevInterval: () => Promise<void>;
  extendInterval: (seconds: number) => Promise<void>;
  setMode: (mode: 'proxy' | 'emulate') => Promise<void>;
}

const TreadmillActionsContext = createContext<TreadmillActions>(null!);

// --- Toast context (for encouragement messages, session end, etc.) ---

type ToastFn = (message: string) => void;
const ToastContext = createContext<ToastFn>(() => {});

// Module-level toast ref — set by App.tsx via registerToast()
let _toastFn: ToastFn = () => {};
export function registerToast(fn: ToastFn) { _toastFn = fn; }

// --- Encouragement callback (bounce animation in Running.tsx) ---
type EncouragementFn = (message: string) => void;
let _encouragementFn: EncouragementFn = () => {};
export function registerEncouragement(fn: EncouragementFn) { _encouragementFn = fn; }

// --- Provider ---

export function TreadmillProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout>>();

  // Use module-level toast function (set by App.tsx via registerToast)
  const showToast = useCallback((msg: string) => {
    _toastFn(msg);
  }, []);

  // WebSocket connection
  useEffect(() => {
    function connect() {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(`${proto}//${window.location.host}/ws`);
      wsRef.current = ws;

      ws.onopen = () => {
        dispatch({ type: 'WS_CONNECTED' });
        // Fetch initial program state
        api.getProgram().then(d => {
          if (d.program) {
            dispatch({ type: 'PROGRAM_UPDATE', payload: d as ServerMessage & { type: 'program' } });
          }
        }).catch(() => {});
      };

      ws.onclose = () => {
        dispatch({ type: 'WS_DISCONNECTED' });
        reconnectRef.current = setTimeout(connect, 2000);
      };

      ws.onerror = () => {
        ws.close();
      };

      ws.onmessage = (evt) => {
        const msg: ServerMessage = JSON.parse(evt.data);
        switch (msg.type) {
          case 'status':
            dispatch({ type: 'STATUS_UPDATE', payload: msg });
            break;
          case 'session':
            dispatch({ type: 'SESSION_UPDATE', payload: msg });
            if (!msg.active && msg.end_reason) {
              if (msg.end_reason === 'watchdog') showToast('Belt stopped — heartbeat lost');
              else if (msg.end_reason === 'auto_proxy') showToast('Belt stopped — console took over');
              else if (msg.end_reason === 'disconnect') showToast('Belt stopped — treadmill disconnected');
            }
            break;
          case 'program':
            dispatch({ type: 'PROGRAM_UPDATE', payload: msg });
            if (msg.encouragement) _encouragementFn(msg.encouragement);
            break;
          case 'connection':
            dispatch({ type: 'CONNECTION_UPDATE', payload: msg });
            break;
          case 'kv':
            dispatch({ type: 'KV_UPDATE', payload: msg });
            break;
          case 'hr':
            dispatch({ type: 'HR_UPDATE', payload: msg });
            break;
          case 'scan_result':
            dispatch({ type: 'SCAN_RESULT', payload: msg });
            break;
        }
      };
    }

    connect();
    return () => {
      clearTimeout(reconnectRef.current);
      wsRef.current?.close();
    };
  }, [showToast]);

  // State ref for stable action closures
  const stateRef = useRef(state);
  stateRef.current = state;

  // Debounced API calls for speed/incline — coalesce rapid hold-repeat into
  // fewer network requests while keeping optimistic UI updates instant.
  const debouncedSetSpeed = useRef(trailingDebounce((mph: number) => {
    api.setSpeed(mph).catch(() => {});
  }, 150)).current;

  const debouncedSetIncline = useRef(trailingDebounce((inc: number) => {
    api.setIncline(inc).catch(() => {});
  }, 150)).current;

  // Stable action refs — never change identity
  const stableActions = useRef<TreadmillActions>({
    setSpeed: async (mph) => {
      dispatch({ type: 'OPTIMISTIC_SPEED', payload: Math.max(0, Math.min(Math.round(mph * 10), 120)) });
      debouncedSetSpeed(mph);
    },
    setIncline: async (value) => {
      dispatch({ type: 'OPTIMISTIC_INCLINE', payload: Math.max(0, Math.min(value, 99)) });
      debouncedSetIncline(value);
    },
    adjustSpeed: (deltaTenths: number) => {
      const cur = stateRef.current.status.emuSpeed;
      const newSpeed = Math.max(0, Math.min(cur + deltaTenths, 120));
      dispatch({ type: 'OPTIMISTIC_SPEED', payload: newSpeed });
      debouncedSetSpeed(newSpeed / 10);
    },
    adjustIncline: (delta: number) => {
      const cur = stateRef.current.status.emuIncline;
      const newInc = Math.max(0, Math.min(Math.round((cur + delta) * 2) / 2, 99));
      dispatch({ type: 'OPTIMISTIC_INCLINE', payload: newInc });
      debouncedSetIncline(newInc);
    },
    emergencyStop: async () => {
      // Emergency stop is never debounced — always fires immediately
      dispatch({ type: 'OPTIMISTIC_SPEED', payload: 0 });
      dispatch({ type: 'OPTIMISTIC_INCLINE', payload: 0 });
      await Promise.all([
        api.setSpeed(0).catch(() => {}),
        api.setIncline(0).catch(() => {}),
        api.stopProgram().catch(() => {}),
      ]);
    },
    resetAll: leadingGuard(async () => {
      dispatch({ type: 'OPTIMISTIC_SPEED', payload: 0 });
      dispatch({ type: 'OPTIMISTIC_INCLINE', payload: 0 });
      await api.resetAll().catch(() => {});
    }, 1000),
    startProgram: leadingGuard(async () => {
      await api.startProgram().catch(() => {});
    }, 1000),
    stopProgram: leadingGuard(async () => {
      await api.stopProgram().catch(() => {});
    }, 1000),
    pauseProgram: leadingGuard(async () => {
      await api.pauseProgram().catch(() => {});
    }, 500),
    skipInterval: leadingGuard(async () => {
      await api.skipInterval().catch(() => {});
    }, 400),
    prevInterval: leadingGuard(async () => {
      await api.prevInterval().catch(() => {});
    }, 400),
    extendInterval: leadingGuard(async (seconds) => {
      await api.extendInterval(seconds).catch(() => {});
    }, 400),
    setMode: leadingGuard(async (mode) => {
      if (mode === 'emulate') await api.setEmulate(true).catch(() => {});
      else await api.setProxy(true).catch(() => {});
    }, 1000),
  }).current;

  return (
    <TreadmillStateContext.Provider value={state}>
      <TreadmillActionsContext.Provider value={stableActions}>
        {children}
      </TreadmillActionsContext.Provider>
    </TreadmillStateContext.Provider>
  );
}

// --- Hooks ---

export function useTreadmillState(): AppState {
  return useContext(TreadmillStateContext);
}

export function useTreadmillActions(): TreadmillActions {
  return useContext(TreadmillActionsContext);
}

export function useToast(): ToastFn {
  return useContext(ToastContext);
}

// Re-export for external toast registration
export { ToastContext };
