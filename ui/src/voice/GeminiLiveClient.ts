/**
 * WebSocket client for Gemini Live (BidiGenerateContentConstrained) API.
 *
 * Manages the bidirectional streaming connection:
 * - Sends setup message with model config, tools, system prompt
 * - Streams mic audio as base64 PCM chunks
 * - Receives audio responses and tool calls
 * - Handles barge-in (interruption)
 */
import { TOOL_DECLARATIONS, VOICE_SYSTEM_PROMPT, VOICE_SMARTASS_ADDENDUM } from './voiceTools';
import type { FunctionCall } from './functionBridge';
import { executeFunctionCall } from './functionBridge';

export type ClientState = 'disconnected' | 'connecting' | 'connected' | 'error';

export interface GeminiLiveCallbacks {
  onStateChange: (state: ClientState) => void;
  onAudioChunk: (pcmBase64: string) => void;
  onSpeakingStart: () => void;
  onSpeakingEnd: () => void;
  onInterrupted: () => void;
  onError: (msg: string) => void;
  onTextFallback?: (text: string, executedCalls: string[]) => void;
}

interface SetupMessage {
  setup: {
    model: string;
    system_instruction: { parts: { text: string }[] };
    tools: { function_declarations: typeof TOOL_DECLARATIONS };
    generation_config: {
      speech_config: {
        voice_config: { prebuilt_voice_config: { voice_name: string } };
      };
      response_modalities: string[];
    };
  };
}

const GEMINI_WS_BASE = 'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContentConstrained';

export class GeminiLiveClient {
  private ws: WebSocket | null = null;
  private callbacks: GeminiLiveCallbacks;
  private apiKey: string;
  private model: string;
  private voice: string;
  private stateContext: string;
  private smartass: boolean;
  private _state: ClientState = 'disconnected';
  private setupDone = false;
  private receivingAudio = false;
  private turnCompleteTimeout: ReturnType<typeof setTimeout> | null = null;
  private turnTextParts: string[] = [];
  private turnToolCalls: string[] = [];

  constructor(
    apiKey: string,
    model: string,
    voice: string,
    callbacks: GeminiLiveCallbacks,
    stateContext = '',
    smartass = false,
  ) {
    this.apiKey = apiKey;
    this.model = model;
    this.voice = voice;
    this.callbacks = callbacks;
    this.stateContext = stateContext;
    this.smartass = smartass;
  }

  get state(): ClientState {
    return this._state;
  }

  private setState(s: ClientState): void {
    this._state = s;
    this.callbacks.onStateChange(s);
  }

  /** Update the treadmill state context. Sends to Gemini mid-session if connected. */
  updateStateContext(ctx: string): void {
    if (ctx === this.stateContext) return;
    this.stateContext = ctx;
    this.sendStateUpdate(ctx);
  }

  /** Inject a state update into the live session so Gemini knows about button taps. */
  private sendStateUpdate(ctx: string): void {
    if (!this.ws || !this.setupDone || this.ws.readyState !== WebSocket.OPEN) return;
    const msg = {
      client_content: {
        turns: [{
          role: 'user',
          parts: [{ text: `[State update — do not respond]\n${ctx}` }],
        }],
        turn_complete: true,
      },
    };
    this.ws.send(JSON.stringify(msg));
  }

  connect(): void {
    if (this.ws) return;
    this.setState('connecting');

    const url = `${GEMINI_WS_BASE}?access_token=${this.apiKey}`;
    this.ws = new WebSocket(url);
    this.setupDone = false;

    this.ws.onopen = () => {
      console.log('[Voice] WebSocket connected, sending setup...');
      this.sendSetup();
    };

    this.ws.onmessage = (evt) => {
      this.handleMessage(evt.data);
    };

    this.ws.onerror = (e) => {
      console.error('[Voice] WebSocket error:', e);
      this.callbacks.onError('WebSocket connection error');
      this.cleanup();
      this.setState('error');
    };

    this.ws.onclose = (e) => {
      console.log('[Voice] WebSocket closed:', e.code, e.reason);
      this.cleanup();
      if (this._state !== 'error') {
        this.setState('disconnected');
      }
    };
  }

  disconnect(): void {
    if (this.ws) {
      this.ws.close();
      this.cleanup();
    }
    this.setState('disconnected');
  }

  private cleanup(): void {
    this.ws = null;
    this.setupDone = false;
    this.receivingAudio = false;
    this.turnTextParts = [];
    this.turnToolCalls = [];
    if (this.turnCompleteTimeout) {
      clearTimeout(this.turnCompleteTimeout);
      this.turnCompleteTimeout = null;
    }
  }

  private sendSetup(): void {
    const basePrompt = this.smartass
      ? VOICE_SYSTEM_PROMPT + VOICE_SMARTASS_ADDENDUM
      : VOICE_SYSTEM_PROMPT;
    const systemText = this.stateContext
      ? `${basePrompt}\n\nCurrent treadmill state:\n${this.stateContext}`
      : basePrompt;

    const setup: SetupMessage = {
      setup: {
        model: `models/${this.model}`,
        system_instruction: { parts: [{ text: systemText }] },
        tools: [{ function_declarations: TOOL_DECLARATIONS }],
        generation_config: {
          speech_config: {
            voice_config: { prebuilt_voice_config: { voice_name: this.voice } },
          },
          response_modalities: ['AUDIO'],
        },
      },
    };

    this.ws?.send(JSON.stringify(setup));
  }

  private async handleMessage(raw: string | ArrayBuffer | Blob): Promise<void> {
    let text: string;
    if (raw instanceof Blob) {
      text = await raw.text();
    } else if (raw instanceof ArrayBuffer) {
      text = new TextDecoder().decode(raw);
    } else {
      text = raw;
    }

    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(text);
    } catch {
      return;
    }

    // Setup complete
    if ('setupComplete' in msg || 'setup_complete' in msg) {
      console.log('[Voice] Setup complete, ready for audio');
      this.setupDone = true;
      this.setState('connected');
      return;
    }

    // Tool call cancellation — Gemini cancelled a pending tool call (e.g. after barge-in)
    if ('toolCallCancellation' in msg || 'tool_call_cancellation' in msg) {
      return;
    }

    // Log unrecognized messages for debugging
    if (!('serverContent' in msg || 'server_content' in msg || 'toolCall' in msg || 'tool_call' in msg)) {
      console.log('[Voice] Unhandled message:', JSON.stringify(msg).slice(0, 200));
    }

    // Server content (audio, turn complete, interrupted)
    const serverContent = (msg.serverContent ?? msg.server_content) as Record<string, unknown> | undefined;
    if (serverContent) {
      // Interrupted
      if (serverContent.interrupted === true) {
        this.receivingAudio = false;
        this.callbacks.onInterrupted();
        return;
      }

      // Turn complete
      if (serverContent.turnComplete === true || serverContent.turn_complete === true) {
        // Fire text fallback immediately — don't let audio timing delay it
        const textJoined = this.turnTextParts.join(' ');
        console.log(`[Voice] Turn complete: toolCalls=[${this.turnToolCalls}], text=${textJoined || '(none)'}`);
        if (this.turnTextParts.length > 0) {
          this.callbacks.onTextFallback?.(textJoined, [...this.turnToolCalls]);
        }
        this.turnTextParts = [];
        this.turnToolCalls = [];

        // Small delay to let last audio chunks finish before signaling speaking end
        if (this.turnCompleteTimeout) clearTimeout(this.turnCompleteTimeout);
        this.turnCompleteTimeout = setTimeout(() => {
          if (this.receivingAudio) {
            this.receivingAudio = false;
            this.callbacks.onSpeakingEnd();
          }
        }, 200);
        return;
      }

      // Model turn — audio and text parts
      const modelTurn = (serverContent.modelTurn ?? serverContent.model_turn) as Record<string, unknown> | undefined;
      if (modelTurn?.parts) {
        const parts = modelTurn.parts as Array<Record<string, unknown>>;
        for (const part of parts) {
          // Collect text parts for fallback detection
          if (typeof part.text === 'string' && part.text.trim()) {
            console.log('[Voice] modelTurn text:', part.text);
            this.turnTextParts.push(part.text);
          }
          const inlineData = (part.inlineData ?? part.inline_data) as { mimeType?: string; mime_type?: string; data?: string } | undefined;
          if (inlineData?.data) {
            if (!this.receivingAudio) {
              this.receivingAudio = true;
              this.callbacks.onSpeakingStart();
            }
            if (this.turnCompleteTimeout) {
              clearTimeout(this.turnCompleteTimeout);
              this.turnCompleteTimeout = null;
            }
            this.callbacks.onAudioChunk(inlineData.data);
          }
        }
      }
    }

    // Tool call
    const toolCall = (msg.toolCall ?? msg.tool_call) as { functionCalls?: Array<{ name: string; args: Record<string, unknown> }> } | undefined;
    if (toolCall?.functionCalls) {
      for (const fc of toolCall.functionCalls) {
        this.turnToolCalls.push(fc.name);
        console.log(`[Voice] toolCall: ${fc.name}(${JSON.stringify(fc.args ?? {})})`);
        const call: FunctionCall = { name: fc.name, args: fc.args ?? {} };
        const result = await executeFunctionCall(call);
        this.sendToolResponse(result.name, result.response);
      }
      // Fire fallback immediately if there was narration text alongside tool calls.
      // turnComplete won't arrive until after the tool response cycle, which is too late.
      if (this.turnTextParts.length > 0) {
        const textJoined = this.turnTextParts.join(' ');
        console.log(`[Voice] Fallback (post-toolCall): already_executed=[${this.turnToolCalls}]`);
        this.callbacks.onTextFallback?.(textJoined, [...this.turnToolCalls]);
        this.turnTextParts = []; // prevent double-fire on turnComplete
      }
    }
  }

  /** Send tool response back to Gemini. */
  private sendToolResponse(name: string, response: { result: string }): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const msg = {
      toolResponse: {
        functionResponses: [{ name, response }],
      },
    };
    this.ws.send(JSON.stringify(msg));
  }

  /** Send a text prompt into the live session as a user turn. */
  sendTextPrompt(text: string): void {
    if (!this.ws || !this.setupDone || this.ws.readyState !== WebSocket.OPEN) return;
    const msg = {
      client_content: {
        turns: [{
          role: 'user',
          parts: [{ text }],
        }],
        turn_complete: true,
      },
    };
    this.ws.send(JSON.stringify(msg));
  }

  /** Send a PCM16 audio chunk (base64 encoded) to Gemini. */
  sendAudio(pcmBase64: string): void {
    if (!this.ws || !this.setupDone || this.ws.readyState !== WebSocket.OPEN) return;
    const msg = {
      realtimeInput: {
        mediaChunks: [
          {
            mimeType: 'audio/pcm;rate=16000',
            data: pcmBase64,
          },
        ],
      },
    };
    this.ws.send(JSON.stringify(msg));
  }

  get isConnected(): boolean {
    return this._state === 'connected' && this.setupDone;
  }
}
