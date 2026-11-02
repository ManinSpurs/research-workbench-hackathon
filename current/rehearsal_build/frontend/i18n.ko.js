// Korean translation table.
//
// Keys are the authored English sentences from index.html and the render
// functions; values are what the researcher reads in Korean. The tone is a
// quiet research instrument: plain declarative Korean, no exclamation, no
// marketing. Technical names that a researcher meets elsewhere (PDF, LaTeX,
// CSV, JSON, API, USD, WAV) stay as they are, because translating them would
// hide which file or which number is meant.
//
// Sections mirror the screen: start, header, projects, question, canvas,
// decision, writing, library, secretary, advanced AI.
(function () {
  'use strict';
  if (!window.WB_I18N) return;
  window.WB_I18N.register('ko', {
    'Talk with the secretary': '비서와 대화하기',
    'Conversation model settings': '대화 모델 설정',
    'The compact project descriptor is included.': '간략한 프로젝트 설명도 포함됩니다.',
    'Secretary conversation model': '비서 대화 모델',
    'Local Qwen (free)': '로컬 Qwen (무료)',
    'Confirm model choice': '모델 선택 승인',
    'Selected model unavailable': '선택한 모델 사용 불가',
    'Local Qwen is free. Choosing local revokes paid conversation authorization.': '로컬 Qwen은 무료입니다. 로컬을 선택하면 유료 대화 승인이 취소됩니다.',
    'Recent conversation turns and the project briefing leave this device. Each reply has a calculated cost, not invoice-verified. Shared spend limit: {budget} USD. Confirm to authorize future conversation messages without per-message prompts. Switching to local revokes this authorization.': '최근 대화와 프로젝트 브리핑이 이 기기 밖으로 전송됩니다. 답변마다 청구서로 검증되지 않은 계산 비용이 발생합니다. 공유 지출 한도: {budget} USD. 승인하면 이후 메시지는 매번 묻지 않고 전송됩니다. 로컬로 전환하면 승인이 취소됩니다.',
    'Calculated cost: {cost} USD; not invoice-verified': '계산 비용: {cost} USD; 청구서 미검증',
    'The selected conversation model is unavailable. Switch models in Settings; nothing was sent to another provider.': '선택한 대화 모델을 사용할 수 없습니다. 설정에서 모델을 바꿔 주세요. 다른 제공자에게 전송하지 않았습니다.',
    'The conversation spend limit would be exceeded. Nothing was sent. Review the limit or switch to local Qwen in Settings.': '대화 지출 한도를 초과하게 되어 전송하지 않았습니다. 설정에서 한도를 확인하거나 로컬 Qwen으로 바꿔 주세요.',
    'Secretary conversation': '비서 대화',
    'Authorize local Qwen': '로컬 Qwen 사용 승인',
    'Revoke conversation authorization': '대화 사용 승인 취소',
    'The local model is off. Start the local model in Settings first.': '로컬 모델이 꺼져 있습니다. 먼저 설정에서 로컬 모델을 켜 주세요.',
    'Allow recent conversation turns and the project briefing to be sent to this local Qwen runtime? API cost is $0. Authorization lasts until this app session ends or the runtime changes.': '최근 대화와 프로젝트 브리핑을 이 로컬 Qwen 실행 환경으로 보내도록 승인하시겠습니까? API 비용은 $0입니다. 승인은 앱 세션이 종료되거나 실행 환경이 바뀔 때까지 유지됩니다.',
    'Talk with the secretary. Nothing is registered automatically.': '비서와 대화합니다. 아무것도 자동으로 등록되지 않습니다.',
    'The secretary is thinking…': '비서가 답변을 준비하고 있습니다…',
    'Register as research question': '연구 질문으로 등록',
    'Local compute: zero API cost': '로컬 실행: API 비용 없음',
    'The conversation could not be sent. Your message is back in the composer.': '대화를 보내지 못했습니다. 작성란에 메시지를 복원했습니다.',
    'non-year numeric constraints changed': '연도 이외의 숫자 조건이 변경되었습니다',
    'refined text is not a question': '다듬은 문장 끝에 물음표가 없습니다',
    'year bound in the original could not be parsed': '원문의 연도 범위를 해석하지 못했습니다',
    'Registered variable reference changed: {name}': '등록된 변수 참조가 변경되었습니다: {name}',
    'Kept years changed: original {original} vs refined {refined}': '포함할 연도가 변경되었습니다: 원문 {original}, 다듬은 문장 {refined}',
    'Exclusions changed: original {original} vs refined {refined}': '제외 조건이 변경되었습니다: 원문 {original}, 다듬은 문장 {refined}',
    'Save / Eject Chip…': '칩 저장 / 꺼내기…',
    'Startup sounds': '시작 효과음',
    'Attach to active chip': '현재 칩에 연결',
    'Local model (llama.cpp on this machine)': '로컬 모델 (이 컴퓨터의 llama.cpp)',
    'Local mode runs a small instruction model on this machine through a server you start yourself. It is text only, sends nothing to a cloud provider, and is not metered as API spend. Its endpoint, model alias and context size come from this machine\'s runtime environment, not from this window.': '로컬 모드는 직접 시작한 서버를 통해 이 컴퓨터에서 소형 지시 모델을 실행합니다. 텍스트만 처리하며 클라우드 제공자에게 아무것도 보내지 않고 API 지출로 집계하지 않습니다. 엔드포인트, 모델 별칭, 컨텍스트 크기는 이 창이 아니라 이 컴퓨터의 실행 환경에서 설정합니다.',
    'The readiness check sends one empty request to that local server: no research text, no files, no key, and no API cost. Until it succeeds the panel reports local as configured-but-not-ready, and the API model stays the active path.': '준비 상태 확인은 로컬 서버에 빈 요청을 한 번 보냅니다. 연구 텍스트, 파일, 키를 보내지 않으며 API 비용이 발생하지 않습니다. 확인에 성공하기 전까지 로컬 모델은 설정됨·준비 안 됨으로 표시되며 API 모델을 계속 사용합니다.',
    'Check local model readiness': '로컬 모델 준비 상태 확인',
    'Language, theme, session and research field. The same controls as Settings, not a second copy of them.': '언어, 테마, 세션, 연구 분야를 관리합니다. 설정과 같은 제어 항목이며 별도 사본이 아닙니다.',
    'Modal / Muse Glimmer-30B connection readiness': 'Modal / Muse Glimmer-30B 연결 준비 상태',
    'Separate Modal connection code is available, not a local model or a replacement for Spark. Live execution is disabled until an endpoint, GPU cost controls and explicit authorization are in place.': '별도 Modal 연결 코드가 준비되어 있습니다. 로컬 모델이나 Spark 대체가 아닙니다. 엔드포인트, GPU 비용 제한 및 명시적 승인이 갖춰질 때까지 실제 실행은 비활성화됩니다.',
    'Endpoint not deployed/configured. Configuration readiness only; runtime and account access have not been verified.': '엔드포인트가 배포/설정되지 않았습니다. 설정 준비 상태만 표시하며 실행 환경 및 계정 접근은 검증하지 않았습니다.',
    'Endpoint configured: {url}. Configuration readiness only; runtime and account access have not been verified.': '설정된 엔드포인트: {url}. 설정 준비 상태만 표시하며 실행 환경 및 계정 접근은 검증하지 않았습니다.',
    'Modal bills GPU time, not tokens. This is not free. SDK login does not establish inference readiness. No new key entry or account check is needed at this code-only stage.': 'Modal은 토큰이 아닌 GPU 사용 시간으로 과금하며 무료가 아닙니다. SDK 로그인만으로 추론 준비가 완료되지는 않습니다. 코드만 준비하는 이 단계에서는 새 키 입력이나 계정 확인이 필요하지 않습니다.',
    'Modal live execution is disabled. No account check or inference request was sent.': 'Modal 실제 실행은 비활성화되어 있습니다. 계정 확인이나 추론 요청을 보내지 않았습니다.',
    'Modal Shared / Muse Spark Open (token billed)': 'Modal 공유 / Muse Spark Open (토큰 과금)',
    'Modal Shared: endpoint not configured in this runtime. Configuration readiness only; nothing was sent.': 'Modal 공유: 이 실행 환경에는 엔드포인트가 설정되어 있지 않습니다. 설정 상태만 표시하며 아무것도 전송하지 않았습니다.',
    'This route uses a Modal-provided model API endpoint. It bills per token, not per GPU second, and is not free. Credits included with your Modal plan cannot be used for this route.': '이 경로는 Modal이 제공하는 모델 API 엔드포인트를 사용합니다. GPU 초가 아니라 토큰 단위로 과금되며 무료가 아닙니다. Modal 요금제에 포함된 크레딧은 이 경로에 쓸 수 없습니다.',
    'A separate promotional or hackathon grant may or may not cover this route. That depends on this account\'s own terms, and this app cannot check it. The allowance control in this panel is this app\'s own spending bookkeeping, not provider credit, so it does not measure such a grant.': '별도의 프로모션 또는 해커톤 지원금으로 이 경로의 비용이 충당될 수도 있고 아닐 수도 있습니다. 이는 해당 계정의 약관에 따르며 이 앱에서는 확인할 수 없습니다. 이 패널의 한도 설정은 이 앱이 스스로 기록하는 지출 관리 값이며 제공자 크레딧이 아니므로, 그런 지원금의 잔액을 대신하지 않습니다.',
    'Listing the route proves only that it is configured. The endpoint and remote model are set by this machine\'s runtime environment, not in this window, so do not change them here. Its key is entered in the shared key group below, once, like every other provider. The account check asks the backend to contact Modal once, on your click only, and sends no research material; opening this panel sends no account, metadata or inference request.': '경로가 목록에 보인다는 것은 설정되어 있다는 뜻일 뿐입니다. 엔드포인트와 원격 모델은 이 창이 아니라 이 컴퓨터의 실행 환경 변수로 설정하므로 여기서 바꾸지 마세요. 키는 다른 제공자와 마찬가지로 아래의 공용 키 영역에 한 번만 입력합니다. 계정 확인은 직접 누를 때만 백엔드가 Modal에 한 번 접속하며 연구 자료는 전송하지 않습니다. 이 패널을 여는 것만으로는 계정, 메타데이터, 추론 요청을 보내지 않습니다.',
    'Modal Shared API key': 'Modal 공유 API 키',
    'Save Modal Shared key': 'Modal 공유 키 저장',
    'Verify Modal Shared account': 'Modal 공유 계정 확인',
    'Modal Shared / Spark Open': 'Modal 공유 / Spark Open',
    'Independently enter a revisit time, or leave blank for no reminder.': '재검토 시각을 직접 입력하세요. 비워 두면 알림을 만들지 않습니다.',
    'Revisit time': '재검토 시각',
    'Entered in local timezone {zone}; saved as UTC.': '현지 시간대 {zone} 기준으로 입력하며 UTC로 저장합니다.',
    'Note (optional)': '메모 (선택)',
    'Record revisit time': '재검토 시각 기록',
    'Revisit reminders': '재검토 알림',
    'Ready to revisit': '재검토할 수 있습니다',
    'Reminder done': '알림 완료',
    'Revisit time recorded': '재검토 시각 기록됨',
    'Reminder only; the research decision is unchanged.': '알림만 관리합니다. 연구 결정은 바뀌지 않습니다.',
    'Done': '완료',
    'Reschedule': '시각 다시 지정',
    'Revisit reminders could not be loaded.': '재검토 알림을 불러오지 못했습니다.',
    'Enter your own revisit date and time. Nothing was saved.': '재검토 날짜와 시각을 직접 입력하세요. 저장하지 않았습니다.',
    'Reminder saved. The research decision is unchanged.': '알림을 저장했습니다. 연구 결정은 바뀌지 않았습니다.',
    'More': '더 보기',
    'Saving is in progress. Cancel cannot stop this server write. Please wait.': '저장 중입니다. 취소로 서버 저장을 중단할 수 없습니다. 잠시 기다려 주세요.',

    // --- start screen -----------------------------------------------------
    'Choose a research field': '연구 분야를 고르세요',
    'Choose a cartridge to open the workbench.': '카트리지를 골라 작업대를 여세요.',
    'Cancel and keep the current field': '취소하고 현재 분야를 유지합니다',

    // --- header -----------------------------------------------------------
    'Research Workbench': '연구 작업대',
    'Theme': '테마',
    'System': '시스템',
    'Light': '밝게',
    'Dark': '어둡게',
    'Language': '언어',
    'End session': '세션 종료',
    'Change research field': '연구 분야 바꾸기',
    'Text in this project\'s documents': '이 프로젝트 문서 안의 문구',
    'Write a note while it is fresh.': '기억이 생생할 때 메모를 남기세요.',
    'Jot a thought, a page number, a to-do.': '떠오른 생각, 페이지 번호, 할 일을 적어 두세요.',

    // --- projects ---------------------------------------------------------
    'Projects': '프로젝트',
    'Register NYC permit data': 'NYC 인허가 데이터 등록',
    'Register synthetic fixture': '합성 예제 데이터 등록',
    'Register local CSV + dictionary': '로컬 CSV와 사전 등록',
    'Project name (1–120 characters)': '프로젝트 이름 (1–120자)',
    'CSV file (up to 2 MiB / 50,000 data rows)': 'CSV 파일 (최대 2 MiB / 데이터 50,000행)',
    'JSON dictionary (up to 2,000 entries)': 'JSON 사전 (최대 2,000개 항목)',
    'Only the files you select are read. Their contents are copied to the local backend; originals stay unchanged. Registration does not fetch network sources.':
      '선택한 파일만 읽습니다. 내용은 로컬 백엔드로 복사되며 원본은 그대로 남습니다. 등록 과정에서 외부 네트워크에 접속하지 않습니다.',
    'Dictionary format': '사전 형식',
    'Each entry requires these five text fields; notes is optional.':
      '각 항목에는 아래 다섯 개의 텍스트 필드가 필요하며, notes는 선택입니다.',
    'Register selected files': '선택한 파일 등록',

    // --- surface ----------------------------------------------------------
    'No project open': '열린 프로젝트 없음',
    // The header's one way back, and the tooltip that explains its shortcut. The
    // label had no entry of its own; the title had none either, so the button read
    // English in a Korean window even though the rest of the header did not.
    'Home': '홈',
    'Return to the home menu (Escape)': '홈 메뉴로 돌아가기 (Esc)',
    'Research': '연구',
    'Writing': '집필',
    'Library': '라이브러리',
    'Citation Composer': '인용 작성기',
    'Compose a citation': '인용 작성',
    'The composer proposes a replacement sentence. Nothing is written into the paper until you approve it.':
      '작성기는 대체 문장을 제안할 뿐입니다. 승인하기 전에는 논문에 아무것도 쓰지 않습니다.',
    'The sentence you wrote': '작성한 문장',
    'Evidence that supports or limits it (registered source IDs)': '이 문장을 뒷받침하거나 한계를 보여주는 근거 (등록된 출처 ID)',
    'Separate several IDs with a comma. Only sources already registered for this project can be used.':
      '여러 개는 쉼표로 구분하세요. 이 프로젝트에 이미 등록된 출처만 사용할 수 있습니다.',
    'How it supports the sentence': '문장을 뒷받침하는 점',
    'What it does not support': '뒷받침하지 않는 점',
    'Proposed replacement': '제안하는 대체 문장',
    'TeX commands, values, equations and citation keys are kept exactly as you write them.':
      'TeX 명령·값·수식·인용 키는 작성한 그대로 보존됩니다.',
    'Propose': '제안하기',
    'Clear': '지우기',
    'Proposals': '제안 목록',
    'Settings': '설정',
    'Theme': '테마',
    'Session': '세션',
    'Close settings': '설정 닫기',
    'Expand surface': '작업 영역 넓히기',
    'Registered data profile (read-only)': '등록된 데이터 구성 (읽기 전용)',
    'Question and domain pack': '질문과 도메인 팩',
    'Ask about this dataset': '이 데이터에 대해 질문하기',
    'Domain pack for new questions': '새 질문에 적용할 도메인 팩',
    'Social Science 1.0.0': '사회과학 1.0.0',
    'Engineering: Measurement 1.0.0': '공학: 측정 1.0.0',
    'Core editing stays local. Optional Meta tasks are configured separately in Secretary. Existing questions retain their original pack.':
      '기본 편집은 로컬에서 처리됩니다. 선택 사항인 Meta 작업은 비서 패널에서 따로 설정합니다. 이미 만든 질문은 원래 팩을 그대로 유지합니다.',
    'Dataset for this question': '이 질문에 사용할 데이터셋',
    "Don't remember this question after this session": '이 질문은 세션이 끝나면 저장하지 않기',
    'Session-only text is stored on disk during this session, excluded from search and briefings, and deleted when you end the session or on the next backend startup after an unclean exit. Closing this browser tab alone does not end the backend session. External provider copies cannot be deleted here.':
      '세션 한정 텍스트는 이 세션 동안 디스크에 저장되며 검색과 브리핑에서 제외됩니다. 세션을 종료하거나, 비정상 종료 후 다음 백엔드 시작 때 삭제됩니다. 브라우저 탭만 닫는 것으로는 백엔드 세션이 끝나지 않습니다. 외부 제공자에 전송된 사본은 여기서 삭제할 수 없습니다.',
    'Ask': '질문하기',

    // --- canvas -----------------------------------------------------------
    'Research canvas: variables, gaps and evidence': '연구 캔버스: 변수, 공백, 근거',
    'Link variables to researcher-defined events or measurement components. A link is not verification. Arrows are relationships you assert yourself; none is inferred, timed or verified.':
      '변수를 연구자가 정의한 사건이나 측정 구성요소에 연결합니다. 연결은 검증이 아닙니다. 화살표는 연구자가 직접 주장한 관계이며, 자동으로 추론되거나 시점이 매겨지거나 검증된 것이 아닙니다.',
    'Edit event or measurement links': '사건·측정 연결 편집',
    'Save canvas draft': '캔버스 초안 저장',
    'Compare with previous saved draft': '이전 저장 초안과 비교',

    // --- decision ---------------------------------------------------------
    'Decision': '결정',
    'Reason (optional)': '이유 (선택)',
    'Adopt': '채택',
    'Defer': '보류',
    'Reject': '기각',
    'Legacy rehearsal comparison': '이전 방식 비교 실행',
    'Delete this question': '이 질문 삭제',

    // --- writing ----------------------------------------------------------
    'Paper draft': '논문 초안',
    'Open a project to start a draft.': '프로젝트를 열면 초안을 시작할 수 있습니다.',
    'LaTeX source · main file': 'LaTeX 원본 · 주 파일',
    'Save draft': '초안 저장',
    'Reload saved draft': '저장된 초안 다시 불러오기',
    'Compile': '컴파일',
    'Idle': '대기 중',
    'Compile sends the saved draft to the local backend (POST /drafts/{id}/compile) and shows only the PDF returned for the current revision. It stays disabled while no LaTeX engine is found on this machine.':
      '컴파일은 저장된 초안을 로컬 백엔드로 보내고(POST /drafts/{id}/compile), 현재 리비전에 대해 돌아온 PDF만 보여줍니다. 이 컴퓨터에서 LaTeX 엔진을 찾지 못하면 비활성 상태로 남습니다.',
    'About compiling local LaTeX': '로컬 LaTeX 컴파일에 대하여',
    'Compile runs in a temporary directory with a cleaned environment and shell escape off. That is not a container and cannot promise a hostile document fails to read another file on this machine. Diagnostics carry severity and line; select one to jump to the line.':
      '컴파일은 정리된 환경과 셸 이스케이프를 끈 상태로 임시 디렉터리에서 실행됩니다. 컨테이너가 아니므로, 악의적인 문서가 이 컴퓨터의 다른 파일을 읽지 못한다고 보장할 수 없습니다. 진단에는 심각도와 줄 번호가 붙으며, 선택하면 해당 줄로 이동합니다.',
    'PDF preview': 'PDF 미리보기',
    'No PDF yet.': '아직 PDF가 없습니다.',
    'Re-check for a TeX engine': 'TeX 엔진 다시 확인',
    'Korean text would need XeLaTeX plus the kotex package; that is not verified on this machine.':
      '한글 본문은 XeLaTeX와 kotex 패키지가 필요하며, 이 컴퓨터에서는 검증되지 않았습니다.',

    // --- library ----------------------------------------------------------
    'Folders': '폴더',
    'New folder': '새 폴더',
    'All documents': '모든 문서',
    'Search': '검색',
    'Rename folder': '폴더 이름 바꾸기',
    'First-pass screen': '1차 스크리닝',
    'Drop PDF files here, or choose files. A file dropped on a folder in the tree is filed into that folder.':
      'PDF 파일을 여기에 놓거나 파일을 선택하세요. 왼쪽 폴더에 파일을 놓으면 그 폴더로 들어갑니다.',
    'Select a document to read it, run a search, or start a first-pass screen.':
      '문서를 선택해 읽거나, 검색을 실행하거나, 1차 스크리닝을 시작하세요.',
    'New note in this folder': '이 폴더에 새 노트',
    'Save note': '노트 저장',
    'Notes are grouped by the day you wrote them. No date entry is needed.':
      '노트는 작성한 날짜별로 묶입니다. 날짜를 따로 입력하지 않아도 됩니다.',

    // --- secretary --------------------------------------------------------
    'Secretary': '비서',
    'Open a project to get a briefing.': '프로젝트를 열면 브리핑이 나타납니다.',
    'Ask a research question.': '연구 질문을 적어 주세요.',
    'Send': '보내기',
    'Scratch notes': '메모장',
    'Your own notes, not messages to the secretary.':
      '비서에게 보내는 메시지가 아니라, 나만 보는 기록입니다.',
    'Saved notes go to the Library, grouped by the day you wrote them.':
      '저장한 노트는 라이브러리로 들어가며, 작성한 날짜별로 묶입니다.',
    'Refresh briefing': '브리핑 새로 고침',
    'Saved questions and canvases': '저장된 질문과 캔버스',

    // --- secretary review list --------------------------------------------
    // The view's own title is the tile's title, so opening the tile never
    // renames the destination.
    'Conversation Review': '대화 기록 모음',
    'Past exchanges with the secretary, newest first. Keep what matters and delete the rest.':
      '지난 비서 대화를 최신순으로 보여 줍니다. 필요한 것은 남기고 나머지는 지우세요.',
    'Keeping a turn changes only its status. The recorded text is never rewritten.':
      '남기기는 상태만 바꿉니다. 기록된 본문은 다시 쓰지 않습니다.',
    'Delete all discarded': '버린 기록 모두 삭제',
    'Open a project to review its secretary exchanges.':
      '비서 기록을 검토하려면 프로젝트를 여세요.',
    'Open a project before reviewing secretary exchanges.':
      '비서 기록을 검토하려면 먼저 프로젝트를 여세요.',
    'No secretary turns recorded for this project yet.':
      '이 프로젝트에는 아직 기록된 비서 대화가 없습니다.',
    'Secretary review unavailable: {reason}.': '비서 기록을 불러올 수 없습니다: {reason}.',
    'the list could not be loaded': '목록을 불러오지 못했습니다',
    'Researcher': '연구자',
    'No chip': '칩 없음',
    'Kept': '남김',
    'Discarded': '버림',
    'Recorded': '기록됨',
    'Keep': '남기기',
    'Delete': '삭제',
    'Undated': '날짜 없음',
    'Your question': '내 질문',
    'No recorded text.': '기록된 본문이 없습니다.',
    'Kept. Only the status changed.': '남겼습니다. 상태만 바뀌었습니다.',
    'Not kept: {reason}.': '남기지 못했습니다: {reason}.',
    'Turn deleted.': '기록을 삭제했습니다.',
    'Not deleted: {reason}.': '삭제하지 못했습니다: {reason}.',
    'Delete {count} discarded turn{plural}? Kept turns are never deleted.':
      '버린 기록 {count}개를 삭제할까요? 남긴 기록은 삭제되지 않습니다.',
    'Nothing was deleted.': '아무것도 삭제하지 않았습니다.',
    'Deleted {count} discarded turn{plural}.': '버린 기록 {count}개를 삭제했습니다.',
    'Nothing was deleted: {reason}.': '아무것도 삭제하지 못했습니다: {reason}.',
    'Applies to this window.': '이 창에만 적용됩니다.',
    'The secretary turn list could not be loaded.': '비서 기록 목록을 불러오지 못했습니다.',
    'Loading secretary turns…': '비서 기록을 불러오는 중…',
    'Only the newest {limit} turns are listed here. Older turns are not shown.':
      '여기에는 최신 {limit}개 기록만 나옵니다. 더 오래된 기록은 표시되지 않습니다.',
    'Delete this kept turn? It is deleted for good and cannot be restored.':
      '남긴 기록을 삭제할까요? 완전히 삭제되며 되돌릴 수 없습니다.',
    'Delete every discarded turn in this project? At least {count} are discarded, and older turns beyond the newest {limit} are not listed here. Kept turns are never deleted.':
      '이 프로젝트에서 버린 기록을 모두 삭제할까요? 최소 {count}개가 버려졌으며, 최신 {limit}개를 넘는 오래된 기록은 여기에 표시되지 않습니다. 남긴 기록은 삭제되지 않습니다.',
    '{reported} The list could not be refreshed: {reason}.':
      '{reported} 목록을 새로 고치지 못했습니다: {reason}.',
    'Save conversation to chip': '대화를 칩에 저장',
    'No chip: the conversation cannot be saved': '칩 없음: 대화를 저장할 수 없습니다',
    'This group has no chip id, so it cannot save from here. Its turns are still written into a chip saved from another group of this project.':
      '이 묶음에는 칩 id가 없어서 여기서는 저장할 수 없습니다. 이 묶음의 기록은 이 프로젝트의 다른 묶음에서 칩에 저장할 때 함께 기록됩니다.',
    'This chip ships with the app, so it cannot be written to. Install a chip of your own to save a conversation.':
      '이 칩은 앱에 기본으로 들어 있어서 기록을 쓸 수 없습니다. 대화를 저장하려면 직접 설치한 칩을 쓰세요.',
    'The chip {chip} is not installed in this app, so there is no file to write into.':
      '칩 {chip}이(가) 이 앱에 설치되어 있지 않아 기록을 쓸 파일이 없습니다.',
    'The chip list could not be read, so saving is off until it loads: {reason}.':
      '칩 목록을 읽지 못해 불러올 때까지 저장할 수 없습니다: {reason}.',
    'the chip list could not be read': '칩 목록을 읽지 못했습니다',
    'Conversation saved into chip {chip}.': '대화를 칩 {chip}에 저장했습니다.',
    'Conversation saved into chip {chip} ({bytes} bytes).':
      '대화를 칩 {chip}에 저장했습니다 ({bytes} 바이트).',
    'The conversation was not saved: {reason}.': '대화를 저장하지 못했습니다: {reason}.',
    'The save may have completed: the chip now holds text. Check the chip before saving again ({reason}).':
      '저장이 되었을 수 있습니다. 칩에 글이 들어 있습니다. 다시 저장하기 전에 칩을 확인하세요 ({reason}).',

    // --- advanced AI ------------------------------------------------------
    'Advanced: optional Meta AI tasks': '고급: 선택 사항인 Meta AI 작업',
    'Cloud is off until enabled.': '활성화하기 전까지 클라우드는 꺼져 있습니다.',
    'Local: status not checked yet.': '로컬: 아직 상태를 확인하지 않았습니다.',
    'Access and allowance': '접근 권한과 한도',
    'Check account model access': '계정의 모델 접근 권한 확인',
    'Each provider keeps its own key in a private owner-only file outside this project. A key is written one way only: it is never shown again, never returned by the app, and never reused for another provider. An environment variable for that provider still wins over the saved file.':
      '각 제공자는 자기 키를 이 프로젝트 밖의 본인 전용 비공개 파일에 보관합니다. 키는 한 방향으로만 기록되며, 다시 표시되지 않고, 앱이 값을 돌려주지 않으며, 다른 제공자에 재사용되지 않습니다. 해당 제공자의 환경 변수가 있으면 저장된 파일보다 우선합니다.',
    'Meta API key': 'Meta API 키',
    'Baseten account: model listed.': 'Baseten 계정: 모델 확인됨.',
    'Baseten account: not verified.': 'Baseten 계정: 확인되지 않음.',
    'Baseten / Muse Spark Open': 'Baseten / Muse Spark Open',
    'Qwen3 4B Instruct (local)': 'Qwen3 4B Instruct (로컬)',
    'Save Meta key': 'Meta 키 저장',
    'Not checked.': '확인하지 않음.',
    'Baseten API key': 'Baseten API 키',
    'Save Baseten key': 'Baseten 키 저장',
    'Enable optional cloud tasks': '선택 사항인 클라우드 작업 활성화',
    'Local total API allowance (USD)': '로컬 API 총 한도 (USD)',
    'API keys stay in your local user environment or private key file. Credit balance is not available here.':
      'API 키는 이 컴퓨터의 사용자 환경이나 비공개 키 파일에 남습니다. 크레딧 잔액은 여기서 확인할 수 없습니다.',
    'Task definition': '작업 정의',
    'Task': '작업',
    'Model': '모델',
    'Automatic by task': '작업에 맞춰 자동 선택',
    'Meta-hosted models are unavailable until a key with credits is entered.': '크레딧이 있는 키를 입력할 때까지 Meta 호스팅 모델을 사용할 수 없습니다.',
    'Task instruction': '작업 지시문',
    'Apply research harness': '연구 하네스 적용',
    'Compare basic vs harness on the same model': '같은 모델에서 기본 방식과 하네스 비교',
    'Task maximum reservation (USD)': '작업 최대 예약 금액 (USD)',
    'Attach locally (8 MiB each)': '로컬에서 첨부 (파일당 8 MiB)',
    'Record a voice memo': '음성 메모 녹음',
    'Recorded in this browser as 16-bit PCM mono WAV at 16 kHz, which the validator accepts. Recording and playback stay on this machine; only an explicit Submit registers the file as a question asset.':
      '이 브라우저에서 16 kHz, 16비트 PCM 모노 WAV로 녹음하며 검증기가 허용하는 형식입니다. 녹음과 재생은 이 컴퓨터 안에서만 이루어지고, 직접 제출할 때만 질문 자료로 등록됩니다.',
    'Recorder idle.': '녹음기 대기 중.',
    'Record': '녹음',
    'Stop': '정지',
    'Submit recording': '녹음 제출',
    'Discard': '버리기',
    'Review and run': '검토 후 실행',
    'Register selected file locally': '선택한 파일을 로컬에 등록',
    'Preview transmission and cost': '전송 범위와 비용 미리보기',
    'Exact transmission scope': '정확한 전송 범위',
    'I approve this exact cloud transmission and maximum reservation':
      '이 전송 범위와 최대 예약 금액을 승인합니다',
    'Run approved task': '승인한 작업 실행'
  });

  // Task names shown in the task list, in the order the select offers them.
  window.WB_I18N.register('ko', {
    'Research query': '연구 질의',
    'Literature analysis': '문헌 분석',
    'Methodology review': '방법론 검토',
    'Cross-artifact review': '원고·코드 검토',
    'Interview transcription': '인터뷰 전사',
    'Image segmentation': '이미지 분할',
    'Generate concept image': '개념 이미지 생성',
    'Edit reference image': '참조 이미지 편집'
  });

  // Deletion report: what survived, said as the place a researcher would look
  // for it. The backend reports rows by table and text column; the screen names
  // the thing itself, so no internal store name and no raw JSON reaches the
  // reader. {count} is a number and {plural} is the English plural suffix, which
  // Korean does not use: an entry simply leaves it out instead of growing a
  // second sentence.
  window.WB_I18N.register('ko', {
    '{count} event payload{plural} redacted (rows kept)': '이벤트 페이로드 {count}건을 지웠습니다 (행은 유지)',
    '{count} spend-accounting row{plural} kept': '지출 기록 {count}건 유지',
    '{count} project draft{plural} kept (with versions, citations and compiles)':
      '프로젝트 초안 {count}건 유지 (버전, 인용, 컴파일 기록 포함)',
    '{count} draft{plural} on another question keep text quoting this question':
      '다른 질문의 초안 {count}건에 이 질문을 인용한 문구가 남습니다',
    '{count} revisit reminder{plural} kept': '다시 볼 조건 {count}건 유지',
    '{count} library note{plural} keep text quoting this question':
      '자료 노트 {count}건에 이 질문을 인용한 문구가 남습니다',
    '{count} kept record{plural} (kept by design)': '설계상 유지하는 기록 {count}건',
    'library note': '자료 노트',
    'LaTeX draft': 'LaTeX 초안',
    'LaTeX draft version': 'LaTeX 초안 버전',
    'LaTeX citation': 'LaTeX 인용',
    'LaTeX compile record': 'LaTeX 컴파일 기록',
    'revisit reminder': '다시 볼 조건',
    'spend-accounting row': '지출 기록',
    'event record': '이벤트 기록',
    'question record': '질문 기록',
    '{count} {what}{plural}': '{what} {count}건',
    'Deletion incomplete: the text is still stored in {places}. The question and its own records were removed.':
      '삭제가 완전히 끝나지 않았습니다. 문구가 아직 {places}에 남아 있습니다. 질문과 그 질문의 기록은 삭제했습니다.',
    'Deletion incomplete: some stored text could not be verified as removed. The question itself was removed.':
      '삭제가 완전히 끝나지 않았습니다. 일부 저장 문구가 지워졌는지 확인하지 못했습니다. 질문 자체는 삭제했습니다.',
    'Forensic erasure of database files is not performed.': '데이터베이스 파일까지 완전히 지우는 작업은 하지 않습니다.',
    // The delete-confirm pair: 'Confirm' already has Korean in app.js's own
    // block; 'Cancel' had none, so the destructive dialog offered one translated
    // button and one English one.
    'Cancel': '취소',
    // Claim–evidence verification (frontend/claim_check_ui.js). The distinction
    // this screen must never blur is the one between a verbatim match and a
    // model's opinion, so 'verified' is always '글자 그대로 확인' and the
    // model's verdict is always '모델의 판단'.
    'Claim–evidence verification': '주장–근거 검증',
    // The sheet's own opener/close pair. Without these the closed sheet — the one
    // line that is always on the Writing screen — stayed English in Korean mode.
    'Check a claim…': '주장 검증…',
    'Pick a sentence from your draft, choose the sources it should be checked against, then approve exactly what gets sent. The app checks the quotes word for word; the judgement of whether the claim is true stays with you.':
      '초안에서 문장을 고르고, 어떤 문헌과 대조할지 선택한 다음, 전송될 내용을 그대로 확인하고 승인하세요. 앱은 인용문이 글자 그대로 일치하는지만 확인하며, 그 주장이 참인지의 판단은 사용자에게 남습니다.',
    'Open a project to check a claim.': '주장을 검증하려면 프로젝트를 여세요.',
    'Choose what to check': '무엇을 검증할지 고르기',
    'Free — no model is called': '무료 — 모델을 호출하지 않습니다',
    'Pick the sentence you want to check and the sources it should be checked against. This step only reads what is already in the project.':
      '검증할 문장과 대조할 문헌을 고르세요. 이 단계는 프로젝트에 이미 있는 내용만 읽습니다.',
    'The claim': '주장 문장',
    'Select a sentence in the draft, or type the claim here.': '초안에서 문장을 선택하거나 여기에 주장을 입력하세요.',
    'Use the sentence selected in the draft': '초안에서 선택한 문장 사용',
    'Select a sentence in the draft first.': '먼저 초안에서 문장을 선택하세요.',
    'Check it against these sources': '이 문헌들과 대조',
    'This project has no sources yet. Add PDFs in Literature Review, then come back.': '이 프로젝트에는 아직 문헌이 없습니다. 문헌 검토에서 PDF를 추가한 뒤 다시 오세요.',
    'Untitled document': '제목 없는 문서',
    'table': '표',
    'note': '메모',
    'Compare with a table (optional)': '표와 비교 (선택)',
    'No table': '표 없음',
    'Untitled table': '제목 없는 표',
    'This step reads the project on this machine. Nothing is sent anywhere until you approve the preview in step 3.': '이 단계는 이 컴퓨터의 프로젝트만 읽습니다. 3단계에서 미리보기를 승인하기 전에는 어디로도 전송되지 않습니다.',
    'Preview what will be sent': '전송될 내용 미리보기',
    'Preparing the preview…': '미리보기 준비 중…',
    'Checking…': '검증 중…',
    'Recording your decision…': '결정 기록 중…',
    '{n} source(s) selected.': '문헌 {n}개 선택됨.',
    'Type or select a claim to continue.': '계속하려면 주장을 입력하거나 선택하세요.',
    'The numbers in this claim': '이 주장에 나온 숫자',
    'Checked by ordinary code against the source text. A matching number does not by itself make the claim true.': '일반 코드로 문헌 본문과 대조한 결과입니다. 숫자가 일치한다고 해서 그 주장이 참이 되는 것은 아닙니다.',
    'Appears in the sources': '문헌에 있음',
    'Appears, but not exactly': '비슷하게 있음',
    'Not found in the sources': '문헌에서 찾지 못함',
    'Exactly what will be sent': '전송될 내용 그대로',
    'Why this cannot run': '실행할 수 없는 이유',
    'Cloud use is off, so this check cannot run.': '클라우드 사용이 꺼져 있어 이 검증은 실행할 수 없습니다.',
    'The local model is not ready, so this check cannot run.': '로컬 모델이 준비되지 않아 이 검증을 실행할 수 없습니다.',
    'Start the local model server, then prepare the preview again. Nothing was sent, and no API cost was reserved.': '로컬 모델 서버를 시작한 뒤 미리보기를 다시 준비하세요. 아무것도 전송되지 않았고 API 비용도 예약되지 않았습니다.',
    'This check cannot run right now.': '지금은 이 검증을 실행할 수 없습니다.',
    'Nothing was sent. Resolve the obstacle and prepare the preview again.': '아무것도 전송되지 않았습니다. 문제를 해결한 뒤 미리보기를 다시 준비하세요.',
    'This account is not ready for that model, so this check cannot run.': '이 계정은 해당 모델을 사용할 준비가 되지 않아 이 검증을 실행할 수 없습니다.',
    'Turn cloud use on in Settings, and make sure an API key is saved for that provider. Nothing was sent.': '설정에서 클라우드 사용을 켜고 해당 제공자의 API 키가 저장되어 있는지 확인하세요. 아무것도 전송되지 않았습니다.',
    'Check the key and the model in the Secretary AI panel, then prepare the preview again. Nothing was sent.': '비서 AI 패널에서 키와 모델을 확인한 뒤 미리보기를 다시 준비하세요. 아무것도 전송되지 않았습니다.',
    'Sent to': '전송 대상',
    'unknown': '알 수 없음',
    'Maximum reserved cost': '최대 예약 비용',
    'Output tokens allowed': '허용 출력 토큰',
    'not reported': '보고되지 않음',
    'Reasoning': '추론',
    'none requested': '요청하지 않음',
    'Provider keeps a copy': '제공자가 사본 보관',
    'yes': '예',
    'no': '아니요',
    'Tools offered': '제공되는 도구',
    'none': '없음',
    'Data use': '데이터 사용',
    'Your claim, word for word': '당신의 주장, 글자 그대로',
    '{n} page(s)': '{n}쪽',
    'p. {n}': '{n}쪽',
    '(cut to fit the window)': '(전송 한도에 맞게 잘림)',
    'A table ({id}) is included.': '표({id})가 포함됩니다.',
    'How the answer will be checked': '답변이 검증되는 방식',
    // The two prose fields the preview returns as a stable code: the code picks the
    // sentence here, and the thresholds are filled from the numbers the response
    // carries, so nothing is parsed out of English.
    'Local compute is not metered by this app, so the reserved amount is exactly zero. That is a zero-API-cost fact, not a claim about electricity or hardware.': '이 앱은 로컬 연산에 요금을 매기지 않으므로 예약 금액은 정확히 0입니다. 이는 API 비용이 0이라는 사실일 뿐, 전기나 하드웨어 비용에 대한 이야기가 아닙니다.',
    'Conservative reservation, not a predicted bill: the full documented context is reserved at the uncached rate. Actual usage is settled when it is reported.': '예상 청구서가 아니라 보수적인 예약 금액입니다. 문서화된 전체 문맥을 캐시 미적용 요율로 예약합니다. 실제 사용량은 보고될 때 정산됩니다.',
    'Every returned quote is checked verbatim against the excerpt text actually transmitted above, with Unicode NFKC normalization and whitespace collapsing only; a quote that occurs only outside the transmitted excerpt never counts. A quote counts as evidence only if it is at least {words} words, or at least {chars} non-space characters when it has no spaces; a shorter verbatim quote does not count either.': '돌려받은 인용문은 위에서 실제 전송된 발췌문과 글자 그대로 대조하며, 유니코드 NFKC 정규화와 공백 축약만 적용합니다. 전송된 발췌문 밖에만 있는 인용문은 인정되지 않습니다. 인용문은 {words}단어 이상이거나, 띄어쓰기가 없을 때 {chars}자(공백 제외) 이상일 때만 근거로 인정되며, 더 짧은 인용문은 글자 그대로 일치해도 인정되지 않습니다.',
    'Send this and check it': '이대로 보내고 검증하기',
    'This sends the text above and reserves the maximum above. It cannot be called back once sent.': '위 내용을 전송하고 위의 최대 금액을 예약합니다. 한 번 전송하면 되돌릴 수 없습니다.',
    'No request can be made until the reason above is resolved.': '위 사유가 해결되기 전에는 요청을 보낼 수 없습니다.',
    // A refused send, and the way back from it. The lead is ours; the backend's
    // own sentence follows it as the detail.
    'This check was refused, so nothing was sent.': '이 검증은 거부되었으므로 아무것도 전송되지 않았습니다.',
    'What the preview described is no longer what the app will send. Prepare the preview again and approve the current version.': '미리보기가 설명한 내용과 앱이 보낼 내용이 더 이상 같지 않습니다. 미리보기를 다시 준비하고 현재 내용을 승인하세요.',
    'Prepare again': '다시 준비',
    'Nothing was sent, so nothing was charged.': '아무것도 전송되지 않았으므로 비용도 청구되지 않았습니다.',
    // Which kind of check a row in the list is. The list holds both, so a row
    // that stayed silent would claim every check came from the draft.
    'from the draft': '초안에서',
    'typed': '직접 입력',
    'What the check found': '검증 결과',
    'This check ended without a result.': '이 검증은 결과 없이 끝났습니다.',
    'Code: {code}': '코드: {code}',
    'There is nothing to accept or override. The record stays here, and the spend entry keeps its reservation.': '수용하거나 뒤집을 결과가 없습니다. 기록은 여기 남고, 지출 항목의 예약 금액은 그대로 유지됩니다.',
    'This result is stale.': '이 결과는 오래된 것입니다.',
    'It was computed before {reasons}. The verdict below describes the older text, not what is on screen now. Check the claim again to compare the current version.': '{reasons} 이후에 계산된 결과입니다. 아래 판단은 지금 화면의 내용이 아니라 그 이전 내용에 대한 것입니다. 현재 내용과 비교하려면 다시 검증하세요.',
    'your sentence changed after this check': '이 검증 이후 문장이 바뀜',
    'a source changed after this check': '이 검증 이후 문헌이 바뀜',
    'a source this check used is gone': '이 검증에 쓰인 문헌이 사라짐',
    'the table changed after this check': '이 검증 이후 표가 바뀜',
    'the table is gone': '표가 사라짐',
    'the draft this claim was taken from is gone': '이 주장을 가져온 초안이 사라짐',
    'Downgraded from "{was}".': '"{was}"에서 낮춰 표시했습니다.',
    "No quote was found verbatim in the text that was sent, so the model's verdict is not backed by evidence here.": '전송된 본문에서 글자 그대로 일치하는 인용문을 찾지 못했습니다. 따라서 모델의 판단을 뒷받침하는 근거가 여기에는 없습니다.',
    'It did not match the text that was sent.': '전송된 본문과 일치하지 않았습니다.',
    'Not found anywhere in this source.': '이 문헌 어디에도 없습니다.',
    'Found in the source, but outside the part that was sent.': '문헌에는 있지만 전송된 부분 밖에 있습니다.',
    'The cited page is not in this source.': '인용한 쪽이 이 문헌에 없습니다.',
    'The answer named a source that was not sent.': '답변이 전송되지 않은 문헌을 가리켰습니다.',
    'The answer returned an empty quote.': '답변이 빈 인용문을 돌려주었습니다.',
    'Too short to count (a quote needs 5 words, or 20 characters if it has no spaces).': '너무 짧아 인정되지 않습니다 (인용문은 5단어, 띄어쓰기가 없으면 20자 이상이어야 합니다).',
    'This quote spans a page break; the two pages were checked as one span.': '이 인용문은 쪽 경계에 걸쳐 있습니다. 두 쪽을 하나로 이어서 확인했습니다.',
    'The answer cited this across a page break. It is still not verified.': '답변이 쪽 경계에 걸쳐 인용했습니다. 여전히 확인되지 않았습니다.',
    'The answer came back without any quotes, so there is nothing to check word for word.': '답변에 인용문이 없어 글자 그대로 대조할 것이 없습니다.',
    // --- the secretary's voice -------------------------------------------
    // "Secretary's note" is already translated in app.js (the briefing's own
    // note card); the same phrase here means the same thing, so it is not
    // redefined — a duplicate would silently override that one.
    'What I checked': '제가 확인한 것',
    'That\'s my reading — you know this work best.': '제 생각은 이렇습니다. 판단은 연구자님께 맡길게요.',
    'What the model wrote': '모델이 쓴 내용',
    'Leans: supported': '지지하는 쪽으로 보임',
    'Leans: partly supported': '부분적으로 지지하는 쪽으로 보임',
    'Leans: contradicted': '반박하는 쪽으로 보임',
    'Leans: not addressed': '다루지 않는 쪽으로 보임',
    'Leans: too little to go on': '판단하기에 근거가 적어 보임',
    'Found word for word in {title}, {page}': '{title}, {page}에서 글자 그대로 찾았습니다',
    'Found word for word in {title}': '{title}에서 글자 그대로 찾았습니다',
    'Found word for word in the source you sent, {page}': '보내주신 문헌의 {page}에서 글자 그대로 찾았습니다',
    'Found word for word in the source you sent.': '보내주신 문헌에서 글자 그대로 찾았습니다.',
    'I couldn\'t find this sentence in your sources.': '이 문장은 보내주신 문헌에서 찾지 못했습니다.',
    'p.{n}': '{n}쪽',
    // The particle sits on 숫자, a fixed noun, instead of on the number: a
    // bracketed "은(는)" gloss is not Korean, and picking 은 or 는 from the last
    // digit would put a grammar rule inside a sentence template. The number stays
    // in parentheses, so every value reads correctly — 10300 included.
    'One thing: the {number} in your sentence doesn\'t quite match the table ({cell}).': '한 가지, 문장에 쓴 숫자({number})가 표의 값({cell})과 조금 다릅니다.',
    'One thing: the {number} in your sentence doesn\'t quite match the table.': '한 가지, 문장에 쓴 숫자({number})가 표의 값과 조금 다릅니다.',
    'I tried to back this up, but I couldn\'t find the sentences I was going to quote in your sources, so I\'d hold off on this one for now.': '이 주장을 뒷받침하려고 했는데, 인용하려던 문장을 보내주신 문헌에서 찾지 못했습니다. 그래서 이번 건은 조금 미루시는 편이 좋겠습니다.',
    'I looked through the sources you picked but couldn\'t find anything that speaks to this claim. Want to try adding another source?': '골라 주신 자료에서는 이 주장과 관련된 내용을 찾지 못했어요. 다른 자료를 더 골라 보시겠어요?',
    'I looked at this one but I don\'t have much to add beyond the checks below — nothing here stands out to me either way.': '이 건도 살펴봤는데, 아래 확인 결과 말고는 딱히 덧붙일 것이 없습니다. 어느 쪽이라고 단정하기는 어렵습니다.',
    'The answer cited this across a page break. I still couldn\'t find it as written.': '답변이 쪽 경계에 걸쳐 인용했습니다. 그래도 적힌 그대로는 찾지 못했습니다.',
    'Korean': '한국어',
    'English': '영어',
    'Note language': '메모 언어',
    'Your decision': '내 결정',
    'Your call': '연구자님의 판단',
    'Keeping my note records that you agree with it. Telling me you see it differently records why, and the note itself is never rewritten.': '제 메모를 그대로 두시면 동의하신 것으로 기록됩니다. 다르게 보신다면 그 이유가 기록되며, 메모 자체는 다시 쓰이지 않습니다.',
    'Reason (required to override)': '이유 (다르게 볼 때 필수)',
    'Why you decided this': '그렇게 보신 이유',
    'Keep my note': '메모 그대로 두기',
    'I see it differently': '다르게 봅니다',
    'You see it differently — your reason is on record.': '다르게 보셨고, 그 이유가 기록되었습니다.',
    'You kept my note.': '제 메모를 그대로 두셨습니다.',
    'Tell me why you see it differently — the reason is required.': '왜 다르게 보시는지 적어주세요. 이유는 필수입니다.',
    'Open the trust receipt': '신뢰 영수증 열기',
    'Earlier checks in this paper': '이 원고의 이전 검증',
    'stale': '오래됨',
    'overridden': '뒤집힘',
    'accepted': '수용됨',
    'Could not prepare the check.': '검증을 준비하지 못했습니다.',
    'The app said: {detail}': '앱의 메시지: {detail}',
    'Could not run the check.': '검증을 실행하지 못했습니다.',
    'An override needs a reason saying why you disagree.': '뒤집으려면 왜 동의하지 않는지 이유를 적어야 합니다.',
    'Could not record the decision.': '결정을 기록하지 못했습니다.',
    'Could not read the receipt.': '영수증을 읽지 못했습니다.',
    'Trust receipt': '신뢰 영수증',
    'Close': '닫기',
    'Outcome: {outcome}': '결과: {outcome}',
    'Waiting for your consent': '승인 대기 중',
    'Running': '실행 중',
    'Checked': '검증됨',
    'Failed': '실패',
    'Failed — charge unknown': '실패 — 청구 금액 알 수 없음',
    'Interrupted — charge unknown': '중단됨 — 청구 금액 알 수 없음',
    'Prepared, not sent. Nothing has been transmitted or charged yet.': '준비만 되었고 전송되지 않았습니다. 아직 전송되거나 청구된 것이 없습니다.',
    'The request is with the provider. This screen cannot stop it once it has started.': '요청이 제공자에게 전달되었습니다. 시작된 뒤에는 이 화면에서 멈출 수 없습니다.',
    'The result arrived, but the provider did not report its cost. The charge is unknown.': '결과는 도착했지만 제공자가 비용을 보고하지 않았습니다. 청구 금액을 알 수 없습니다.',
    'Stopped: the provider used more than the reserved maximum': '중단됨: 제공자가 예약한 최대치보다 많이 사용했습니다',
    'The run cost more than the amount the preview reserved. The result was not kept, and the spend ledger keeps the reservation. Nothing was retried automatically.': '실행 비용이 미리보기에서 예약한 금액을 넘었습니다. 결과는 보관하지 않았고, 지출 기록의 예약 금액은 유지됩니다. 자동으로 다시 시도하지 않았습니다.',
    'Interrupted while running — the charge is unknown': '실행 중 중단됨 — 청구 금액 알 수 없음',
    'The app stopped while this check was running. The request may have reached the provider, so the charge is unknown and the reservation is kept. There is nothing to accept or override here; prepare a new check if you want one.': '이 검증이 실행되는 동안 앱이 종료되었습니다. 요청이 제공자에게 도달했을 수 있어 청구 금액을 알 수 없고 예약 금액은 유지됩니다. 여기에는 수용하거나 뒤집을 결과가 없습니다. 필요하면 새로 검증을 준비하세요.',
    'Job status': '작업 상태',
    'Outcome': '결과 구분',
    'Reservation kept (USD)': '유지되는 예약 금액(USD)',
    'Model': '모델',
    'Provider': '제공자',
    'Data use': '데이터 사용',
    'Result integrity': '결과 무결성',
    'Error code': '오류 코드',
    'Check': '검증',
    'Recorded at': '기록 시각'
  });
})();
