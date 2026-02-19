import { useState, useEffect, useRef } from 'react';
import { sendChat } from './api';

const fallbacks = [
  "Let's get moving!",
  "Your legs are ready.",
  "One step at a time.",
  "You showed up. That's the hardest part.",
  "Today's a good day for a run.",
  "Fresh air for the mind.",
  "Just press play.",
  "Your future self says thanks.",
  "Run like nobody's watching.",
  "Miles don't care about Mondays.",
  "Lace up, zone out.",
  "The belt is waiting for you.",
];

const PROMPT = 'Give me a single short, fun, encouraging message for someone about to run on a treadmill. Max 8 words. Just the message, no quotes, no emoji. Be creative and playful.';
const REFRESH_MS = 90_000; // refresh every 90s

export function useMotivation(enabled: boolean): string {
  const [msg, setMsg] = useState(() =>
    fallbacks[Math.floor(Math.random() * fallbacks.length)]
  );
  const timer = useRef<ReturnType<typeof setInterval>>();

  useEffect(() => {
    if (!enabled) return;

    const fetchOne = () => {
      sendChat(PROMPT)
        .then(r => {
          const text = r.text?.trim();
          if (text && text.length < 60) setMsg(text);
        })
        .catch(() => {});
    };

    // Fetch after a short delay, then periodically
    const initial = setTimeout(fetchOne, 2000);
    timer.current = setInterval(fetchOne, REFRESH_MS);

    return () => {
      clearTimeout(initial);
      clearInterval(timer.current);
    };
  }, [enabled]);

  return msg;
}
