#!/bin/bash
set -e
cd "$(dirname "$0")"
export WB_MODAL_SHARED_BASE_URL="https://maninspurs--ep-wb-research-spark-server.us-west.modal.direct"
export WB_MODAL_SHARED_MODEL_ID="meta-models/Muse-Spark-Open-816B-A42B"
export WB_PREVIEW_MODAL_SHARED=1
printf '%s\n' 'This isolated test uses sample records and runs one real Modal Shared synthetic request.'
printf '%s\n' 'The core rehearsal remains a mock fixture; further AI tasks use the exact-transmission consent.'
exec node tools/preview_desktop_app.mjs
