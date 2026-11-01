/* Read-only presentation of the existing workspace state. */
(function () {
  'use strict';
  const language = window.WB_I18N;
  const section = document.querySelector('#app-shell > main > section');
  const heading = document.getElementById('interior-question-title');
  const location = document.getElementById('research-location');
  const intro = document.getElementById('research-intro');
  const tools = document.getElementById('research-tools');
  const profile = document.getElementById('profile');
  const canvas = document.getElementById('canvas-panel');
  const project = document.getElementById('project-title');
  const translated = {
    'Start with a research question.': '연구 질문부터 시작하세요.',
    'Current research question': '현재 연구 질문',
    'Open a project to begin.': '프로젝트를 열어 시작하세요.',
    'Research tools': '연구 도구',
    'Data': '데이터',
    'Canvas': '캔버스',
    'Sources': '자료',
    'Code': '코드',
    'A code workspace is not available in this build.': '이 버전에는 코드 작업 공간이 없습니다.',
    'Inspect the registered data and evidence. A proposed design stays a proposal until you decide.':
      '등록한 데이터와 근거를 확인하세요. 제안된 설계는 직접 결정하기 전까지 제안으로 남습니다.'
  };
  language.register('ko', translated);
  function render() {
    const anotherView = ['writing-mode', 'library-mode', 'citation-mode', 'secretary-review-mode']
      .some(mode => section.classList.contains(mode));
    intro.hidden = anotherView;
    tools.hidden = anotherView;
    heading.textContent = S.question
      ? S.questionText || language.t('Current research question')
      : language.t('Start with a research question.');
    location.textContent = S.project ? project.textContent : language.t('Open a project to begin.');
    document.getElementById('research-tool-data').disabled = !S.project;
    document.getElementById('research-tool-sources').disabled = !S.project;
    document.getElementById('research-tool-canvas').disabled = canvas.classList.contains('hidden');
    document.getElementById('research-tool-code').disabled = true;
  }
  function showTool(tool) {
    // The Sources tool used to press the duplicated "Library" tab. That tab is
    // gone, so the route calls the view-opening function the tab called.
    if (tool === 'sources') { if (typeof openLibraryView === 'function') openLibraryView(); return; }
    if (window.WB_DECLUTTER_SHELL) window.WB_DECLUTTER_SHELL.openSupport();
    const target = tool === 'data' ? profile.closest('details') : canvas;
    target.open = true;
    target.scrollIntoView({ block: 'nearest' });
    target.querySelector('summary').focus({ preventScroll: true });
  }
  const baseControls = controls;
  controls = function () {
    const result = baseControls.apply(this, arguments);
    render();
    return result;
  };
  const observer = new MutationObserver(render);
  observer.observe(project, { childList: true, subtree: true });
  observer.observe(section, { attributes: true, attributeFilter: ['class'] });
  observer.observe(canvas, { attributes: true, attributeFilter: ['class'] });
  language.onChange(render);
  window.WB_INTERIOR_RESEARCH = { render, showTool };
  language.apply();
  render();
})();
