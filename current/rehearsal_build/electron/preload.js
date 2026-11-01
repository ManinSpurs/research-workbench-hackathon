'use strict';

// Preload runs with sandbox + contextIsolation on. The renderer gets exactly
// this small surface and nothing else: no node, no ipcRenderer, no remote.

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('WB_DESKTOP', {
  isDesktop: true,

  // Opens the native 파일 → 열기… dialog and resolves to the chosen path or null.
  openChip: () => ipcRenderer.invoke('wb:open-chip'),

  // Subscribes to chips opened through the native menu (or the dialog).
  // Returns an unsubscribe function.
  onChipOpened: (cb) => {
    if (typeof cb !== 'function') return () => {};
    const handler = (_event, payload) => cb(payload);
    ipcRenderer.on('chip:open', handler);
    return () => ipcRenderer.removeListener('chip:open', handler);
  },

  // Asks the main process to run the same "new chip" flow as 파일 → 새 칩 만들기…
  // (the main process then emits chip:new back to this renderer).
  newChip: () => ipcRenderer.send('wb:new-chip'),
  saveChip: (payload) => ipcRenderer.invoke('wb:save-chip', payload),

  // Subscribes to chip:new (native menu or newChip()). Returns an unsubscribe fn.
  onNewChip: (cb) => {
    if (typeof cb !== 'function') return () => {};
    const handler = (_event) => cb();
    ipcRenderer.on('chip:new', handler);
    return () => ipcRenderer.removeListener('chip:new', handler);
  },

  // 'boot' | 'home' | 'menu' — animated native window resize in the main process.
  setWindowSize: (mode) => ipcRenderer.send('wb:set-window-size', mode),
  windowControl: (action) => {
    if (action === 'close' || action === 'minimize') ipcRenderer.send('wb:window-control', action);
  },
  ignorePointer: (ignore) => ipcRenderer.send('wb:ignore-pointer', ignore === true),
});
