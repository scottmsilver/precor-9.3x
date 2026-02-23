/**
 * Tool declarations for Gemini Live API function calling.
 * Mirrors TOOL_DECLARATIONS from program_engine.py.
 */

export interface FunctionDeclaration {
  name: string;
  description: string;
  parameters: {
    type: string;
    properties: Record<string, { type: string; description?: string; items?: unknown }>;
    required?: string[];
  };
}

export const TOOL_DECLARATIONS: FunctionDeclaration[] = [
  {
    name: 'set_speed',
    description: 'Set treadmill belt speed',
    parameters: {
      type: 'OBJECT',
      properties: { mph: { type: 'NUMBER', description: 'Speed in mph (0-12)' } },
      required: ['mph'],
    },
  },
  {
    name: 'set_incline',
    description: 'Set treadmill incline grade',
    parameters: {
      type: 'OBJECT',
      properties: { incline: { type: 'NUMBER', description: 'Incline percent (0-15, 0.5% steps)' } },
      required: ['incline'],
    },
  },
  {
    name: 'start_workout',
    description: 'Generate and start an interval training program',
    parameters: {
      type: 'OBJECT',
      properties: { description: { type: 'STRING', description: 'Workout description' } },
      required: ['description'],
    },
  },
  {
    name: 'stop_treadmill',
    description: 'Stop the treadmill and end any running program',
    parameters: {
      type: 'OBJECT',
      properties: {},
    },
  },
  {
    name: 'pause_program',
    description: 'Pause the running interval program',
    parameters: {
      type: 'OBJECT',
      properties: {},
    },
  },
  {
    name: 'resume_program',
    description: 'Resume a paused program',
    parameters: {
      type: 'OBJECT',
      properties: {},
    },
  },
  {
    name: 'skip_interval',
    description: 'Skip to next interval in program',
    parameters: {
      type: 'OBJECT',
      properties: {},
    },
  },
  {
    name: 'extend_interval',
    description: 'Add or subtract seconds from the current interval duration. Positive = longer, negative = shorter. Min 10s.',
    parameters: {
      type: 'OBJECT',
      properties: {
        seconds: { type: 'NUMBER', description: 'Seconds to add (positive) or subtract (negative)' },
      },
      required: ['seconds'],
    },
  },
  {
    name: 'add_time',
    description: 'Add extra intervals at the end of the running program',
    parameters: {
      type: 'OBJECT',
      properties: {
        intervals: {
          type: 'ARRAY',
          description: 'Array of interval objects with name, duration (seconds), speed (mph), incline (%)',
          items: {
            type: 'OBJECT',
            properties: {
              name: { type: 'STRING' },
              duration: { type: 'NUMBER' },
              speed: { type: 'NUMBER' },
              incline: { type: 'NUMBER' },
            },
          },
        },
      },
      required: ['intervals'],
    },
  },
];

/** System prompt for the voice AI coach. Matches CHAT_SYSTEM_PROMPT from program_engine.py. */
export const VOICE_SYSTEM_PROMPT = `You are an AI treadmill coach. You control a Precor treadmill via function calls.
Be brief, friendly, motivating. Respond in 1-3 short sentences max.
Feel free to use emoji in your text responses when it feels natural.

Tools:
- set_speed: change speed (mph). Use 0 to stop belt.
- set_incline: change incline (0-15%, 0.5% steps)
- start_workout: create & start an interval program from a description
- stop_treadmill: emergency stop (speed 0, incline 0, end program)
- pause_program / resume_program: pause/resume interval programs
- skip_interval: skip to next interval
- extend_interval: add or subtract seconds from current interval (positive = longer, negative = shorter)
- add_time: add extra intervals at the end of the current program

CRITICAL RULE — never change speed, incline, or any treadmill setting unless the user explicitly asks you to. Do NOT proactively adjust settings to "push" or "challenge" the user. Only use tools in direct response to a clear user request.

Guidelines:
- For workout requests, use start_workout with a detailed description
- For simple adjustments ("faster", "more incline"), use set_speed/set_incline
- Walking: 2-4 mph. Jogging: 4-6 mph. Running: 6+ mph
- If user says "stop", use stop_treadmill immediately
- For "more time", "extend", "add 5 minutes" etc., use extend_interval or add_time
- extend_interval changes the CURRENT interval's duration (e.g. +60 adds 1 min)
- add_time appends new intervals at the END of the program
- Always confirm what you did briefly
- You can wrap a single important word in <<double angle brackets>> to give it an animated glow effect in the UI. Use sparingly for emphasis.`;

export const VOICE_SMARTASS_ADDENDUM = `
SMART-ASS MODE: Be sarcastic, witty, and make fun of the user for being lazy.
Roast them (lovingly) about their pace, breaks, or workout choices.
Still be helpful and encouraging underneath the sass.`;
