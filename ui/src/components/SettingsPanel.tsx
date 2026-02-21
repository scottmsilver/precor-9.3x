import React, { useState, useRef, useCallback, useEffect } from 'react';
import { useLocation } from 'wouter';
import { useTreadmillState, useTreadmillActions, useToast } from '../state/TreadmillContext';
import * as api from '../state/api';
import { haptic } from '../utils/haptics';
import { hrColor } from '../utils/hrColor';

interface SettingsPanelProps {
  open: boolean;
  onClose: () => void;
}

const rowStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  height: 48, padding: '0 4px',
  borderBottom: '1px solid var(--separator)',
  cursor: 'pointer',
  WebkitTapHighlightColor: 'transparent',
};

export default function SettingsPanel({ open, onClose }: SettingsPanelProps): React.ReactElement | null {
  const { status, hrmDevices } = useTreadmillState();
  const actions = useTreadmillActions();
  const showToast = useToast();
  const [debugUnlocked, setDebugUnlocked] = useState(false);
  const [smartass, setSmartass] = useState(() => {
    try { return localStorage.getItem('smartass_mode') === 'true'; } catch { return false; }
  });
  const [hrmScanning, setHrmScanning] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const debugTaps = useRef<number[]>([]);
  const [, setLocation] = useLocation();

  // Reset debug unlock when panel closes
  useEffect(() => {
    if (!open) setDebugUnlocked(false);
  }, [open]);

  const handleHeaderTap = useCallback(() => {
    const now = Date.now();
    debugTaps.current.push(now);
    debugTaps.current = debugTaps.current.filter(t => now - t < 500);
    if (debugTaps.current.length >= 3) {
      debugTaps.current = [];
      setDebugUnlocked(true);
      haptic(50);
    }
  }, []);

  if (!open) return null;

  const handleGpxUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const data = await api.uploadGpx(file);
      if (data.ok && data.program) {
        const name = (data.program as { name?: string }).name || 'Route';
        showToast(`Loaded GPX route "${name}". Tap Start to begin!`);
        haptic(25);
        onClose();
      } else {
        showToast('GPX upload failed: ' + (data.error || 'unknown error'));
      }
    } catch (err) {
      showToast('GPX upload failed: ' + (err instanceof Error ? err.message : 'unknown error'));
    }
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  return (
    <>
      {/* Overlay */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0, zIndex: 300,
          background: 'rgba(18,18,16,0.6)',
          backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)',
        }}
      />

      {/* Panel */}
      <div style={{
        position: 'fixed', top: 0, right: 0, bottom: 0, zIndex: 301,
        width: 'min(320px, 85vw)', background: '#1E1D1B',
        borderRadius: 'var(--r-xl) 0 0 var(--r-xl)',
        padding: '24px 16px', overflowY: 'auto',
      }}>
        {/* Header — triple-tap unlocks debug */}
        <h2
          onClick={handleHeaderTap}
          style={{
            fontSize: 18, fontWeight: 700, color: 'var(--text)',
            margin: '0 0 20px', cursor: 'default',
            WebkitTapHighlightColor: 'transparent',
            userSelect: 'none',
          }}
        >
          Settings
        </h2>

        {/* Import GPX */}
        <label style={rowStyle}>
          <span style={{ fontSize: 15, color: 'var(--text)' }}>Import GPX Route</span>
          <span style={{ fontSize: 13, color: 'var(--text3)' }}>&#8250;</span>
          <input
            ref={fileInputRef}
            type="file"
            accept=".gpx"
            onChange={handleGpxUpload}
            style={{ display: 'none' }}
          />
        </label>

        {/* Debug Console */}
        <div
          onClick={() => { setLocation('/debug'); onClose(); haptic(25); }}
          style={rowStyle}
        >
          <span style={{ fontSize: 15, color: 'var(--text)' }}>Debug Console</span>
          <span style={{ fontSize: 13, color: 'var(--text3)' }}>&#8250;</span>
        </div>

        {/* Smart-ass mode toggle */}
        <div
          onClick={() => {
            const next = !smartass;
            setSmartass(next);
            try { localStorage.setItem('smartass_mode', String(next)); } catch {}
            haptic(25);
            showToast(next ? 'Smart-ass mode ON. Brace yourself.' : 'Smart-ass mode off.');
          }}
          style={rowStyle}
        >
          <span style={{ fontSize: 15, color: 'var(--text)' }}>Smart-ass Mode</span>
          <div style={{
            width: 44, height: 26, borderRadius: 13,
            background: smartass ? 'var(--purple)' : 'var(--fill)',
            position: 'relative', transition: 'background 200ms var(--ease)',
            flexShrink: 0,
          }}>
            <div style={{
              width: 22, height: 22, borderRadius: 11,
              background: '#fff',
              position: 'absolute', top: 2,
              left: smartass ? 20 : 2,
              transition: 'left 200ms var(--ease)',
            }} />
          </div>
        </div>

        {/* Heart Rate Monitor */}
        <div style={{ marginTop: 24 }}>
          <h3 style={{
            fontSize: 13, fontWeight: 600, color: 'var(--text3)',
            textTransform: 'uppercase' as const, letterSpacing: '0.02em',
            margin: '0 0 8px',
          }}>Heart Rate Monitor</h3>

          {/* Connection status */}
          <div style={{
            ...rowStyle,
            cursor: 'default',
          }}>
            <span style={{ fontSize: 15, color: 'var(--text)' }}>
              {status.hrmConnected ? `${status.heartRate > 0 ? status.heartRate + ' bpm' : 'Connected'}` : 'Not connected'}
            </span>
            {status.hrmConnected && (
              <span style={{
                fontSize: 14,
                color: hrColor(status.heartRate),
              }}>{'\u2665'}</span>
            )}
            {!status.hrmConnected && (
              <span style={{ fontSize: 13, color: 'var(--text3)' }}>{'\u2013'}</span>
            )}
          </div>

          {/* Device list from scan results */}
          {hrmDevices.length > 0 && !status.hrmConnected && (
            <div style={{ borderBottom: '1px solid var(--separator)' }}>
              {hrmDevices.map(d => {
                const selectDevice = async () => {
                  haptic(25);
                  try {
                    await api.selectHrmDevice(d.address);
                    showToast(`Connecting to ${d.name || d.address}...`);
                  } catch {
                    showToast('Failed to select device');
                  }
                };
                return (
                <div
                  key={d.address}
                  role="button"
                  tabIndex={0}
                  onClick={selectDevice}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      selectDevice();
                    }
                  }}
                  style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    height: 44, padding: '0 4px',
                    cursor: 'pointer',
                    WebkitTapHighlightColor: 'transparent',
                  }}
                >
                  <span style={{ fontSize: 14, color: 'var(--text)' }}>
                    {d.name || d.address}
                  </span>
                  <span style={{ fontSize: 12, color: 'var(--text3)', fontVariantNumeric: 'tabular-nums' }}>
                    {d.rssi} dBm
                  </span>
                </div>
                );
              })}
            </div>
          )}

          {/* Scan / Forget buttons */}
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <button
              onClick={async () => {
                haptic(25);
                setHrmScanning(true);
                try {
                  await api.scanHrm();
                } catch {
                  showToast('Scan failed');
                }
                setTimeout(() => setHrmScanning(false), 10000);
              }}
              disabled={hrmScanning}
              style={{
                flex: 1, height: 44, border: 'none',
                borderRadius: 'var(--r-sm)',
                background: 'var(--fill2)',
                color: hrmScanning ? 'var(--text3)' : 'var(--text)',
                fontSize: 14, fontWeight: 600, fontFamily: 'inherit',
                cursor: hrmScanning ? 'default' : 'pointer',
                WebkitTapHighlightColor: 'transparent',
              }}
            >
              {hrmScanning ? 'Scanning...' : 'Scan'}
            </button>
            {status.hrmConnected && (
              <button
                onClick={async () => {
                  haptic([25, 30, 25]);
                  try {
                    await api.forgetHrmDevice();
                    showToast('HRM device forgotten');
                  } catch {
                    showToast('Failed to forget device');
                  }
                }}
                style={{
                  flex: 1, height: 44, border: 'none',
                  borderRadius: 'var(--r-sm)',
                  background: 'var(--fill2)',
                  color: 'var(--red)',
                  fontSize: 14, fontWeight: 600, fontFamily: 'inherit',
                  cursor: 'pointer',
                  WebkitTapHighlightColor: 'transparent',
                }}
              >
                Forget
              </button>
            )}
          </div>
        </div>

        {/* Mode toggle (unlocked by triple-tap) */}
        {debugUnlocked && (
          <div style={{ marginTop: 24 }}>
            <h3 style={{
              fontSize: 13, fontWeight: 600, color: 'var(--text3)',
              textTransform: 'uppercase' as const, letterSpacing: '0.02em',
              margin: '0 0 8px',
            }}>Mode</h3>
            <div style={{
              display: 'flex', borderRadius: 'var(--r-sm)', overflow: 'hidden',
              background: 'var(--fill2)',
            }}>
              {(['proxy', 'emulate'] as const).map(mode => {
                const active = mode === 'proxy' ? status.proxy : status.emulate;
                const bg = mode === 'proxy' ? 'var(--green)' : 'var(--purple)';
                const fg = mode === 'proxy' ? '#000' : '#fff';
                return (
                  <button
                    key={mode}
                    onClick={() => { actions.setMode(mode); haptic([25, 30, 25]); }}
                    style={{
                      flex: 1, height: 44, border: 'none',
                      background: active ? bg : 'transparent',
                      color: active ? fg : 'var(--text3)',
                      fontSize: 15, fontWeight: 600, fontFamily: 'inherit',
                      cursor: 'pointer', borderRadius: 'var(--r-sm)',
                      WebkitTapHighlightColor: 'transparent',
                      transition: 'all 200ms var(--ease)',
                    }}
                  >{mode === 'proxy' ? 'Proxy' : 'Emulate'}</button>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </>
  );
}
