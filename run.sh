#!/usr/bin/env bash
# =============================================================================
#  Data_LG — 실행 스크립트
#  install.sh 실행 후 사용. 백엔드 + 프론트엔드를 시작.
#  사용법: bash run.sh
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "========================================================"
echo "   Data_LG — 서비스 시작"
echo "========================================================"

# ── 설치 여부 확인 ────────────────────────────────────────────────────────────
[[ ! -d backend/.venv ]]          && error "백엔드 가상환경 없음. 먼저 bash install.sh를 실행해주세요."
[[ ! -d frontend-react/node_modules ]] && error "프론트엔드 패키지 없음. 먼저 bash install.sh를 실행해주세요."
[[ ! -f backend/data/app.db ]]    && error "DB 없음. 먼저 bash install.sh를 실행해주세요."

# ── 환경변수 로드 ─────────────────────────────────────────────────────────────
[[ ! -f .env ]] && error ".env 파일 없음. 먼저 bash install.sh를 실행해주세요."

# ── 로그 디렉토리 ─────────────────────────────────────────────────────────────
mkdir -p "$SCRIPT_DIR/logs"

# ── 종료 핸들러 ───────────────────────────────────────────────────────────────
cleanup() {
  echo ""
  info "서비스 종료 중..."
  [[ -f "$SCRIPT_DIR/logs/backend.pid"  ]] && kill "$(cat "$SCRIPT_DIR/logs/backend.pid")"  2>/dev/null || true
  [[ -f "$SCRIPT_DIR/logs/frontend.pid" ]] && kill "$(cat "$SCRIPT_DIR/logs/frontend.pid")" 2>/dev/null || true
  rm -f "$SCRIPT_DIR/logs/backend.pid" "$SCRIPT_DIR/logs/frontend.pid"
  success "종료 완료"
}
trap cleanup EXIT INT TERM

# ── 백엔드 시작 ───────────────────────────────────────────────────────────────
info "백엔드 시작 중..."
cd "$SCRIPT_DIR/backend"
uv run uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 \
  --log-level info \
  > "$SCRIPT_DIR/logs/backend.log" 2>&1 &
BACKEND_PID=$!
echo $BACKEND_PID > "$SCRIPT_DIR/logs/backend.pid"
success "백엔드 시작 (PID: $BACKEND_PID, 로그: logs/backend.log)"

# ── 백엔드 준비 대기 ──────────────────────────────────────────────────────────
info "백엔드 준비 대기 중..."
MAX_WAIT=30; WAITED=0
until curl -sf http://localhost:8000/ &>/dev/null; do
  sleep 1; WAITED=$((WAITED+1))
  if [[ $WAITED -ge $MAX_WAIT ]]; then
    warn "백엔드 준비 타임아웃 — logs/backend.log 확인"
    break
  fi
  echo -n "."
done
echo ""

# ── 프론트엔드 시작 ───────────────────────────────────────────────────────────
info "프론트엔드 시작 중..."
cd "$SCRIPT_DIR/frontend-react"
npm run dev -- --host 0.0.0.0 \
  > "$SCRIPT_DIR/logs/frontend.log" 2>&1 &
FRONTEND_PID=$!
echo $FRONTEND_PID > "$SCRIPT_DIR/logs/frontend.pid"
success "프론트엔드 시작 (PID: $FRONTEND_PID, 로그: logs/frontend.log)"

# ── 시작 완료 ─────────────────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo -e "${GREEN}  서비스 실행 중!${NC}"
echo "========================================================"
echo "  프론트엔드:  http://localhost:3000"
echo "  백엔드 API:  http://localhost:8000/docs"
echo "  로그:        logs/backend.log  /  logs/frontend.log"
echo "  종료:        Ctrl+C"
echo "========================================================"
echo ""

# ── 포그라운드 대기 (Ctrl+C → cleanup) ───────────────────────────────────────
wait $BACKEND_PID
