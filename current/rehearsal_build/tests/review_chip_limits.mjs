// No server, database, native profile, or external API is used.
import vm from 'node:vm';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import path from 'node:path';
const root = path.resolve(import.meta.dirname, '..');
let size = 100 * 1024 * 1024, sent = 0, errors = 0;
const window = { isDestroyed: () => false, webContents: { send: () => sent++ } };
const menuSource = await fs.readFile(path.join(root, 'electron/menu.js'), 'utf8');
const sandbox = { module: { exports: {} }, require: name => {
  if (name === 'node:path') return path;
  if (name === 'node:fs/promises') return {
    stat: async () => ({ isFile: () => true, size }),
    readFile: async () => Buffer.from('PK')
  };
  if (name === 'electron') return {
    BrowserWindow: { getFocusedWindow: () => window },
    Menu: { buildFromTemplate: x => x, setApplicationMenu: () => {} },
    dialog: { showErrorBox: () => errors++ }
  };
  throw Error(name);
}};
vm.runInNewContext(menuSource, sandbox);
await sandbox.module.exports.deliverChip('/mock.chip', window);
assert.equal(sent, 1, 'exact 100 MiB is accepted');
size++;
await sandbox.module.exports.deliverChip('/mock.chip', window);
assert.equal(sent, 1, 'over limit is not delivered');
assert.equal(errors, 1);
for (const file of ['frontend/home_hub.js', 'frontend/startup.js', 'frontend/app.js', 'electron/main.js']) {
  new vm.Script(await fs.readFile(path.join(root, file), 'utf8'), { filename: file });
}
console.log('PASS: native open boundary and scoped JavaScript syntax (mock-only)');
