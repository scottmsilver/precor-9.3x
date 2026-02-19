import type { ChatResponse, HistoryEntry, StatusMessage, ProgramMessage, AppConfig } from './types';

function apiBase(): string {
  return '';  // same origin; Vite proxy handles in dev
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (res.status === 503) {
    throw new Error('treadmill_io disconnected');
  }
  return res.json();
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`);
  return res.json();
}

// --- Status & Control ---

export async function setSpeed(mph: number): Promise<StatusMessage> {
  return post('/api/speed', { value: mph });
}

export async function setIncline(value: number): Promise<StatusMessage> {
  return post('/api/incline', { value });
}

export async function setEmulate(enabled: boolean): Promise<StatusMessage> {
  return post('/api/emulate', { enabled });
}

export async function setProxy(enabled: boolean): Promise<StatusMessage> {
  return post('/api/proxy', { enabled });
}

export async function getStatus(): Promise<StatusMessage> {
  return get('/api/status');
}

// --- Programs ---

export async function getProgram(): Promise<ProgramMessage> {
  return get('/api/program');
}

export async function generateProgram(prompt: string): Promise<{ ok: boolean; program?: unknown; error?: string }> {
  return post('/api/program/generate', { prompt });
}

export async function startProgram(): Promise<ProgramMessage> {
  return post('/api/program/start', {});
}

export async function quickStart(speed = 3.0, incline = 0, durationMinutes = 60): Promise<{ ok: boolean }> {
  return post('/api/program/quick-start', { speed, incline, duration_minutes: durationMinutes });
}

export async function adjustDuration(deltaSeconds: number): Promise<ProgramMessage> {
  return post('/api/program/adjust-duration', { delta_seconds: deltaSeconds });
}

export async function stopProgram(): Promise<ProgramMessage> {
  return post('/api/program/stop', {});
}

export async function resetAll(): Promise<{ ok: boolean }> {
  return post('/api/reset', {});
}

export async function pauseProgram(): Promise<ProgramMessage> {
  return post('/api/program/pause', {});
}

export async function skipInterval(): Promise<ProgramMessage> {
  return post('/api/program/skip', {});
}

export async function prevInterval(): Promise<ProgramMessage> {
  return post('/api/program/prev', {});
}

export async function extendInterval(seconds: number): Promise<ProgramMessage> {
  return post('/api/program/extend', { seconds });
}

// --- History ---

export async function getHistory(): Promise<HistoryEntry[]> {
  return get('/api/programs/history');
}

export async function loadFromHistory(id: string): Promise<{ ok: boolean; program?: unknown; error?: string }> {
  return post(`/api/programs/history/${id}/load`, {});
}

// --- GPX ---

export async function uploadGpx(file: File): Promise<{ ok: boolean; program?: unknown; error?: string }> {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${apiBase()}/api/gpx/upload`, { method: 'POST', body: form });
  return res.json();
}

// --- Chat ---

function isSmartassMode(): boolean {
  try { return localStorage.getItem('smartass_mode') === 'true'; } catch { return false; }
}

export async function sendChat(message: string): Promise<ChatResponse> {
  return post('/api/chat', { message, smartass: isSmartassMode() });
}

export async function sendVoiceChat(audio: string, mimeType: string): Promise<ChatResponse> {
  return post('/api/chat/voice', { audio, mime_type: mimeType, smartass: isSmartassMode() });
}

// --- Voice intent extraction ---

export async function extractIntent(text: string, alreadyExecuted: string[] = []): Promise<{ actions: Action[]; text: string }> {
  return post('/api/voice/extract-intent', { text, already_executed: alreadyExecuted });
}

interface Action {
  name: string;
  args: Record<string, unknown>;
  result?: string;
}

// --- TTS ---

export async function requestTts(text: string, voice = 'Kore'): Promise<{ ok: boolean; audio?: string; sample_rate?: number; error?: string }> {
  return post('/api/tts', { text, voice });
}

// --- Log ---

export async function getLog(lines = 200): Promise<{ lines: string[] }> {
  return get(`/api/log?lines=${lines}`);
}

// --- Config ---

export async function getConfig(): Promise<AppConfig> {
  return get('/api/config');
}

// --- Voice prompts ---

export async function getVoicePrompt(id: string): Promise<string> {
  const res = await get<{ prompt: string }>(`/api/voice/prompt/${id}`);
  return res.prompt;
}
