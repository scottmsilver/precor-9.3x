import React, { createContext, useContext } from 'react';
import { useVoice } from './useVoice';
import type { UseVoiceReturn } from './useVoice';

const VoiceContext = createContext<UseVoiceReturn | null>(null);

export function VoiceProvider({ children }: { children: React.ReactNode }) {
  const voice = useVoice();
  return <VoiceContext.Provider value={voice}>{children}</VoiceContext.Provider>;
}

export function useVoiceContext(): UseVoiceReturn {
  const ctx = useContext(VoiceContext);
  if (!ctx) throw new Error('useVoiceContext must be used within VoiceProvider');
  return ctx;
}
