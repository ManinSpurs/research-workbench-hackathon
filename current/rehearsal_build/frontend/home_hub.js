/* Home is another view of the existing project secretary, not another agent or store. */
(function () {
  'use strict';
  const tr = (key, vars) => window.WB_I18N ? window.WB_I18N.t(key, vars) : key;
  const byId = id => document.getElementById(id);
  const desktop = window.WB_DESKTOP;
  let chipOperation = false;
  window.WB_CHIP_BUSY = () => chipOperation;
  if (window.WB_I18N) window.WB_I18N.register('ko', {
    'Research Secretary': '연구 비서', 'Conversation Review': '대화 기록 모음',
    'Keep or remove past conversations': '지난 대화를 남기거나 삭제합니다',
    'Project': '프로젝트', 'Choose a project': '프로젝트 선택',
    'Welcome back.': '다시 오셨네요.',
    'Welcome back. Here is where we left off.': '다시 오셨네요. 지난 작업을 정리해 드릴게요.',
    '+{count} more': '+{count}건 더 있음',
    'Try the sample chip': '예제 칩 체험',
    'Continue this notebook': '이 노트 이어서 하기',
    'Imported: {title}': '가져온 노트: {title}',
    'Adopted': '채택', 'Deferred': '보류', 'Rejected': '기각',
    'Reason: {reason}': '이유: {reason}',
    'Waiting for your answer': '답을 기다리는 질문',
    'Next action': '다음 행동',
    'Create a project from this chip’s saved record? The record will be remembered on this device.': '이 칩의 저장 기록으로 프로젝트를 만들까요? 기록은 이 기기에 저장됩니다.',
    'Could not open this notebook. Check the chip and try again.': '이 노트를 열지 못했습니다. 칩을 확인한 후 다시 시도해 주세요.',
    'Creates a project from the chip’s saved record.': '칩의 저장 기록으로 프로젝트를 만듭니다.',
    'Welcome. Choose a project to continue, or open Research to start one.': '프로젝트를 선택하면 지난 작업을 이어갈 수 있습니다. 새 연구는 연구 메뉴에서 시작하세요.',
    'The saved briefing is not available yet.': '저장된 작업 요약을 아직 불러오지 못했습니다.',
    'Conversation history is unavailable. Your saved briefing is shown above.': '대화 기록을 불러오지 못했습니다. 위에는 저장된 작업 요약을 표시했습니다.',
    'Ask your secretary...': '비서에게 물어보세요...',
    'New Chip': '새 칩', 'Chip name': '칩 이름', 'Create': '만들기',
    'Start with File > New Chip… (⌘N) or File > Open… (⌘O).':
      '파일 > 새 칩 만들기… (⌘N) 또는 파일 > 열기… (⌘O)로 시작하세요.',
    'Open…': '열기…', 'New Chip…': '새 칩 만들기…',
    'Choose a chip smaller than 4 MiB.': '4 MiB보다 작은 칩 파일을 선택하세요.',
    'Wait for the current task to finish.': '현재 작업이 끝날 때까지 기다려 주세요.',
    'Resume this conversation': '이 대화 이어가기',
    'You have an unanswered clarification: {question}': '답변을 기다리는 확인 질문이 있습니다: {question}',
    'No research decisions have been recorded yet.': '아직 기록된 연구 결정이 없습니다.'
  });
  const host = byId('home-conversation');
  const briefingHost = document.createElement('div');
  briefingHost.className = 'home-conversation-briefing';
  host.appendChild(briefingHost);
  const selector = byId('home-project');
  const continueNotebook = document.createElement('button');
  continueNotebook.type = 'button'; continueNotebook.hidden = true;
  continueNotebook.dataset.testid = 'continue-notebook';
  const continueNote = document.createElement('p'); continueNote.hidden = true; continueNote.className = 'home-import-note';
  selector.after(continueNotebook, continueNote);
  function labelNotebook() {
    continueNotebook.textContent = tr('Continue this notebook');
    continueNote.textContent = tr('Creates a project from the chip’s saved record.');
  }
  labelNotebook();
  continueNotebook.onclick = async () => {
    if (S.busy || chipOperation || !window.confirm(tr('Create a project from this chip’s saved record? The record will be remembered on this device.'))) return;
    const pack = S.pack, epoch = S.epoch;
    chipOperation = true; continueNotebook.disabled = true;
    try {
      const imported = await call('POST', `/chips/${encodeURIComponent(pack)}/import`, {memory: 'remember'});
      if (S.pack !== pack || S.epoch !== epoch) return;
      await loadProjects();
      await openProject(imported.project_id, tr('Continue this notebook'), true);
      window.refreshHomeSecretary();
    } catch (_) { showError(tr('Could not open this notebook. Check the chip and try again.')); }
    finally { chipOperation = false; continueNotebook.disabled = false; }
  };
  const composer = byId('secretary-compose');
  const origin = document.createComment('Shared secretary composer');
  composer.before(origin);
  let projectRows = [], request = 0, timer, lastHtml = '', displayedScope = '', arrivalReply = null;
  const home = () => !byId('startup-menu').hidden;
  function bubble(text, role = 'secretary') {
    return `<div class="home-bubble home-bubble-${role}">${esc(text)}</div>`;
  }
  // `text` is the legacy field; a structured item without it is still a real item.
  function briefingShown(item) {
    return Boolean(item && (item.text || (item.kind && item.question)));
  }
  function briefingText(item) {
    if (item.kind && item.question) {
      const labels = {adopt: 'Adopted', defer: 'Deferred', reject: 'Rejected'};
      const label = item.kind === 'decision' ? labels[item.verdict]
        : item.kind === 'waiting' ? 'Waiting for your answer'
        : item.kind === 'next' ? 'Next action' : null;
      if (label) return `${tr(label)} · ${item.question}`
        + (item.reason ? `\n${tr('Reason: {reason}', {reason: item.reason})}` : '');
    }
    const text = item.text || item.question || '';
    const pending = /^Review clarification_pending: ([\s\S]*)$/.exec(text);
    if (pending) return tr('You have an unanswered clarification: {question}', { question: pending[1] });
    if (text === 'No decisions in this pack.' || text === 'No decisions yet.') return tr('No research decisions have been recorded yet.');
    return serverText(text);
  }
  function contextStill(pid, pack, epoch, serial) {
    return S.project === pid && S.pack === pack && S.epoch === epoch && serial === request;
  }
  async function refresh() {
    const serial = ++request, pid = S.project, pack = S.pack, epoch = S.epoch;
    const scope = `${pid || ''}/${pack}/${epoch}`;
    if (scope !== displayedScope) { briefingHost.replaceChildren(); lastHtml = ''; displayedScope = scope; }
    secretaryChatConversation();
    selector.disabled = S.busy || chipOperation;
    function projectLabel(name) {
      return name.startsWith('Imported: ') ? tr('Imported: {title}', {title: name.slice('Imported: '.length)}) : name;
    }
    selector.replaceChildren(new Option(tr('Choose a project'), ''), ...projectRows.map(p => new Option(projectLabel(p.name), p.id)));
    selector.value = pid || '';
    if (home() && pack) {
      try {
        const record = await call('GET', `/chips/${encodeURIComponent(pack)}/record`);
        if (!contextStill(pid, pack, epoch, serial)) return;
        const hasRecord = ['questions', 'answers', 'results', 'code', 'harness'].some(key => Array.isArray(record[key]) && record[key].length);
        const project = projectRows.find(row => row.id === pid);
        const imported = project && project.name.startsWith('Imported: ');
        continueNotebook.hidden = continueNote.hidden = !hasRecord || imported;
      } catch (_) { continueNotebook.hidden = continueNote.hidden = true; }
    }
    let html = '';
    if (!pid) {
      html = bubble(tr('Welcome. Choose a project to continue, or open Research to start one.'));
    } else {
      const parts = S.briefing && S.briefing.parts;
      if (home() && !S.cq && S.composerMode == null && parts && Array.isArray(parts.waiting_on_you)) {
        for (const item of parts.waiting_on_you) {
          const index = S.pending.findIndex(pending => pending.question_id === item.question_id);
          if (item.question_id && index >= 0) {
            const briefing = S.briefing;
            resumeClarification(index);
            S.briefing = briefing;
            arrivalReply = S.question;
            break;
          }
        }
      }
      html = bubble(tr('Welcome back. Here is where we left off.'));
      if (parts) {
        for (const [label, key] of [
          ['Where you left off', 'where_you_left_off'],
          ['Waiting on you', 'waiting_on_you'],
          ['Suggested next action', 'suggested_next_action']
        ]) {
          const items = (Array.isArray(parts[key]) ? parts[key] : []).filter(briefingShown);
          html += `<section class="home-brief-section home-brief-${key}"><h3>${esc(tr(label))}</h3>`
            + items.slice(0, 3).map(item => bubble(briefingText(item))).join('')
            + (items.length > 3 ? bubble(tr('+{count} more', {count: items.length - 3})) : '')
            + '</section>';
        }
      }
      if (!S.cq && S.pending.length) html += `<button type="button" data-home-resume>${esc(tr('Resume this conversation'))}</button>`;
      if (!parts) html += bubble(tr('The saved briefing is not available yet.'));
      try {
        const result = await call('GET', `/projects/${encodeURIComponent(pid)}/secretary-turns?pack_id=${encodeURIComponent(pack)}&limit=50`);
        if (!contextStill(pid, pack, epoch, serial)) return;
        renderSecretaryChatHistory(result.turns || []);
        if (S.cqPrompt && S.cqPrompt.gap && !(parts && (parts.waiting_on_you || []).some(item => item.question === S.cqPrompt.gap))) html += bubble(S.cqPrompt.gap + (S.cqPrompt.why_matters ? '\n' + S.cqPrompt.why_matters : ''));
      } catch (_) {
        if (!contextStill(pid, pack, epoch, serial)) return;
        html += bubble(tr('Conversation history is unavailable. Your saved briefing is shown above.'));
      }
    }
    if (!contextStill(pid, pack, epoch, serial)) return;
    if (html !== lastHtml) {
      const position = host.scrollTop;
      briefingHost.innerHTML = html; lastHtml = html;
      host.scrollTop = S.question && S.question !== arrivalReply ? host.scrollHeight : position;
    }
    if (secretaryChatView && secretaryChatView.scrollNewest) {
      scrollSecretaryChat();
      secretaryChatView.scrollNewest = false;
    }
    const error = byId('error');
    byId('home-error').textContent = error ? error.textContent : '';
    byId('secretary-input').placeholder = tr('Ask your secretary...');
    syncComposerMode();
    if (home()) {
      // The hint belongs to syncComposerMode(): it names the mode actually in
      // force (answer the waiting question, start a new one, or that a reply has
      // nowhere to go). An older line written here said "reply" over a control
      // set to "new", so home now only mirrors the title for the tooltip.
      const hint = byId('secretary-compose-hint'); hint.title = hint.textContent;
    }
  }
  window.refreshHomeSecretary = function (projects) {
    if (Array.isArray(projects)) projectRows = projects;
    clearTimeout(timer); timer = setTimeout(refresh, 40);
  };
  selector.addEventListener('change', async () => {
    const project = projectRows.find(p => p.id === selector.value);
    if (project && !S.busy && !chipOperation) { await openProject(project.id, project.name); window.refreshHomeSecretary(); }
  });
  host.addEventListener('click', async event => {
    if (event.target.closest('[data-home-resume]') && !S.busy) { resumeClarification(0); await loadBriefing(); window.refreshHomeSecretary(); }
  });
  function moveComposer() {
    if (home()) byId('home-composer-slot').appendChild(composer);
    else origin.after(composer);
    secretaryChatConversation();
    window.refreshHomeSecretary();
  }
  new MutationObserver(moveComposer).observe(byId('startup-menu'), { attributes: true, attributeFilter: ['hidden'] });
  new MutationObserver(() => window.refreshHomeSecretary()).observe(byId('error'), { childList: true, subtree: true, characterData: true });
  moveComposer();

  // Small, code-native pixel icons. They never substitute generated research evidence.
  const paths = {
    research: '<path d="M3 17V7h12v10H3Zm4-2v-4m4 4V9m5 6 4 4m-1-7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z"/>',
    library: '<path d="M5 3h11v14H5V3Zm3 4h5m-5 3h5M3 7H1v14h14v-2m4-12h3v14h-4"/>',
    review: '<path d="M2 3h14v10H8l-4 4v-4H2V3Zm5 4h6M19 8h3v12h-4v3l-4-3H9v-4"/>',
    writing: '<path d="M3 2h12v5M3 2v20h16V12M6 7h5M6 11h3m2 6 2-5 7-7 3 3-7 7-5 2Z"/>'
  };
  function icons() {
    document.querySelectorAll('#home-menu [data-menu]').forEach(tile => {
      if (tile.querySelector('.menu-icon')) return;
      const icon = document.createElement('span'); icon.className = 'menu-icon'; icon.setAttribute('aria-hidden', 'true');
      icon.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="miter" shape-rendering="crispEdges">${paths[tile.dataset.menu] || ''}</svg>`;
      tile.appendChild(icon);
    });
  }
  new MutationObserver(icons).observe(byId('home-menu'), { childList: true });
  icons();

  // Cartridge selection belongs to File > Open / New Chip, not a tray of fixed subjects.
  const idle = document.createElement('div'); idle.className = 'home-chip-idle';
  idle.innerHTML = '<span class="idle-chip-mark" aria-hidden="true">▥</span><strong>RESEARCH<br>WORKBENCH</strong>';
  // The packaged app starts on a fresh origin every launch, so this idle screen is
  // what a person always lands on. Chip selection lives in the native menu bar,
  // which is not something this screen said out loud: it offered no on-screen
  // action at all. The hint names the menu commands; in the desktop app two
  // buttons trigger those very same commands through the preload bridge, so the
  // buttons and the menu run one flow, not two.
  const idleHint = document.createElement('p'); idleHint.className = 'home-chip-hint';
  idle.appendChild(idleHint);
  // Both the hint and the two buttons are built once, so their words go through
  // this one function: the i18n listener below calls it again, and a live
  // language switch leaves no English behind on the idle screen.
  const idleButtons = [];
  function labelIdle() {
    idleHint.textContent = tr('Start with File > New Chip… (⌘N) or File > Open… (⌘O).');
    for (const entry of idleButtons) entry.node.textContent = tr(entry.key);
  }
  if (desktop) {
    const idleActions = document.createElement('div'); idleActions.className = 'home-chip-actions';
    const openButton = document.createElement('button'); openButton.type = 'button';
    openButton.setAttribute('data-testid', 'idle-open-chip');
    // desktop.openChip() is the same request the File > Open… item makes; the
    // main process runs openChipDialog + deliverChip, and chip_bridge routes the
    // result back through WB_CHIP_HOOK exactly as it does for the menu.
    openButton.addEventListener('click', () => { desktop.openChip(); });
    const newButton = document.createElement('button'); newButton.type = 'button';
    newButton.setAttribute('data-testid', 'idle-new-chip');
    idleButtons.push({ node: openButton, key: 'Open…' }, { node: newButton, key: 'New Chip…' });
    // desktop.newChip() sends the same wb:new-chip message File > New Chip… sends,
    // and the main process answers with chip:new, which chip_bridge forwards to
    // WB_CHIP_NEW_HOOK — the dialog opener, not a second creation path.
    newButton.addEventListener('click', () => { desktop.newChip(); });
    idleActions.appendChild(openButton); idleActions.appendChild(newButton);
    idle.appendChild(idleActions);
  }
  if (!desktop) {
    const file = document.createElement('button'); file.type = 'button';
    file.addEventListener('click', () => upload.click()); idle.appendChild(file);
    const create = document.createElement('button'); create.type = 'button';
    create.addEventListener('click', () => newChip.showModal()); idle.appendChild(create);
    idleButtons.push({ node: file, key: 'Open…' }, { node: create, key: 'New Chip…' });
  }
  labelIdle();
  const sampleButton = document.createElement('button'); sampleButton.type = 'button';
  sampleButton.dataset.testid = 'try-sample-chip';
  sampleButton.textContent = tr('Try the sample chip');
  sampleButton.onclick = async () => {
    if (chipOperation || S.busy) return;
    sampleButton.disabled = true;
    try {
      const data = await call('GET', '/chip-samples');
      await installChip(data.samples[0]);
    } catch (_) { chipError(tr('Could not open this notebook. Check the chip and try again.')); }
    finally { sampleButton.disabled = false; }
  };
  idle.appendChild(sampleButton);
  byId('startup-stage').appendChild(idle);
  const chipMessage = document.createElement('p'); chipMessage.setAttribute('role', 'alert'); idle.appendChild(chipMessage);
  const saveEject = document.createElement('button'); saveEject.type = 'button'; saveEject.textContent = tr('Save / Eject Chip…');
  byId('home-composer-slot').appendChild(saveEject);
  saveEject.onclick = async () => {
    if (S.busy || chipOperation) return;
    chipOperation = true; saveEject.disabled = true;
    const pack = S.pack, pid = S.project, epoch = S.epoch;
    try {
      const record = await call('GET', '/chips/' + encodeURIComponent(pack));
      if (record.source === 'builtin') throw new Error('Built-in chips are read-only examples, not writable notebooks.');
      const result = await call('POST', '/chips/export/' + encodeURIComponent(pack));
      if (S.pack !== pack || S.project !== pid || S.epoch !== epoch) throw new Error('Context changed. Save again from the active chip.');
      if (desktop) {
        if (!await desktop.saveChip(result)) return;
      } else {
        const bytes = Uint8Array.from(atob(result.base64), c => c.charCodeAt(0));
        const url = URL.createObjectURL(new Blob([bytes], { type: 'application/octet-stream' }));
        const link = document.createElement('a'); link.href = url; link.download = result.filename; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        // Browsers cannot confirm that the download was saved. Keep the chip selected.
        showError('Download requested. Chip remains selected because the browser cannot confirm saving.');
        return;
      }
      if (S.pack !== pack || S.project !== pid || S.epoch !== epoch) return;
      await window.WB_STARTUP.eject();
      S.epoch++; S.project = null; S.pack = ''; byId('pack-select').value = ''; S.pending = []; S.briefing = null; clearActive();
      localStorage.removeItem('wb.project'); localStorage.removeItem('wb.pack');
      window.refreshHomeSecretary();
    } catch (error) { showError(errorText(error.message)); }
    finally { chipOperation = false; saveEject.disabled = false; }
  };
  function idleState() {
    idle.hidden = byId('startup-choices').hidden;
  }
  new MutationObserver(idleState).observe(byId('startup-choices'), { attributes: true, attributeFilter: ['hidden'] });
  idleState();
  const upload = document.createElement('input'); upload.type = 'file'; upload.accept = '.chip'; upload.hidden = true;
  document.body.appendChild(upload);
  upload.addEventListener('change', async () => {
    const file = upload.files[0]; if (!file) return;
    if (file.size > 100 * 1024 * 1024) { chipError('Choose a chip no larger than 100 MiB.'); return; }
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = ''; for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
    await installChip({ base64: btoa(binary), filename: file.name }); upload.value = '';
  });
  function chipError(text) {
    chipMessage.textContent = text; idle.hidden = false;
  }
  async function installChip(payload, owned=false) {
    if (S.busy || (chipOperation && !owned)) { chipError(tr('Wait for the current task to finish.')); return false; }
    chipOperation = true;
    const epoch = S.epoch;
    const priorPack = S.pack;
    let accepted = false;
    try {
      // Opening validates bytes on the backend, including exact-match reopens.
      // A native path alone is never sufficient proof of a built-in identity.
      const result = await call('POST', '/chips/open', payload.base64
        ? { base64: payload.base64, filename: payload.filename }
        : { path: payload.path });
      const builtin = result.source === 'builtin';
      await loadDomainPacks();
      const pack = result.pack_id || (result.identity && result.identity.pack_id);
      if (!pack) throw new Error('The installed chip has no identity.');
      const members = await call('GET', '/chips/' + encodeURIComponent(pack) + '/projects');
      const record = await call('GET', '/chips/' + encodeURIComponent(pack) + '/record');
      const carriesRecord = ['questions', 'answers', 'results', 'code', 'harness'].some(key => Array.isArray(record[key]) && record[key].length);
      const workspace = builtin || carriesRecord ? null : await call('POST', '/chips/' + encodeURIComponent(pack) + '/workspace');
      if (S.epoch !== epoch || S.busy) throw new Error('Context changed. Open the chip again.');
      await window.WB_STARTUP.openPack(pack);
      if (S.epoch !== epoch || S.busy) throw new Error('Context changed. Open the chip again.');
      accepted = true;
      S.epoch++; S.project = null; S.pending = []; S.briefing = null; clearActive();
      localStorage.removeItem('wb.project');
      byId('pack-select').value = pack;
      if (S.pack !== pack) await changePack(true);
      await loadProjects();
      if (workspace) await openProject(workspace.project_id, workspace.name, true);
      else if ((members.projects || []).length === 1) await openProject(members.projects[0].id, members.projects[0].name, true);
      chipMessage.textContent = '';
      window.refreshHomeSecretary();
      return true;
    } catch (error) {
      if (!accepted && S.epoch === epoch) {
        byId('pack-select').value = priorPack;
        try { if (priorPack) localStorage.setItem('wb.pack', priorPack); else localStorage.removeItem('wb.pack'); } catch (_) {}
      }
      chipError(errorText(error.message));
      showError(errorText(error.message));
      if (owned) throw error;
      return false;
    }
    finally { chipOperation = false; }
  }
  window.WB_CHIP_HOOK = (_path, payload) => installChip(payload || {});
  const newChip = document.createElement('dialog'); newChip.className = 'new-chip-dialog';
  newChip.innerHTML = '<form><h2 data-i18n>New Chip</h2><label for="new-chip-name" data-i18n>Chip name</label><input id="new-chip-name" maxlength="120" required autocomplete="off"><p class="new-chip-error" role="alert"></p><div><button type="button" data-cancel data-i18n>Cancel</button><button type="submit" data-i18n>Create</button></div></form>';
  document.body.appendChild(newChip);
  newChip.querySelector('[data-cancel]').onclick = () => newChip.close();
  newChip.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    if (S.busy || chipOperation) return;
    const name = byId('new-chip-name').value.trim(); if (!name) return;
    chipOperation = true;
    const epoch = S.epoch;
    const submit = newChip.querySelector('[type="submit"]'); submit.disabled = true;
    try {
      const result = await call('POST', '/chips/build', { pack_id: 'chip_' + crypto.randomUUID().replace(/-/g, ''), version: '1.0.0', title: name,
        instructions: 'Help the researcher work within the chosen topic. Use registered evidence, preserve uncertainty, and leave decisions to the researcher.', required_context: [], checks: [], allowed_tools: [] });
      if (desktop) {
        const saved = await desktop.saveChip({ filename: result.filename, base64: result.base64 });
        if (!saved) return;
      } else {
        const link = document.createElement('a'); link.href = 'data:application/octet-stream;base64,' + result.base64; link.download = result.filename; link.click();
      }
      if (epoch !== S.epoch || S.busy) throw new Error('Context changed. Open the saved chip again.');
      if (await installChip(result, true)) newChip.close();
    } catch (error) { newChip.querySelector('.new-chip-error').textContent = errorText(error.message); }
    finally { chipOperation = false; submit.disabled = false; }
  };
  window.WB_CHIP_NEW_HOOK = () => { if (!S.busy && !chipOperation) { newChip.showModal(); byId('new-chip-name').focus(); } };
  if (window.WB_I18N) window.WB_I18N.onChange(() => { labelIdle(); labelNotebook(); sampleButton.textContent = tr('Try the sample chip'); saveEject.textContent = tr('Save / Eject Chip…'); window.refreshHomeSecretary(); });
  window.refreshHomeSecretary();
})();
