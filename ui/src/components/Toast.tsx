import React from 'react';
import { motion, AnimatePresence } from 'motion/react';

/** Parse <<word>> glow markup into React elements. */
export function renderGlowText(text: string): React.ReactNode {
  const parts = text.split(/(<<.+?>>)/g);
  if (parts.length === 1) return text;
  return parts.map((part, i) => {
    const match = part.match(/^<<(.+?)>>$/);
    if (match) {
      return (
        <span
          key={i}
          className="glow-word"
          style={{
            color: 'var(--purple)',
            fontWeight: 600,
            textShadow: '0 0 6px var(--purple), 0 0 12px rgba(139,127,160,0.4)',
          }}
        >
          {match[1]}
        </span>
      );
    }
    return part;
  });
}

interface ToastProps {
  message: string;
  visible: boolean;
}

export default function Toast({ message, visible }: ToastProps) {
  return (
    <AnimatePresence>
      {visible && message && (
        <motion.div
          key={message}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.3 }}
          style={{
            position: 'fixed',
            bottom: 80,
            left: 16, right: 16,
            maxWidth: 480,
            margin: '0 auto',
            background: 'var(--elevated)',
            border: '0.5px solid var(--separator)',
            borderRadius: 'var(--r-md)',
            padding: '10px 14px',
            fontSize: 13, color: 'var(--text2)',
            lineHeight: 1.4,
            zIndex: 400,
            pointerEvents: 'none' as const,
            backdropFilter: 'blur(12px)',
            WebkitBackdropFilter: 'blur(12px)',
          }}
        >
          {renderGlowText(message)}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
