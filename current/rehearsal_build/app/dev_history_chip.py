"""Public-safe development log of this app, packaged as a chip notebook.

Importing it shows the secretary's return briefing for the build itself: the
decisions taken day by day with their reasons, what is waiting on the researcher,
and the next steps. Costs are calculated, not invoice-verified.
"""
from . import chips
from .packs import PACKS

PACK_ID = "dev_history_notebook"

# (question_id, date, question, verdict, reason); verdict None = waiting on the researcher.
ENTRIES = [
    ("secretary-core", "2026-10-26", "Should the secretary's return briefing be the core of the app?", "adopt",
     "Opening a project shows the last decisions with their reasons, what is waiting, and one next "
     "action. Model calls are optional tools, never the centre."),
    ("review-depth", "2026-10-26", "How much review does a change need?", "adopt",
     "Core features (cost settlement, secrets, model routes) get worker tests, a reviewer and an "
     "independent second check; ordinary features get one reviewer."),
    ("menus", "2026-10-27", "Which menus does the home screen offer?", "adopt",
     "Four, in this order: Literature Review, Research, Writing, Conversation Review. Settings stay small."),
    ("chips", "2026-10-27", "Should the app start like a game console with insertable chips?", "adopt",
     "A chip carries one field's instruction pack and its research notebook; inserting it opens that "
     "field's work."),
    ("revisit", "2026-10-28", "Can a deferred question come back on a date without a model call?", "adopt",
     "A revisit date re-shows the item in the briefing from stored records only."),
    ("interior", "2026-10-29", "How should the research screen be laid out?", "adopt",
     "Question, evidence and next action are grouped so they can be compared at a glance. The mockup "
     "was approved before the app changed."),
    ("local-model", "2026-10-30", "Which local model fits this Mac?", "adopt",
     "Qwen3-4B-Instruct-2507 (Q4_K_M, about 2.5 GB); a 27B model did not fit the free disk. The app "
     "starts and stops its own local server, and local answers cost no API credits."),
    ("claim-check", "2026-10-31", "What is the core feature of the hackathon build?", "adopt",
     "The claim-evidence check: numbers reconciled by code, quotes matched verbatim against the excerpt "
     "sent, consent before any model call. The result is a secretary's note, never a ruling."),
    ("effort", "2026-10-31", "Keep cloud reasoning effort low, or switch to none?", "adopt",
     "Keep low. The 0.45 s median gap sat inside run-to-run spread, and quality was equal."),
    ("scoring", "2026-11-01", "How are comparison answers scored?", "adopt",
     "AI first-pass scoring, then human second review after unblinding. AI scores are never presented "
     "as human scores."),
    ("harness-effect", "2026-11-01", "Does the harness make answers better?", "defer",
     "Round 3 (10 questions, 3 providers) is only an indication: local Qwen 4/10 without and 6/10 with "
     "the harness; Baseten 9/10 both; Modal 7/10 both. One repetition, correlated datasets, and the "
     "human review is still pending."),
    ("look", "2026-11-01", "Adopt the macOS 27-style look?", "adopt",
     "Glass only on the toolbar, sidebars and secretary panel; research content stays solid, with a "
     "solid fallback when transparency is reduced."),
    ("glimmer", "2026-11-01", "Run Glimmer 30B on a rented GPU?", "reject",
     "GPU use needs a payment method on the provider account; the researcher chose not to use it. The "
     "Glimmer row stays 'not deployed'."),
    ("release", "2026-11-01",
     "When should the repository be committed, published and submitted (deadline Nov 4, 21:00 ET)?",
     None, ""),
    ("human-review", "2026-11-01",
     "Round 3 human second review: confirm or revise the AI first-pass marks on the 56 retained answers?",
     None, ""),
]

NEXT = [
    ("Demo video", "Record the 3 minutes: open this chip, show the briefing, then one claim check with its receipt."),
    ("Invoices", "Reconcile provider invoices against the calculated spend (Baseten about $0.21; Modal up to $4.17)."),
]

CONVERSATION = """PUBLIC-SAFE DEVELOPMENT LOG of Research Workbench. Costs are calculated, not invoice-verified.
2026-10-26 Researcher: The secretary is the core. When I come back, tell me where I stopped.
2026-10-26 Secretary: Saved. The return briefing leads; model settings fold away.
2026-10-27 Researcher: Make it feel like a game console. A chip per field.
2026-10-27 Secretary: Four menus fixed: Literature Review, Research, Writing, Conversation Review.
2026-10-28 Secretary: Deferred questions can now come back on a date, with no model call.
2026-10-30 Secretary: Local Qwen3-4B installed and wired; the app starts and stops it itself.
2026-10-31 Secretary: Claim-evidence check is live on the local and cloud paths. First cloud check: 3.4 s, about $0.004.
2026-11-01 Secretary: Round 3 compared 3 providers on 10 questions. AI first-pass scores are in; your second review is next.
2026-11-01 Researcher: Skip Glimmer.
2026-11-01 Secretary: Noted. Waiting on you: the human review, and when to commit, publish and submit.
"""


# Korean edition: same ids, dates and verdicts; only the words differ.
KO = {
    "secretary-core": ("비서의 복귀 브리핑을 앱의 중심으로 둘까요?",
                       "프로젝트를 열면 지난 결정과 이유, 기다리는 일, 다음 행동 하나를 먼저 보여 줍니다. "
                       "모델 호출은 선택 도구일 뿐 중심이 아닙니다."),
    "review-depth": ("변경 하나에 검토를 얼마나 할까요?",
                     "핵심 기능(비용 정산, 비밀 정보, 모델 연결)은 작업자 검사, 검토자, 독립 재확인을 거치고 "
                     "일반 기능은 검토자 한 번을 거칩니다."),
    "menus": ("홈 화면 메뉴는 무엇으로 할까요?",
              "네 개, 이 순서로: 문헌 검토, 연구, 집필, 대화 기록 모음. 설정은 작게 둡니다."),
    "chips": ("게임기처럼 칩을 꽂아 시작하는 앱으로 만들까요?",
              "칩 하나에 분야별 지침과 연구 노트가 들어 있고, 꽂으면 그 분야 작업이 열립니다."),
    "revisit": ("보류한 질문을 모델 호출 없이 정한 날짜에 다시 띄울 수 있을까요?",
                "재검토 날짜가 되면 저장된 기록만으로 브리핑에 다시 보여 줍니다."),
    "interior": ("연구 화면은 어떻게 배치할까요?",
                 "질문, 근거, 다음 행동을 한데 묶어 한눈에 비교하게 했습니다. 시안을 먼저 승인받고 앱을 바꿨습니다."),
    "local-model": ("이 맥에 맞는 로컬 모델은 무엇일까요?",
                    "Qwen3-4B-Instruct-2507(Q4_K_M, 약 2.5GB). 27B 모델은 남은 디스크에 들어가지 않았습니다. "
                    "앱이 로컬 서버를 직접 켜고 끄며, 로컬 답변에는 API 비용이 들지 않습니다."),
    "claim-check": ("해커톤 빌드의 핵심 기능은 무엇일까요?",
                    "주장–근거 확인: 숫자는 코드로 대조하고, 인용은 보낸 원문과 글자 그대로 맞춰 보며, "
                    "모델 호출 전에는 동의를 받습니다. 결과는 판정이 아니라 비서의 의견입니다."),
    "effort": ("클라우드 생각 단계를 '낮음'으로 둘까요, '없음'으로 바꿀까요?",
               "'낮음' 유지. 중앙값 차이 0.45초는 실행마다 생기는 편차 안이었고 품질은 같았습니다."),
    "scoring": ("비교 실험의 답은 어떻게 채점할까요?",
                "AI 1차 채점 → 사람 2차 검수(출처 공개 뒤). AI 점수를 사람 점수처럼 쓰지 않습니다."),
    "harness-effect": ("하네스가 답을 더 좋게 만들까요?",
                       "3차 비교(10문제, 3곳)는 가능성 수준입니다: 로컬 Qwen은 하네스 없이 4/10, 하네스로 6/10, "
                       "Baseten은 둘 다 9/10, Modal은 둘 다 7/10. 한 번만 돌렸고 자료가 서로 겹치며 사람 검수가 남았습니다."),
    "look": ("macOS 27 스타일로 바꿀까요?",
             "유리 효과는 툴바, 사이드바, 비서 패널에만 쓰고 연구 내용은 단단한 바탕으로 둡니다. "
             "투명도 줄이기를 켜면 단단한 바탕으로 바뀝니다."),
    "glimmer": ("Glimmer 30B를 GPU를 빌려 돌릴까요?",
                "GPU를 쓰려면 계정에 결제 수단이 필요해 연구자가 쓰지 않기로 했습니다. 설정의 Glimmer 줄은 '배포 안 됨'으로 둡니다."),
    "release": ("저장소 커밋, 공개, 제출은 언제 할까요? (마감 11월 4일 오후 9시, 미국 동부)", ""),
    "human-review": ("3차 비교 사람 2차 검수: 남은 답 56개의 AI 1차 점수를 확정할까요, 고칠까요?", ""),
}

NEXT_KO = [
    ("데모 영상", "3분 촬영: 이 칩을 열어 브리핑을 보여 주고, 주장 확인 한 번과 영수증을 보여 줍니다."),
    ("청구서", "실제 청구서를 계산된 비용과 대조합니다(Baseten 약 $0.21, Modal 최대 $4.17)."),
]

CONVERSATION_KO = """공개용 Research Workbench 개발 일지입니다. 비용은 계산값이며 청구서와 대조하지 않았습니다.
2026-10-26 연구자: 비서가 핵심이야. 다시 오면 어디까지 했는지 알려 줘.
2026-10-26 비서: 저장했습니다. 복귀 브리핑을 맨 앞에 두고 모델 설정은 접어 두겠습니다.
2026-10-27 연구자: 게임기처럼 느껴지게 해 줘. 분야마다 칩 하나.
2026-10-27 비서: 메뉴 네 개를 정했습니다: 문헌 검토, 연구, 집필, 대화 기록 모음.
2026-10-28 비서: 보류한 질문을 모델 호출 없이 정한 날짜에 다시 띄울 수 있습니다.
2026-10-30 비서: 로컬 Qwen3-4B를 설치해 연결했습니다. 앱이 직접 켜고 끕니다.
2026-10-31 비서: 주장–근거 확인이 로컬과 클라우드 양쪽에서 돌아갑니다. 첫 클라우드 확인은 3.4초, 약 $0.004였습니다.
2026-11-01 비서: 3차 비교에서 10문제를 3곳에 물었습니다. AI 1차 점수가 나왔고, 다음은 연구자의 2차 검수입니다.
2026-11-01 연구자: Glimmer는 쓰지 말자.
2026-11-01 비서: 기록했습니다. 사람 검수와, 커밋·공개·제출 시점을 정해 주시길 기다리고 있습니다.
"""


def dev_history_bytes(lang="en"):
    if lang == "ko":
        entries = [(q, day, KO[q][0], verdict, KO[q][1]) for q, day, _, verdict, _ in ENTRIES]
        pack_id, title, tagline, next_items, conversation = (
            PACK_ID + "_ko", "Research Workbench 개발 일지", "이 앱을 만든 과정, 날짜별로", NEXT_KO, CONVERSATION_KO)
    else:
        entries = ENTRIES
        pack_id, title, tagline, next_items, conversation = (
            PACK_ID, "Research Workbench development log", "How this app was built, day by day", NEXT, CONVERSATION)
    pack = PACKS["engineering_measurement"].model_copy(update={
        "pack_id": pack_id, "title": title,
        "instructions": "Keep every decision with its reason and date. Separate measured results from "
                        "plans and calculated costs from invoices. Never present AI scores as human "
                        "scores. Ask the researcher before changing scope."})
    record = chips.Record.model_validate({
        # Newest first: the briefing shows the first three decisions, and "where you left
        # off" means the latest days, not the first ones.
        "questions": [{"question_id": q, "text": text, "asked_at": day}
                      for q, day, text, _, _ in reversed(entries)],
        "answers": [{"question_id": q, "kind": verdict, "value": reason, "answered_at": day}
                    for q, day, _, verdict, reason in reversed(entries) if verdict],
        "harness": [{"key": key, "value": value} for key, value in next_items],
    })
    return chips._encode(
        chips.Manifest(format=1, pack=pack,
                       presentation=chips.Presentation(tagline=tagline, color="#3a6b4f", glyph="⌘")),
        None, record, conversation)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    Path(sys.argv[1]).write_bytes(dev_history_bytes(*sys.argv[2:3]))
