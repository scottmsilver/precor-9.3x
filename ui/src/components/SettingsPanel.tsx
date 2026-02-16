import React, { useState, useRef } from 'react';
import { useTreadmillState, useTreadmillActions } from '../state/TreadmillContext';
import { useToast } from '../state/TreadmillContext';
import * as api from '../state/api';
import { haptic } from '../utils/haptics';

interface SettingsPanelProps {
  open: boolean;
  onClose: () => void;
  showDebug: boolean;
}

export default function SettingsPanel({ open, onClose, showDebug }: SettingsPanelProps): React.ReactElement | null {
  const { status } = useTreadmillState();
  const actions = useTreadmillActions();
  const showToast = useToast();
  const [voiceEnabled, setVoiceEnabled] = useState(true);
  const fileInputRef = useRef<HTMLInputElement>(null);

  if (!open) return null;

  const speedPresets = [2, 3, 4, 5, 6, 7, 8, 10];
  const inclinePresets = [0, 2, 4, 6, 8, 10, 12, 15];

  const handleGpxUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const data = await api.uploadGpx(file);
      if (data.ok && data.program) {
        const name = (data.program as { name?: string }).name || 'Route';
        showToast(`Loaded GPX route "${name}". Tap Start to begin!`);
        haptic(25);
      } else {
        showToast('GPX upload failed: ' + (data.error || 'unknown error'));
      }
    } catch (err) {
      showToast('GPX upload failed: ' + (err instanceof Error ? err.message : 'unknown error'));
    }
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const presetStyle = (active: boolean, isIncline = false): React.CSSProperties => ({
    height: 44, borderRadius: 'var(--r-sm)',
    border: 'none',
    background: active
      ? (isIncline ? 'rgba(166,152,130,0.2)' : 'rgba(107,200,155,0.2)')
      : 'var(--fill2)',
    color: active
      ? (isIncline ? 'var(--orange)' : 'var(--green)')
      : 'var(--text2)',
    fontSize: 15, fontWeight: 600,
    fontVariantNumeric: 'tabular-nums', fontFamily: 'inherit',
    cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
    WebkitTapHighlightColor: 'transparent',
    transition: 'transform 100ms var(--ease), background 100ms var(--ease)',
  });

  return (
    <>
      {/* Overlay */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0, zIndex: 50,
          background: 'rgba(18,18,16,0.6)',
          backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)',
        }}
      />

      {/* Panel */}
      <div style={{
        position: 'fixed', top: 0, right: 0, bottom: 0, zIndex: 51,
        width: 'min(320px, 85vw)', background: '#1E1D1B',
        borderRadius: 'var(--r-xl) 0 0 var(--r-xl)',
        padding: '24px 16px', overflowY: 'auto',
      }}>
        {/* Mode toggle (debug only) */}
        {showDebug && (
          <>
            <h3 style={{
              fontSize: 13, fontWeight: 600, color: 'var(--text3)',
              textTransform: 'uppercase' as const, letterSpacing: '0.02em',
              margin: '0 0 8px',
            }}>Mode</h3>
            <div style={{
              display: 'flex', borderRadius: 'var(--r-sm)', overflow: 'hidden',
              background: 'var(--fill2)',
            }}>
              <button
                onClick={() => { actions.setMode('proxy'); haptic([25, 30, 25]); }}
                style={{
                  flex: 1, height: 44, border: 'none',
                  background: status.proxy ? 'var(--green)' : 'transparent',
                  color: status.proxy ? '#000' : 'var(--text3)',
                  fontSize: 15, fontWeight: 600, fontFamily: 'inherit',
                  cursor: 'pointer', borderRadius: 'var(--r-sm)',
                  WebkitTapHighlightColor: 'transparent',
                  transition: 'all 200ms var(--ease)',
                }}
              >Proxy</button>
              <button
                onClick={() => { actions.setMode('emulate'); haptic([25, 30, 25]); }}
                style={{
                  flex: 1, height: 44, border: 'none',
                  background: status.emulate ? 'var(--purple)' : 'transparent',
                  color: status.emulate ? '#fff' : 'var(--text3)',
                  fontSize: 15, fontWeight: 600, fontFamily: 'inherit',
                  cursor: 'pointer', borderRadius: 'var(--r-sm)',
                  WebkitTapHighlightColor: 'transparent',
                  transition: 'all 200ms var(--ease)',
                }}
              >Emulate</button>
            </div>
          </>
        )}

        {/* Speed presets */}
        <h3 style={{
          fontSize: 13, fontWeight: 600, color: 'var(--text3)',
          textTransform: 'uppercase' as const, letterSpacing: '0.02em',
          margin: '20px 0 8px',
        }}>Speed Presets</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 6 }}>
          {speedPresets.map(p => (
            <button
              key={p}
              style={presetStyle(status.emuSpeed === p * 10)}
              onClick={() => { actions.setSpeed(p); haptic(25); }}
            >{p.toFixed(1)}</button>
          ))}
        </div>

        {/* Incline presets */}
        <h3 style={{
          fontSize: 13, fontWeight: 600, color: 'var(--text3)',
          textTransform: 'uppercase' as const, letterSpacing: '0.02em',
          margin: '20px 0 8px',
        }}>Incline Presets</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 6 }}>
          {inclinePresets.map(p => (
            <button
              key={p}
              style={presetStyle(status.emuIncline === p, true)}
              onClick={() => { actions.setIncline(p); haptic(25); }}
            >{p}</button>
          ))}
        </div>

        {/* Voice toggle */}
        <h3 style={{
          fontSize: 13, fontWeight: 600, color: 'var(--text3)',
          textTransform: 'uppercase' as const, letterSpacing: '0.02em',
          margin: '20px 0 8px',
        }}>Voice</h3>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 0' }}>
          <span style={{ fontSize: 14, color: 'var(--text2)' }}>AI speaks responses</span>
          <button
            onClick={() => { setVoiceEnabled(!voiceEnabled); haptic(15); }}
            style={{
              width: 48, height: 28, borderRadius: 14, border: 'none',
              cursor: 'pointer', padding: 2,
              transition: 'background 200ms var(--ease)',
              background: voiceEnabled ? 'var(--purple)' : 'var(--fill2)',
            }}
          >
            <div style={{
              width: 24, height: 24, borderRadius: 12, background: '#fff',
              transition: 'transform 200ms var(--ease)',
              transform: voiceEnabled ? 'translateX(20px)' : 'translateX(0)',
            }} />
          </button>
        </div>

        {/* GPX upload */}
        <h3 style={{
          fontSize: 13, fontWeight: 600, color: 'var(--text3)',
          textTransform: 'uppercase' as const, letterSpacing: '0.02em',
          margin: '20px 0 8px',
        }}>Import</h3>
        <label style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          height: 44, borderRadius: 'var(--r-sm)', background: 'var(--fill2)',
          color: 'var(--text2)', fontSize: 15, fontWeight: 600, cursor: 'pointer',
        }}>
          Upload GPX Route
          <input
            ref={fileInputRef}
            type="file"
            accept=".gpx"
            onChange={handleGpxUpload}
            style={{ display: 'none' }}
          />
        </label>
      </div>
    </>
  );
}
