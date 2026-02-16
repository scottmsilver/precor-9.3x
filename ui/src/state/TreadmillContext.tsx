import React, { createContext, useContext, useReducer, useEffect, useRef, useCallback } from 'react';
import type { AppState, KVEntry, ServerMessage, TreadmillStatus, SessionState, ProgramState } from './types';
import * as api from './api';

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
  | { type: 'OPTIMISTIC_INCLINE'; payload: number };

const MAX_KV_LOG = 500;

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'WS_CONNECTED':
      return { ...state, wsConnected: true };

    case 'WS_DISCONNECTED':
      return { ...state, wsConnected: false };

    case 'STATUS_UPDATE': {
      const m = action.payload;
      return {
        ...state,
        status: {
          proxy: m.proxy,
          emulate: m.emulate,
          emuSpeed: m.emu_speed ?? state.status.emuSpeed,
          emuIncline: m.emu_incline ?? state.status.emuIncline,
          speed: m.speed ?? state.status.speed,
          incline: m.incline ?? state.status.incline,
          motor: m.motor ?? state.status.motor,
          treadmillConnected: m.treadmill_connected ?? state.status.treadmillConnected,
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

    case 'OPTIMISTIC_SPEED':
      return {
        ...state,
        status: { ...state.status, emuSpeed: action.payload },
      };

    case 'OPTIMISTIC_INCLINE':
      return {
        ...state,
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

// --- Provider ---

export function TreadmillProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout>>();
  const toastRef = useRef<ToastFn>(() => {});

  // Expose toast setter
  const setToastFn = useCallback((fn: ToastFn) => {
    toastRef.current = fn;
  }, []);

  const showToast = useCallback((msg: string) => {
    toastRef.current(msg);
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
            if (msg.encouragement) showToast(msg.encouragement);
            break;
          case 'connection':
            dispatch({ type: 'CONNECTION_UPDATE', payload: msg });
            break;
          case 'kv':
            dispatch({ type: 'KV_UPDATE', payload: msg });
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

  // Stable action refs — never change identity
  const stableActions = useRef<TreadmillActions>({
    setSpeed: async (mph) => {
      dispatch({ type: 'OPTIMISTIC_SPEED', payload: Math.max(0, Math.min(Math.round(mph * 10), 120)) });
      await api.setSpeed(mph).catch(() => {});
    },
    setIncline: async (value) => {
      dispatch({ type: 'OPTIMISTIC_INCLINE', payload: Math.max(0, Math.min(value, 99)) });
      await api.setIncline(value).catch(() => {});
    },
    adjustSpeed: (deltaTenths: number) => {
      const cur = stateRef.current.status.emuSpeed;
      const newSpeed = Math.max(0, Math.min(cur + deltaTenths, 120));
      dispatch({ type: 'OPTIMISTIC_SPEED', payload: newSpeed });
      api.setSpeed(newSpeed / 10).catch(() => {});
    },
    adjustIncline: (delta: number) => {
      const cur = stateRef.current.status.emuIncline;
      const newInc = Math.max(0, Math.min(cur + delta, 99));
      dispatch({ type: 'OPTIMISTIC_INCLINE', payload: newInc });
      api.setIncline(newInc).catch(() => {});
    },
    emergencyStop: async () => {
      dispatch({ type: 'OPTIMISTIC_SPEED', payload: 0 });
      dispatch({ type: 'OPTIMISTIC_INCLINE', payload: 0 });
      await Promise.all([
        api.setSpeed(0).catch(() => {}),
        api.setIncline(0).catch(() => {}),
        api.stopProgram().catch(() => {}),
      ]);
    },
    resetAll: async () => {
      dispatch({ type: 'OPTIMISTIC_SPEED', payload: 0 });
      dispatch({ type: 'OPTIMISTIC_INCLINE', payload: 0 });
      await api.resetAll().catch(() => {});
    },
    startProgram: async () => {
      await api.startProgram().catch(() => {});
    },
    stopProgram: async () => {
      await api.stopProgram().catch(() => {});
    },
    pauseProgram: async () => {
      await api.pauseProgram().catch(() => {});
    },
    skipInterval: async () => {
      await api.skipInterval().catch(() => {});
    },
    prevInterval: async () => {
      await api.prevInterval().catch(() => {});
    },
    extendInterval: async (seconds) => {
      await api.extendInterval(seconds).catch(() => {});
    },
    setMode: async (mode) => {
      if (mode === 'emulate') await api.setEmulate(true).catch(() => {});
      else await api.setProxy(true).catch(() => {});
    },
  }).current;

  return (
    <TreadmillStateContext.Provider value={state}>
      <TreadmillActionsContext.Provider value={stableActions}>
        <ToastContext.Provider value={showToast}>
          <ToastRegistrar setToastFn={setToastFn} />
          {children}
        </ToastContext.Provider>
      </TreadmillActionsContext.Provider>
    </TreadmillStateContext.Provider>
  );
}

// Internal component to wire the toast function
function ToastRegistrar({ setToastFn }: { setToastFn: (fn: ToastFn) => void }) {
  const showToast = useContext(ToastContext);
  useEffect(() => {
    // This will be called by the parent, but we need the child context toast
    // Actually, the toast fn needs to be set externally by the App shell
  }, [showToast, setToastFn]);
  return null;
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
