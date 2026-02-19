import React, { useState, useCallback, useRef, useEffect } from 'react';
import { useLocation } from 'wouter';
import TabBar from './components/TabBar';
import Toast from './components/Toast';
import DisconnectBanner from './components/DisconnectBanner';
import SettingsPanel from './components/SettingsPanel';
import VoiceOverlay from './components/VoiceOverlay';
import { ToastContext, registerToast } from './state/TreadmillContext';
import { useVoice } from './state/useVoice';

export default function App({ children }: { children: React.ReactNode }) {
  const [location] = useLocation();
  const [toastMsg, setToastMsg] = useState('');
  const [toastVisible, setToastVisible] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const toastTimer = useRef<ReturnType<typeof setTimeout>>();
  const { voiceState, toggle: toggleVoice } = useVoice();

  const isRun = location.startsWith('/run');

  const showToast = useCallback((message: string) => {
    setToastMsg(message);
    setToastVisible(true);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToastVisible(false), 8000);
  }, []);

  // Wire up the module-level toast ref so TreadmillProvider's WebSocket
  // handler (encouragement, session-end warnings) can show toasts
  useEffect(() => {
    registerToast(showToast);
    return () => clearTimeout(toastTimer.current);
  }, [showToast]);

  return (
    <ToastContext.Provider value={showToast}>
      <DisconnectBanner />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', paddingBottom: isRun ? 0 : 60 }}>
        {children}
      </div>
      <TabBar
        voiceState={voiceState}
        onVoiceToggle={toggleVoice}
        onSettingsToggle={() => setSettingsOpen(s => !s)}
      />
      <VoiceOverlay voiceState={voiceState} />
      <Toast message={toastMsg} visible={toastVisible} />
      <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </ToastContext.Provider>
  );
}
