#!/usr/bin/env bash
# =============================================================================
#  Data_LG — 설치 스크립트
#  최초 1회만 실행. 의존성 설치, DB 초기화, 시드 데이터 입력까지 수행.
#  사용법: bash install.sh [--vllm-endpoint URL] [--vllm-model MODEL]
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

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
  error "Node.js가 설치되어 있지 않습니다. https://nodejs.org 에서 v18 이상을 설치해주세요."
fi
success "node: $(node --version)  /  npm: $(npm --version)"

# ── 3. .env 파일 설정 ─────────────────────────────────────────────────────────
DEFAULT_ENDPOINT="http://your-vllm-server/v1"
DEFAULT_MODEL="Qwen3/Qwen3-Next-80B-A3B-Instruct-FP8"

if [[ ! -f .env ]]; then
  cp .env.simple .env
  info ".env.simple → .env 복사됨"
fi

CURRENT_EP=$(grep -E '^VLLM_ENDPOINT_SMALL=' .env 2>/dev/null | cut -d'=' -f2- || echo "$DEFAULT_ENDPOINT")
CURRENT_MODEL=$(grep -E '^VLLM_MODEL_SMALL=' .env 2>/dev/null | cut -d'=' -f2- || echo "$DEFAULT_MODEL")

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

sed -i "s|^VLLM_ENDPOINT_SMALL=.*|VLLM_ENDPOINT_SMALL=${VLLM_ENDPOINT}|" .env
sed -i "s|^VLLM_MODEL_SMALL=.*|VLLM_MODEL_SMALL=${VLLM_MODEL}|"         .env
success "vLLM: ${VLLM_ENDPOINT}  /  ${VLLM_MODEL}"

# ── 4. 내장 데이터셋 생성 ─────────────────────────────────────────────────────
PARQUET_COUNT=$(ls datasets_builtin/*.parquet 2>/dev/null | wc -l || echo 0)
if [[ "$PARQUET_COUNT" -lt 1 ]]; then
  info "내장 데이터셋 생성 중..."
  uv run --project . python datasets_builtin/generate_datasets.py || \
    warn "데이터셋 생성 실패 — 나중에 수동 실행: python datasets_builtin/generate_datasets.py"
else
  success "내장 데이터셋 이미 존재 (${PARQUET_COUNT}개)"
fi

# ── 5. 백엔드 의존성 설치 ─────────────────────────────────────────────────────
info "백엔드 의존성 설치 중..."
cd "$SCRIPT_DIR/backend"
uv sync --extra dev
success "백엔드 의존성 설치 완료"

# ── 6. DB 초기화 ──────────────────────────────────────────────────────────────
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
npm install
success "프론트엔드 의존성 설치 완료"

# ── 완료 ──────────────────────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo -e "${GREEN}  설치 완료!${NC}"
echo "========================================================"
echo "  실행하려면: bash run.sh"
echo "========================================================"
echo ""
