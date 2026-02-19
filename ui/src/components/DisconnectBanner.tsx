import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { useTreadmillState } from '../state/TreadmillContext';

const bannerBase: React.CSSProperties = {
  position: 'fixed', top: 0, left: 0, right: 0, zIndex: 200,
  backdropFilter: 'blur(8px)',
  WebkitBackdropFilter: 'blur(8px)',
  padding: '10px 16px', textAlign: 'center' as const,
  fontSize: 13, fontWeight: 600,
};

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

  return (
    <AnimatePresence>
      {!status.treadmillConnected && (
        <motion.div
          key="disconnected"
          initial={{ y: '-100%' }}
          animate={{ y: 0 }}
          exit={{ y: '-100%' }}
          transition={{ duration: 0.3, ease: [0, 0, 0.2, 1] }}
          style={{
            ...bannerBase,
            background: 'rgba(196,92,82,0.15)',
            borderBottom: '1px solid rgba(196,92,82,0.3)',
            color: 'var(--red)',
          }}
        >
          Treadmill disconnected — reconnecting...
        </motion.div>
      )}
      {status.treadmillConnected && showReconnect && (
        <motion.div
          key="reconnected"
          initial={{ opacity: 0, y: '-100%' }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.3, ease: [0, 0, 0.2, 1] }}
          style={{
            ...bannerBase,
            background: 'rgba(107,200,155,0.15)',
            borderBottom: '1px solid rgba(107,200,155,0.3)',
            color: 'var(--green)',
          }}
        >
          Reconnected
        </motion.div>
      )}
    </AnimatePresence>
  );
}
