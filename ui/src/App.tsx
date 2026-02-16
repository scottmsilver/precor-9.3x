import React, { useState, useCallback, useRef, useEffect } from 'react';
import { useLocation } from 'wouter';
import Header from './components/Header';
import Toast from './components/Toast';
import DisconnectBanner from './components/DisconnectBanner';
import StopButton from './components/StopButton';
import SettingsPanel from './components/SettingsPanel';
import VoiceFAB from './components/VoiceFAB';
import VoiceOverlay from './components/VoiceOverlay';
import { ToastContext } from './state/TreadmillContext';
import { useVoice } from './state/useVoice';

export default function App({ children }: { children: React.ReactNode }) {
  const [location] = useLocation();
  const [toastMsg, setToastMsg] = useState('');
  const [toastVisible, setToastVisible] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const toastTimer = useRef<ReturnType<typeof setTimeout>>();
  const { voiceState, toggle: toggleVoice } = useVoice();

  const showToast = useCallback((message: string) => {
    setToastMsg(message);
    setToastVisible(true);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToastVisible(false), 8000);
  }, []);

  useEffect(() => {
    return () => clearTimeout(toastTimer.current);
  }, []);

  return (
    <ToastContext.Provider value={showToast}>
      <Header onSettingsToggle={() => setSettingsOpen(s => !s)} />
      <DisconnectBanner />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {children}
      </div>
      {location !== '/run' && <StopButton />}
      {location !== '/run' && <VoiceFAB voiceState={voiceState} onTap={toggleVoice} />}
      <VoiceOverlay voiceState={voiceState} />
      <Toast message={toastMsg} visible={toastVisible} />
      <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} showDebug={location === '/debug'} />
    </ToastContext.Provider>
  );
}
