import React from 'react';

interface ToastProps {
  message: string;
  visible: boolean;
}

export default function Toast({ message, visible }: ToastProps) {
  if (!visible || !message) return null;

  return (
    <div
      key={message}
      style={{
        position: 'fixed',
        bottom: 56,
        left: 16, right: 16,
        maxWidth: 480,
        margin: '0 auto',
        background: 'var(--elevated)',
        border: '0.5px solid var(--separator)',
        borderRadius: 'var(--r-md)',
        padding: '10px 14px',
        fontSize: 13, color: 'var(--text2)',
        lineHeight: 1.4,
        zIndex: 25,
        animation: 'toastLife 8s var(--ease) forwards',
        pointerEvents: 'none' as const,
        backdropFilter: 'blur(12px)',
        WebkitBackdropFilter: 'blur(12px)',
      }}
    >
      {message}
    </div>
  );
}
