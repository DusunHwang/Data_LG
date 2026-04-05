#!/usr/bin/env bash
# =============================================================================
#  Data_LG — 전체 설치 + 실행 스크립트 (React 프론트엔드, SQLite 백엔드)
#
#  사용법:
#      bash setup_260405.sh
#
#  .env_vllm 파일 (선택):
#      VLLM_ENDPOINT=http://192.168.1.100:8000/v1
#
#  진행 순서:
#    1) uv / Node.js 설치 확인
#    2) vLLM 엔드포인트 설정 (.env_vllm 또는 대화형 입력)
#    3) 모델 자동 조회 / 선택
#    4) backend/.env 생성
#    5) 백엔드 의존성 설치 + DB 초기화
#    6) 프론트엔드 의존성 설치
#    7) 서비스 시작 (백엔드 8000, 프론트엔드 3000)
# =============================================================================
set -euo pipefail

R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' C='\033[0;36m' B='\033[1m' N='\033[0m'
info()   { echo -e "${C}[INFO]${N}  $*"; }
ok()     { echo -e "${G}[ OK ]${N}  $*"; }
warn()   { echo -e "${Y}[WARN]${N}  $*"; }
err()    { echo -e "${R}[ERR ]${N}  $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
mkdir -p logs

echo ""
echo -e "${B}${C}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║   Data_LG — 설치 + 실행 스크립트            ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${N}"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1  도구 확인
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${B}── STEP 1 / 5  도구 확인 ──${N}"

# uv
if ! command -v uv &>/dev/null; then
  info "uv 설치 중..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
fi
ok "uv: $(uv --version)"

# Node.js / npm
if ! command -v node &>/dev/null || ! command -v npm &>/dev/null; then
  err "Node.js / npm 가 설치되어 있지 않습니다.\n  → https://nodejs.org 에서 설치 후 다시 실행하세요."
fi
ok "Node.js: $(node --version)  /  npm: $(npm --version)"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2  vLLM 엔드포인트 설정
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${B}── STEP 2 / 5  vLLM 서버 설정 ──${N}"

# .env_vllm 파일에서 읽기 시도
VLLM_ENDPOINT=""
if [[ -f "$SCRIPT_DIR/.env_vllm" ]]; then
  _from_file=$(grep -E '^VLLM_ENDPOINT=' "$SCRIPT_DIR/.env_vllm" 2>/dev/null | cut -d'=' -f2- || true)
  if [[ -n "$_from_file" ]]; then
    VLLM_ENDPOINT="$_from_file"
    info ".env_vllm 에서 엔드포인트 읽음: $VLLM_ENDPOINT"
  fi
fi

# 기존 backend/.env 에서 읽기 시도
if [[ -z "$VLLM_ENDPOINT" && -f "$SCRIPT_DIR/backend/.env" ]]; then
  _from_env=$(grep -E '^VLLM_ENDPOINT_SMALL=' "$SCRIPT_DIR/backend/.env" 2>/dev/null | cut -d'=' -f2- || true)
  if [[ -n "$_from_env" ]]; then
    VLLM_ENDPOINT="$_from_env"
    info "backend/.env 에서 엔드포인트 읽음: $VLLM_ENDPOINT"
  fi
fi

# 모델 조회 함수
fetch_models() {
  local ep="$1"
  curl -sf --max-time 8 "${ep}/models" 2>/dev/null | \
    python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    ids = [m['id'] for m in data.get('data', [])]
    print('\n'.join(ids))
except Exception:
    pass
" 2>/dev/null || true
}

# 엔드포인트 유효성 검사 (최대 3회 시도)
SELECTED_ENDPOINT=""
SELECTED_MODEL=""

try_endpoint() {
  local ep="$1"
  # 끝 슬래시 제거, /v1 없으면 추가
  ep="${ep%/}"
  [[ "$ep" != */v1 ]] && ep="${ep}/v1"

  info "연결 확인 중: $ep"
  local models
  models=$(fetch_models "$ep")

  if [[ -z "$models" ]]; then
    return 1
  fi

  SELECTED_ENDPOINT="$ep"
  local count
  count=$(echo "$models" | grep -c . || true)

  if [[ $count -eq 1 ]]; then
    SELECTED_MODEL=$(echo "$models" | head -1)
    ok "모델 자동 선택: $SELECTED_MODEL"
  else
    echo ""
    echo -e "  ${B}사용 가능한 모델 목록:${N}"
    local idx=1
    while IFS= read -r m; do
      printf "    [%d] %s\n" "$idx" "$m"
      idx=$((idx+1))
    done <<< "$models"
    echo ""
    read -rp "  모델 번호 선택 [1]: " _idx
    _idx="${_idx:-1}"
    SELECTED_MODEL=$(echo "$models" | sed -n "${_idx}p")
    [[ -z "$SELECTED_MODEL" ]] && SELECTED_MODEL=$(echo "$models" | head -1)
    ok "선택된 모델: $SELECTED_MODEL"
  fi
  return 0
}

# 파일에서 읽은 엔드포인트로 먼저 시도
if [[ -n "$VLLM_ENDPOINT" ]]; then
  if ! try_endpoint "$VLLM_ENDPOINT"; then
    warn "저장된 엔드포인트 연결 실패 — 직접 입력합니다."
    VLLM_ENDPOINT=""
  fi
fi

# 대화형 입력 (연결 실패 또는 엔드포인트 없을 때)
if [[ -z "$SELECTED_ENDPOINT" ]]; then
  echo ""
  echo -e "  ${B}vLLM 서버 주소를 입력하세요.${N}"
  echo -e "  예) http://192.168.1.100:8000/v1"
  echo -e "  (Enter 건너뛰기: 나중에 start_260405.sh 실행 전 .env_vllm 파일에 입력)"
  echo ""

  for attempt in 1 2 3; do
    read -rp "  vLLM 엔드포인트 [skip]: " _raw_ep
    if [[ -z "$_raw_ep" ]]; then
      warn "vLLM 설정 건너뜀 — 나중에 .env_vllm 파일에 VLLM_ENDPOINT=... 입력 후 start_260405.sh 실행"
      SELECTED_ENDPOINT="http://localhost:8080/v1"
      SELECTED_MODEL="your-model-name"
      break
    fi
    if try_endpoint "$_raw_ep"; then
      break
    fi
    warn "연결 실패 (시도 $attempt/3)"
    [[ $attempt -eq 3 ]] && err "vLLM 서버에 연결할 수 없습니다."
  done
fi

echo ""
echo "  ┌──────────────────────────────────────────────┐"
printf "  │  엔드포인트: %-30s │\n" "$SELECTED_ENDPOINT"
printf "  │  모델:       %-30s │\n" "$SELECTED_MODEL"
echo "  └──────────────────────────────────────────────┘"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3  backend/.env 생성
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${B}── STEP 3 / 5  backend/.env 생성 ──${N}"

SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || \
             openssl rand -hex 32 2>/dev/null || \
             echo "change-this-secret-key-$(date +%s)")

# 기존 SECRET_KEY 유지 (재설치 시)
if [[ -f "$SCRIPT_DIR/backend/.env" ]]; then
  _existing_key=$(grep -E '^SECRET_KEY=' "$SCRIPT_DIR/backend/.env" 2>/dev/null | cut -d'=' -f2- || true)
  if [[ -n "$_existing_key" && "$_existing_key" != "your-secret-key-change-in-production" ]]; then
    SECRET_KEY="$_existing_key"
    info "기존 SECRET_KEY 유지"
  fi
fi

cat > "$SCRIPT_DIR/backend/.env" <<EOF
# Data_LG 백엔드 설정 — setup_260405.sh 로 자동 생성됨

# SQLite DB 경로 (backend 실행 디렉토리 기준)
DATABASE_PATH=./data/app.db

# JWT
SECRET_KEY=${SECRET_KEY}
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7

# vLLM
VLLM_ENDPOINT_SMALL=${SELECTED_ENDPOINT}
VLLM_MODEL_SMALL=${SELECTED_MODEL}
VLLM_TEMPERATURE=0.1
VLLM_MAX_TOKENS=4096

# 파일 저장 경로
ARTIFACT_STORE_ROOT=./data/artifacts
BUILTIN_DATASET_PATH=./datasets_builtin

# 앱 설정
APP_ENV=development
LOG_LEVEL=INFO
MAX_UPLOAD_MB=100
MAX_SHAP_ROWS=5000
PLOT_SAMPLING_THRESHOLD_ROWS=200000
DEFAULT_SESSION_TTL_DAYS=7
DEFAULT_SUBSET_LIMIT=5
JOB_TIMEOUT_SECONDS=600
EOF

ok "backend/.env 생성 완료"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4  의존성 설치 + DB 초기화
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${B}── STEP 4 / 5  의존성 설치 + DB 초기화 ──${N}"

# 백엔드 의존성
info "백엔드 의존성 설치 중..."
cd "$SCRIPT_DIR/backend"
uv sync 2>/dev/null || uv pip install -e ".[dev]"
ok "백엔드 의존성 완료"

# DB 마이그레이션
info "DB 마이그레이션 중..."
mkdir -p data data/artifacts
uv run alembic upgrade head
ok "DB 마이그레이션 완료"

# 시드 데이터
info "시드 데이터 입력 중..."
uv run python -m app.db.seed && ok "시드 데이터 완료" || warn "시드 스킵 (이미 존재)"

# 내장 데이터셋
PARQUET_COUNT=$(ls "$SCRIPT_DIR/backend/datasets_builtin/"*.parquet 2>/dev/null | wc -l || echo 0)
if [[ "$PARQUET_COUNT" -lt 1 ]]; then
  info "내장 데이터셋 생성 중..."
  cd "$SCRIPT_DIR/backend"
  uv run python datasets_builtin/generate_datasets.py && \
    ok "내장 데이터셋 생성 완료" || warn "데이터셋 생성 실패 — 나중에 수동 실행 가능"
else
  ok "내장 데이터셋 존재 (${PARQUET_COUNT}개)"
fi

# 프론트엔드 의존성
info "프론트엔드 의존성 설치 중..."
cd "$SCRIPT_DIR/frontend-react"
npm install
ok "프론트엔드 의존성 완료"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5  서비스 시작
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${B}── STEP 5 / 5  서비스 시작 ──${N}"

# 기존 프로세스 정리
pkill -f "uvicorn app.main:app" 2>/dev/null || true
pkill -f "vite.*3000"           2>/dev/null || true
sleep 1

# 백엔드 시작
info "백엔드 시작 중 (포트 8000)..."
cd "$SCRIPT_DIR/backend"
nohup uv run uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 --log-level info \
  > "$SCRIPT_DIR/logs/backend.log" 2>&1 &
echo $! > "$SCRIPT_DIR/logs/backend.pid"

# 백엔드 준비 대기
printf "  대기 중"
MAX_WAIT=40; WAITED=0
while ! curl -sf http://localhost:8000/docs &>/dev/null; do
  printf "."; sleep 2; WAITED=$((WAITED+2))
  [[ $WAITED -ge $MAX_WAIT ]] && { echo ""; warn "백엔드 타임아웃 — 로그: logs/backend.log"; break; }
done
echo ""
ok "백엔드 시작 완료 (PID: $(cat "$SCRIPT_DIR/logs/backend.pid"))"

# 프론트엔드 시작
info "프론트엔드 시작 중 (포트 3000)..."
cd "$SCRIPT_DIR/frontend-react"
nohup npm run dev -- --host 0.0.0.0 \
  > "$SCRIPT_DIR/logs/frontend.log" 2>&1 &
echo $! > "$SCRIPT_DIR/logs/frontend.pid"

# 프론트엔드 준비 대기
sleep 3
printf "  대기 중"
MAX_WAIT=30; WAITED=0
while ! curl -sf http://localhost:3000 &>/dev/null; do
  printf "."; sleep 2; WAITED=$((WAITED+2))
  [[ $WAITED -ge $MAX_WAIT ]] && { echo ""; warn "프론트엔드 타임아웃 — 로그: logs/frontend.log"; break; }
done
echo ""
ok "프론트엔드 시작 완료 (PID: $(cat "$SCRIPT_DIR/logs/frontend.pid"))"

# ── 종료 처리 ─────────────────────────────────────────────────────────────────
cleanup() {
  echo ""
  info "서비스 종료 중..."
  [[ -f "$SCRIPT_DIR/logs/backend.pid"  ]] && kill "$(cat "$SCRIPT_DIR/logs/backend.pid")"  2>/dev/null || true
  [[ -f "$SCRIPT_DIR/logs/frontend.pid" ]] && kill "$(cat "$SCRIPT_DIR/logs/frontend.pid")" 2>/dev/null || true
  ok "종료 완료"
}
trap cleanup EXIT INT TERM

# ── 완료 배너 ─────────────────────────────────────────────────────────────────
HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
echo ""
echo -e "${B}${G}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║           설치 및 시작 완료!                    ║"
echo "  ╠══════════════════════════════════════════════════╣"
printf "  ║  프론트엔드:  http://%-26s║\n" "${HOST_IP}:3000  "
printf "  ║  백엔드 API:  http://%-26s║\n" "${HOST_IP}:8000/docs  "
echo "  ╠══════════════════════════════════════════════════╣"
echo "  ║  기본 계정                                      ║"
echo "  ║    admin       /  Admin123!                     ║"
echo "  ║    demo_user_1 /  Demo123!                      ║"
echo "  ╠══════════════════════════════════════════════════╣"
echo "  ║  로그:  logs/backend.log  /  logs/frontend.log  ║"
echo "  ║  종료:  Ctrl+C  또는  bash stop.sh              ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${N}"

# 로그 실시간 출력 (Ctrl+C로 종료)
echo -e "${C}[백엔드 로그 — Ctrl+C 로 종료]${N}"
tail -f "$SCRIPT_DIR/logs/backend.log"
