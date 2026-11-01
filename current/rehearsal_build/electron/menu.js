'use strict';

// Native macOS menu bar for the Workbench window.
//
// Structure (top level, in order):
//   Mixdog  — app menu (종료)
//   파일    — 새 칩 만들기… (CmdOrCtrl+N), 열기… (CmdOrCtrl+O), ─,
//             최근 연 칩 (submenu), 닫기
//   편집    — undo / redo / cut / copy / paste / select-all roles
//   보기    — reload, toggle devtools
//   창      — minimize, zoom, front
//
// Electron advantage noted for a later audio task: the renderer in an Electron
// window is allowed to start audio playback without a preceding user gesture,
// unlike a plain browser tab, which is blocked by the autoplay policy.

const path = require('node:path');
const fs = require('node:fs/promises');
const { BrowserWindow, Menu, dialog } = require('electron');

const CHIP_FILTERS = [{ name: 'Research chip', extensions: ['chip'] }];

// Most recently opened chips, newest first. Starts empty, so 파일 → 최근 연 칩
// is an empty submenu until the researcher opens something.
const recentChips = [];
const RECENT_LIMIT = 10;

function currentWindow() {
  return BrowserWindow.getFocusedWindow() || BrowserWindow.getAllWindows()[0] || null;
}

async function openChipDialog(win) {
  const target = win && !win.isDestroyed() ? win : currentWindow();
  const result = await dialog.showOpenDialog(target, {
    title: '칩 파일 열기',
    properties: ['openFile'],
    filters: CHIP_FILTERS,
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
}

function rememberChip(filePath) {
  const existing = recentChips.indexOf(filePath);
  if (existing !== -1) recentChips.splice(existing, 1);
  recentChips.unshift(filePath);
  if (recentChips.length > RECENT_LIMIT) recentChips.length = RECENT_LIMIT;
}

// Tell the renderer about a chosen chip file. The renderer decides what to do
// with it (frontend/chip_bridge.js forwards to window.WB_CHIP_HOOK).
async function deliverChip(filePath, win) {
  const target = win && !win.isDestroyed() ? win : currentWindow();
  if (!target || target.isDestroyed()) return;
  try {
    const info = await fs.stat(filePath);
    if (!info.isFile() || info.size > 100 * 1024 * 1024) throw new Error('Choose a .chip file no larger than 100 MiB.');
    const bytes = await fs.readFile(filePath);
    if (target.isDestroyed()) return;
    target.webContents.send('chip:open', { path: filePath, filename: path.basename(filePath), base64: bytes.toString('base64') });
    rememberChip(filePath);
    buildMenu();
  } catch (error) { dialog.showErrorBox('Cannot open chip', error.message); }
}

// New-chip flow: the native menu only notifies the renderer. The renderer owns
// what "a new chip" means (frontend/chip_bridge.js forwards to
// window.WB_CHIP_NEW_HOOK), so no state is invented here.
function deliverNewChip(win) {
  const target = win && !win.isDestroyed() ? win : currentWindow();
  if (!target || target.isDestroyed()) return;
  target.webContents.send('chip:new');
}

function recentSubmenu() {
  const items = recentChips.map((filePath) => ({
    label: path.basename(filePath),
    toolTip: filePath,
    click: () => deliverChip(filePath),
  }));
  return items;
}

function buildMenu() {
  const template = [
    {
      label: 'Research Workbench',
      submenu: [
        { label: '종료', accelerator: 'CmdOrCtrl+Q', role: 'quit' },
      ],
    },
    {
      label: 'File',
      submenu: [
        {
          label: 'New Chip…',
          accelerator: 'CmdOrCtrl+N',
          click: () => deliverNewChip(currentWindow()),
        },
        {
          label: 'Open…',
          accelerator: 'CmdOrCtrl+O',
          click: async () => {
            const win = currentWindow();
            const chosen = await openChipDialog(win);
            if (chosen) deliverChip(chosen, win);
          },
        },
        { type: 'separator' },
        { label: 'Open Recent', submenu: recentSubmenu() },
        { label: '닫기', accelerator: 'CmdOrCtrl+W', role: 'close' },
      ],
    },
    {
      label: '편집',
      submenu: [
        { label: '실행 취소', accelerator: 'CmdOrCtrl+Z', role: 'undo' },
        { label: '다시 실행', accelerator: 'Shift+CmdOrCtrl+Z', role: 'redo' },
        { type: 'separator' },
        { label: '오려두기', accelerator: 'CmdOrCtrl+X', role: 'cut' },
        { label: '복사하기', accelerator: 'CmdOrCtrl+C', role: 'copy' },
        { label: '붙여넣기', accelerator: 'CmdOrCtrl+V', role: 'paste' },
        { label: '전체 선택', accelerator: 'CmdOrCtrl+A', role: 'selectAll' },
      ],
    },
    {
      label: '보기',
      submenu: [
        { label: '새로 고침', accelerator: 'CmdOrCtrl+R', role: 'reload' },
        { label: '개발자 도구', accelerator: 'Alt+CmdOrCtrl+I', role: 'toggleDevTools' },
      ],
    },
    {
      label: '창',
      submenu: [
        { label: '최소화', accelerator: 'CmdOrCtrl+M', role: 'minimize' },
        { label: '확대/축소', role: 'zoom' },
        { label: '모두 앞으로 가져오기', role: 'front' },
      ],
    },
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
  return menu;
}

module.exports = { buildMenu, openChipDialog, deliverChip, deliverNewChip, recentChips };
