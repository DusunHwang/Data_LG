#!/usr/bin/env bash
# =============================================================================
#  Data_LG — Docker 없이 uv 가상환경으로 직접 실행
#  사용법: bash start.sh [--vllm-endpoint URL] [--vllm-model MODEL]
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
echo "   Data_LG — 직접 실행 모드 (uv + SQLite)"
echo "========================================================"

# ── 1. uv 설치 확인 ───────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
  info "uv 설치 중..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
fi
success "uv: $(uv --version)"

# ── 2. .env 파일 설정 ─────────────────────────────────────────────────────────
DEFAULT_ENDPOINT="http://your-vllm-server/v1"
DEFAULT_MODEL="Qwen3/Qwen3-Next-80B-A3B-Instruct-FP8"

if [[ ! -f .env ]]; then
  cp .env.simple .env
  info ".env.simple → .env 복사됨"
fi

# 대화형 입력 (.env에 이미 값이 있으면 그대로 사용, 터미널이 없으면 건너뜀)
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
    # 비대화형 터미널 — .env 현재값 그대로 사용
    VLLM_ENDPOINT="$CURRENT_EP"
    VLLM_MODEL="$CURRENT_MODEL"
  fi
fi

sed -i "s|^VLLM_ENDPOINT_SMALL=.*|VLLM_ENDPOINT_SMALL=${VLLM_ENDPOINT}|" .env
sed -i "s|^VLLM_MODEL_SMALL=.*|VLLM_MODEL_SMALL=${VLLM_MODEL}|"         .env
success "vLLM: ${VLLM_ENDPOINT}  /  ${VLLM_MODEL}"

# ── 3. 내장 데이터셋 생성 ─────────────────────────────────────────────────────
PARQUET_COUNT=$(ls datasets_builtin/*.parquet 2>/dev/null | wc -l || echo 0)
if [[ "$PARQUET_COUNT" -lt 1 ]]; then
  info "내장 데이터셋 생성 중..."
  uv run --project . python datasets_builtin/generate_datasets.py 2>/dev/null || \
    python3 datasets_builtin/generate_datasets.py || \
    warn "데이터셋 생성 실패 — 나중에 수동 실행: python datasets_builtin/generate_datasets.py"
fi

# ── 4. 백엔드 의존성 설치 ─────────────────────────────────────────────────────
info "백엔드 의존성 설치 중..."
cd "$SCRIPT_DIR/backend"
uv sync --extra dev 2>/dev/null || uv pip install -e ".[dev]"
success "백엔드 의존성 설치 완료"

# ── 5. DB 초기화 ──────────────────────────────────────────────────────────────
info "DB 초기화 중..."
mkdir -p data
uv run alembic upgrade head
success "DB 마이그레이션 완료"

# ── 6. 시드 데이터 ────────────────────────────────────────────────────────────
info "시드 데이터 입력 중..."
uv run python -m app.db.seed && success "시드 데이터 완료" || \
  warn "시드 데이터 실패 (이미 있을 수 있음)"

# ── 7. 프론트엔드 의존성 설치 ──────────────────────────────────────────────────
info "프론트엔드 의존성 설치 중..."
cd "$SCRIPT_DIR/frontend"
uv sync 2>/dev/null || uv pip install -e .
success "프론트엔드 의존성 설치 완료"

# ── 8. 서비스 시작 ────────────────────────────────────────────────────────────
echo ""
info "서비스 시작 중..."

# 백엔드 백그라운드 실행
cd "$SCRIPT_DIR/backend"
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  --log-level info > "$SCRIPT_DIR/logs/backend.log" 2>&1 &
BACKEND_PID=$!
echo $BACKEND_PID > "$SCRIPT_DIR/logs/backend.pid"
success "백엔드 시작 (PID: $BACKEND_PID)"

# 백엔드 준비 대기
mkdir -p "$SCRIPT_DIR/logs"
MAX_WAIT=30; WAITED=0
while ! curl -sf http://localhost:8000/docs &>/dev/null; do
  sleep 2; WAITED=$((WAITED+2))
  [[ $WAITED -ge $MAX_WAIT ]] && { warn "백엔드 준비 타임아웃"; break; }
  echo -n "."
done
echo ""

# 프론트엔드 포그라운드 실행 (Ctrl+C로 종료)
cd "$SCRIPT_DIR/frontend"
info "프론트엔드 시작 (Ctrl+C로 전체 종료)"

cleanup() {
  echo ""
  info "서비스 종료 중..."
  [[ -f "$SCRIPT_DIR/logs/backend.pid" ]] && kill "$(cat "$SCRIPT_DIR/logs/backend.pid")" 2>/dev/null || true
  success "종료 완료"
}
trap cleanup EXIT INT TERM

echo ""
echo "========================================================"
echo -e "${GREEN}  서비스 시작 완료!${NC}"
echo "========================================================"
echo "  프론트엔드:  http://localhost:8501"
echo "  백엔드 API:  http://localhost:8000/docs"
echo "  로그:        logs/backend.log"
echo "  종료:        Ctrl+C"
echo "========================================================"
echo ""

uv run streamlit run app/main.py \
  --server.port 8501 \
  --server.address 0.0.0.0 \
  --server.headless true
