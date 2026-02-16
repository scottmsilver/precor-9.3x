import React, { useState, useEffect, useRef } from 'react';
import { useTreadmillState } from '../state/TreadmillContext';

export default function DisconnectBanner() {
  const { status } = useTreadmillState();
  const [showReconnect, setShowReconnect] = useState(false);
  const prevConnected = useRef(status.treadmillConnected);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    if (!prevConnected.current && status.treadmillConnected) {
      setShowReconnect(true);
      timer.current = setTimeout(() => setShowReconnect(false), 3000);
    }
    prevConnected.current = status.treadmillConnected;
    return () => clearTimeout(timer.current);
  }, [status.treadmillConnected]);

  if (!status.treadmillConnected) {
    return (
      <div style={{
        position: 'fixed', top: 0, left: 0, right: 0, zIndex: 40,
        background: 'rgba(196,92,82,0.15)', backdropFilter: 'blur(8px)',
        WebkitBackdropFilter: 'blur(8px)',
        borderBottom: '1px solid rgba(196,92,82,0.3)',
        padding: '10px 16px', textAlign: 'center' as const,
        fontSize: 13, fontWeight: 600, color: 'var(--red)',
        animation: 'bannerSlideDown 300ms var(--ease-decel) forwards',
      }}>
        Treadmill disconnected — reconnecting...
      </div>
    );
  }

  if (showReconnect) {
    return (
      <div style={{
        position: 'fixed', top: 0, left: 0, right: 0, zIndex: 40,
        background: 'rgba(107,200,155,0.15)', backdropFilter: 'blur(8px)',
        WebkitBackdropFilter: 'blur(8px)',
        borderBottom: '1px solid rgba(107,200,155,0.3)',
        padding: '10px 16px', textAlign: 'center' as const,
        fontSize: 13, fontWeight: 600, color: 'var(--green)',
        animation: 'reconnectFlash 3s var(--ease) forwards',
      }}>
        Reconnected
      </div>
    );
  }

  return null;
}
