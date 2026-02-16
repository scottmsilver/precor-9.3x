/**
 * Maps Gemini function calls to REST API calls via api.ts.
 */
import * as api from '../state/api';

export interface FunctionCall {
  name: string;
  args: Record<string, unknown>;
}

export interface FunctionResult {
  name: string;
  response: { result: string };
}

/** Execute a Gemini function call via our treadmill REST API. */
export async function executeFunctionCall(call: FunctionCall): Promise<FunctionResult> {
  const { name, args } = call;
  let result: string;

  try {
    switch (name) {
      case 'set_speed': {
        await api.setSpeed(args.mph as number);
        result = `Speed set to ${args.mph} mph`;
        break;
      }
      case 'set_incline': {
        await api.setIncline(args.incline as number);
        result = `Incline set to ${args.incline}%`;
        break;
      }
      case 'start_workout': {
        const gen = await api.generateProgram(args.description as string);
        if (gen.ok) {
          await api.startProgram();
          result = 'Workout program started';
        } else {
          result = `Error generating program: ${gen.error ?? 'unknown'}`;
        }
        break;
      }
      case 'stop_treadmill': {
        await Promise.all([
          api.setSpeed(0),
          api.setIncline(0),
          api.stopProgram(),
        ]);
        result = 'Treadmill stopped';
        break;
      }
      case 'pause_program': {
        await api.pauseProgram();
        result = 'Program paused';
        break;
      }
      case 'resume_program': {
        await api.pauseProgram(); // toggle pause
        result = 'Program resumed';
        break;
      }
      case 'skip_interval': {
        await api.skipInterval();
        result = 'Skipped to next interval';
        break;
      }
      case 'extend_interval': {
        await api.extendInterval(args.seconds as number);
        result = `Interval extended by ${args.seconds} seconds`;
        break;
      }
      case 'add_time': {
        // add_time goes through chat endpoint since it needs server-side program modification
        const resp = await api.sendChat(
          `[function_result] add_time with intervals: ${JSON.stringify(args.intervals)}`
        );
        result = resp.text || 'Time added';
        break;
      }
      default:
        result = `Unknown function: ${name}`;
    }
  } catch (err) {
    result = `Error executing ${name}: ${err instanceof Error ? err.message : String(err)}`;
  }

  return {
    name,
    response: { result },
  };
}
