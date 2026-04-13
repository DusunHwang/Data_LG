#!/usr/bin/env bash
# =============================================================================
#  Data_LG — 설치 스크립트
#  최초 1회 또는 배포 갱신 시 실행. 의존성 설치, DB 마이그레이션,
#  내장 데이터셋 경로 설정, 시드 데이터 입력까지 수행.
#  사용법: bash install.sh [--vllm-endpoint URL] [--vllm-model MODEL]
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

# ── 옵션 파싱 ─────────────────────────────────────────────────────────────────
VLLM_ENDPOINT=""
VLLM_MODEL=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --vllm-endpoint) VLLM_ENDPOINT="$2"; shift 2 ;;
    --vllm-model)    VLLM_MODEL="$2";    shift 2 ;;
    *) shift ;;
  esac
done

echo ""
echo "========================================================"
echo "   Data_LG — 설치"
echo "========================================================"

# ── 1. uv 설치 확인 ───────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
  info "uv 설치 중..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
fi
success "uv: $(uv --version)"

# ── 2. Node.js 확인 ───────────────────────────────────────────────────────────
if ! command -v node &>/dev/null; then
  error "Node.js가 설치되어 있지 않습니다.\n  Ubuntu/Debian: curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs\n  또는 https://nodejs.org 에서 v18 이상을 설치하세요."
fi
NODE_MAJOR=$(node --version | sed 's/v\([0-9]*\).*/\1/')
if [[ "$NODE_MAJOR" -lt 18 ]]; then
  error "Node.js v18 이상이 필요합니다. 현재: $(node --version)"
fi
success "node: $(node --version)  /  npm: $(npm --version)"

# ── 3. backend/.env 준비 및 vLLM 설정 입력 ───────────────────────────────────
DEFAULT_ENDPOINT="http://your-vllm-server/v1"
DEFAULT_MODEL="your-model-name"

# backend/.env 기준으로 현재값 읽기 (백엔드가 실제로 읽는 파일)
BACKEND_ENV="$SCRIPT_DIR/backend/.env"

if [[ ! -f "$BACKEND_ENV" ]]; then
  if [[ -f "$SCRIPT_DIR/.env.simple" ]]; then
    cp "$SCRIPT_DIR/.env.simple" "$BACKEND_ENV"
    info ".env.simple → backend/.env 복사됨"
  else
    touch "$BACKEND_ENV"
    warn ".env.simple이 없어 빈 backend/.env를 생성했습니다."
  fi
fi

# 백엔드는 backend 디렉터리에서 실행되지만 내장 데이터셋은 repo 루트의
# datasets_builtin을 사용한다. 절대 경로로 고정해 배포 위치 변경 시에도
# mpea_alloy.csv와 parquet built-in 파일을 같은 registry에서 찾게 한다.
mkdir -p "$SCRIPT_DIR/datasets_builtin"
set_env_var "$BACKEND_ENV" "BUILTIN_DATASET_PATH" "$SCRIPT_DIR/datasets_builtin"
set_env_var "$BACKEND_ENV" "ARTIFACT_STORE_ROOT" "./data/artifacts"
set_env_var "$BACKEND_ENV" "DATABASE_PATH" "./data/app.db"

CURRENT_EP=$(grep -E '^VLLM_ENDPOINT_SMALL=' "$BACKEND_ENV" 2>/dev/null | cut -d'=' -f2- || echo "$DEFAULT_ENDPOINT")
CURRENT_MODEL=$(grep -E '^VLLM_MODEL_SMALL=' "$BACKEND_ENV" 2>/dev/null | cut -d'=' -f2- || echo "$DEFAULT_MODEL")

if [[ -z "$VLLM_ENDPOINT" && -z "$VLLM_MODEL" ]]; then
  if [[ -t 0 ]]; then
    echo ""
    echo "──────────────────────────────────────────────────────"
    echo "  vLLM 서버 설정 (Enter = 현재값 유지)"
    echo "──────────────────────────────────────────────────────"
    read -rp "  엔드포인트 [${CURRENT_EP}]: " INPUT_EP
    read -rp "  모델명     [${CURRENT_MODEL}]: " INPUT_MODEL
    VLLM_ENDPOINT="${INPUT_EP:-$CURRENT_EP}"
    VLLM_MODEL="${INPUT_MODEL:-$CURRENT_MODEL}"
  else
    VLLM_ENDPOINT="$CURRENT_EP"
    VLLM_MODEL="$CURRENT_MODEL"
  fi
fi
VLLM_ENDPOINT="${VLLM_ENDPOINT:-$CURRENT_EP}"
VLLM_MODEL="${VLLM_MODEL:-$CURRENT_MODEL}"

set_env_var "$BACKEND_ENV" "VLLM_ENDPOINT_SMALL" "$VLLM_ENDPOINT"
set_env_var "$BACKEND_ENV" "VLLM_MODEL_SMALL" "$VLLM_MODEL"
success "vLLM: ${VLLM_ENDPOINT}  /  ${VLLM_MODEL}"
success "내장 데이터셋 경로: $SCRIPT_DIR/datasets_builtin"

# ── 4. 백엔드 의존성 설치 ─────────────────────────────────────────────────────
info "백엔드 의존성 설치 중..."
cd "$SCRIPT_DIR/backend"
uv sync --extra dev
success "백엔드 의존성 설치 완료"

# ── 5. 내장 데이터셋 확인/생성 ────────────────────────────────────────────────
PARQUET_COUNT=$(find "$SCRIPT_DIR/datasets_builtin" -maxdepth 1 -name '*.parquet' 2>/dev/null | wc -l | tr -d ' ')
if [[ "$PARQUET_COUNT" -lt 4 ]]; then
  info "기본 parquet 내장 데이터셋 생성 중..."
  uv run python "$SCRIPT_DIR/datasets_builtin/generate_datasets.py" || \
    warn "데이터셋 생성 실패 — 나중에 수동 실행: cd backend && uv run python ../datasets_builtin/generate_datasets.py"
else
  success "기본 parquet 내장 데이터셋 이미 존재 (${PARQUET_COUNT}개)"
fi

if [[ -f "$SCRIPT_DIR/datasets_builtin/mpea_alloy.csv" ]]; then
  success "MPEA 내장 데이터셋 확인: datasets_builtin/mpea_alloy.csv"
else
  warn "MPEA 내장 데이터셋 파일 없음: datasets_builtin/mpea_alloy.csv"
fi

# ── 6. DB 초기화/마이그레이션 ─────────────────────────────────────────────────
info "DB 초기화 중..."
mkdir -p data
uv run alembic upgrade head
success "DB 마이그레이션 완료"

# ── 7. 시드 데이터 ────────────────────────────────────────────────────────────
info "시드 데이터 입력 중..."
uv run python -m app.db.seed && success "시드 데이터 완료" || \
  warn "시드 데이터 실패 (이미 있을 수 있음)"

# ── 8. 프론트엔드 의존성 설치 ─────────────────────────────────────────────────
info "프론트엔드 의존성 설치 중..."
cd "$SCRIPT_DIR/frontend-react"
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi
success "프론트엔드 의존성 설치 완료"

# ── 완료 ──────────────────────────────────────────────────────────────────────
cd "$SCRIPT_DIR"
echo ""
echo "========================================================"
echo -e "${GREEN}  설치 완료!${NC}"
echo "========================================================"
echo "  실행하려면: bash run.sh"
echo "========================================================"
echo ""
