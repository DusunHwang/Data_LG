#!/usr/bin/env bash
# =============================================================================
#  Data_LG — 실행 스크립트
#  install.sh 실행 후 사용. DB 마이그레이션 확인 후 백엔드 + 프론트엔드를 시작.
#  사용법: bash run.sh
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
set_env_var() {
  local file="$1"
  local key="$2"
  local value="$3"
  if grep -qE "^${key}=" "$file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$file"
  fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "========================================================"
echo "   Data_LG — 서비스 시작"
echo "========================================================"

# ── 설치 여부 확인 ────────────────────────────────────────────────────────────
[[ ! -d backend/.venv ]]               && error "백엔드 가상환경 없음. 먼저 bash install.sh를 실행해주세요."
[[ ! -d frontend-react/node_modules ]] && error "프론트엔드 패키지 없음. 먼저 bash install.sh를 실행해주세요."
[[ ! -f backend/data/app.db ]]         && error "DB 없음. 먼저 bash install.sh를 실행해주세요."
[[ ! -f backend/.env ]]                && error "backend/.env 없음. 먼저 bash install.sh를 실행해주세요."

# ── 실행 환경 보정 ────────────────────────────────────────────────────────────
# backend는 backend/를 cwd로 실행되므로, repo 루트 datasets_builtin을 절대 경로로
# 주입해 parquet built-in과 mpea_alloy.csv를 같은 경로에서 사용할 수 있게 한다.
set_env_var "$SCRIPT_DIR/backend/.env" "BUILTIN_DATASET_PATH" "$SCRIPT_DIR/datasets_builtin"
set_env_var "$SCRIPT_DIR/backend/.env" "ARTIFACT_STORE_ROOT" "./data/artifacts"
set_env_var "$SCRIPT_DIR/backend/.env" "DATABASE_PATH" "./data/app.db"
set_env_var "$SCRIPT_DIR/backend/.env" "COMPUTE_THREADS" "8"
set_env_var "$SCRIPT_DIR/backend/.env" "WORKER_MAX_WORKERS" "1"

export OMP_NUM_THREADS=8
export OPENBLAS_NUM_THREADS=8
export MKL_NUM_THREADS=8
export NUMEXPR_NUM_THREADS=8
export VECLIB_MAXIMUM_THREADS=8

[[ ! -f "$SCRIPT_DIR/datasets_builtin/mpea_alloy.csv" ]] && \
  warn "MPEA 내장 데이터셋 파일이 없습니다: datasets_builtin/mpea_alloy.csv"

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

# ── 기존 프로세스 정리 ────────────────────────────────────────────────────────
for PORT in 8000 3000; do
  PIDS=$(lsof -ti tcp:$PORT 2>/dev/null || true)
  if [[ -n "$PIDS" ]]; then
    warn "포트 $PORT 기존 프로세스 종료: $PIDS"
    kill $PIDS 2>/dev/null || true
    sleep 1
  fi
done

# ── DB 마이그레이션 확인 ─────────────────────────────────────────────────────
info "DB 마이그레이션 확인 중..."
cd "$SCRIPT_DIR/backend"
uv run alembic upgrade head
success "DB 마이그레이션 확인 완료"

# ── 백엔드 시작 ───────────────────────────────────────────────────────────────
info "백엔드 시작 중..."
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
if wait -n "$BACKEND_PID" "$FRONTEND_PID"; then
  warn "서비스 프로세스가 종료되었습니다."
else
  warn "서비스 프로세스 중 하나가 오류로 종료되었습니다. 로그를 확인하세요."
fi
