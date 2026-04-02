#!/usr/bin/env bash
# =============================================================================
#  Data_LG — 원클릭 설치 및 서비스 시작 스크립트
#  사용법: bash setup.sh [--dev]
#    --dev : docker-compose.override.yml 적용 (핫리로드 개발 모드)
# =============================================================================
set -euo pipefail

# ── 색상 출력 헬퍼 ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── 옵션 파싱 ─────────────────────────────────────────────────────────────────
DEV_MODE=false
for arg in "$@"; do
  [[ "$arg" == "--dev" ]] && DEV_MODE=true
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE_CMD="docker compose"
$DEV_MODE && COMPOSE_OPTS="-f docker-compose.yml -f docker-compose.override.yml" || COMPOSE_OPTS="-f docker-compose.yml"

echo ""
echo "========================================================"
echo "   Data_LG 서비스 설치 및 시작"
$DEV_MODE && echo "   모드: 개발 (hot-reload)" || echo "   모드: 프로덕션"
echo "========================================================"
echo ""

# ── 1. OS 감지 ────────────────────────────────────────────────────────────────
detect_os() {
  if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    echo "$ID"
  elif [[ "$(uname)" == "Darwin" ]]; then
    echo "darwin"
  else
    echo "unknown"
  fi
}
OS=$(detect_os)
info "OS: $OS"

# ── 2. Docker 설치 확인 및 설치 ───────────────────────────────────────────────
install_docker() {
  info "Docker 설치 중..."
  case "$OS" in
    ubuntu|debian)
      sudo apt-get update -qq
      sudo apt-get install -y -qq ca-certificates curl gnupg lsb-release
      sudo install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://download.docker.com/linux/$OS/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      sudo chmod a+r /etc/apt/keyrings/docker.gpg
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/$OS $(lsb_release -cs) stable" | \
        sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
      sudo apt-get update -qq
      sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
      sudo systemctl enable --now docker
      # 현재 유저를 docker 그룹에 추가
      sudo usermod -aG docker "$USER" 2>/dev/null || true
      ;;
    centos|rhel|fedora|rocky|almalinux)
      sudo yum install -y yum-utils
      sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
      sudo yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
      sudo systemctl enable --now docker
      sudo usermod -aG docker "$USER" 2>/dev/null || true
      ;;
    darwin)
      error "macOS에서는 Docker Desktop을 수동으로 설치해주세요: https://www.docker.com/products/docker-desktop"
      ;;
    *)
      error "지원하지 않는 OS입니다. Docker를 수동으로 설치해주세요."
      ;;
  esac
  success "Docker 설치 완료"
}

if ! command -v docker &>/dev/null; then
  install_docker
else
  DOCKER_VER=$(docker --version 2>/dev/null | grep -oP '\d+\.\d+' | head -1)
  success "Docker 이미 설치됨: $DOCKER_VER"
fi

# docker compose (plugin) 확인
if ! docker compose version &>/dev/null; then
  error "docker compose 플러그인이 없습니다. Docker 20.10+ 또는 docker-compose-plugin을 설치해주세요."
fi
success "docker compose: $(docker compose version --short 2>/dev/null || echo 'ok')"

# Docker 데몬 실행 확인
if ! docker info &>/dev/null; then
  warn "Docker 데몬이 실행 중이 아닙니다. 시작을 시도합니다..."
  if command -v systemctl &>/dev/null; then
    sudo systemctl start docker
    sleep 3
  fi
  docker info &>/dev/null || error "Docker 데몬 시작 실패. 수동으로 시작해주세요: sudo systemctl start docker"
fi

# ── 3. vLLM 설정 입력 ────────────────────────────────────────────────────────
DEFAULT_VLLM_ENDPOINT="http://10.36.114.31:30081/v1"
DEFAULT_VLLM_MODEL="Qwen3/Qwen3-Next-80B-A3B-Instruct-FP8"

echo ""
echo "──────────────────────────────────────────────────────"
echo "  vLLM 서버 설정"
echo "──────────────────────────────────────────────────────"

# 기존 .env에서 현재값 읽어오기 (있으면)
CURRENT_ENDPOINT=""
CURRENT_MODEL=""
if [[ -f .env ]]; then
  CURRENT_ENDPOINT=$(grep -E '^VLLM_ENDPOINT_SMALL=' .env | cut -d'=' -f2- | tr -d '"' | tr -d "'")
  CURRENT_MODEL=$(grep -E '^VLLM_MODEL_SMALL=' .env | cut -d'=' -f2- | tr -d '"' | tr -d "'")
fi

PROMPT_ENDPOINT="${CURRENT_ENDPOINT:-$DEFAULT_VLLM_ENDPOINT}"
PROMPT_MODEL="${CURRENT_MODEL:-$DEFAULT_VLLM_MODEL}"

read -rp "  vLLM 엔드포인트 [${PROMPT_ENDPOINT}]: " INPUT_ENDPOINT
read -rp "  vLLM 모델명     [${PROMPT_MODEL}]: " INPUT_MODEL

VLLM_ENDPOINT="${INPUT_ENDPOINT:-$PROMPT_ENDPOINT}"
VLLM_MODEL="${INPUT_MODEL:-$PROMPT_MODEL}"

success "엔드포인트: $VLLM_ENDPOINT"
success "모델:       $VLLM_MODEL"
echo ""

# ── 4. .env 파일 생성/업데이트 ───────────────────────────────────────────────
write_env() {
  cat > .env << EOF
# PostgreSQL
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=regression_platform
POSTGRES_USER=app
POSTGRES_PASSWORD=changeme

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# JWT
SECRET_KEY=your-secret-key-change-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7

# vLLM
VLLM_ENDPOINT_SMALL=${VLLM_ENDPOINT}
VLLM_MODEL_SMALL=${VLLM_MODEL}
VLLM_TEMPERATURE=0.1
VLLM_MAX_TOKENS=4096

# Artifact Store
ARTIFACT_STORE_ROOT=/data/app/artifacts
BUILTIN_DATASET_PATH=/app/datasets_builtin

# App
APP_ENV=development
LOG_LEVEL=INFO
MAX_UPLOAD_MB=100
MAX_SHAP_ROWS=5000
PLOT_SAMPLING_THRESHOLD_ROWS=200000
DEFAULT_SESSION_TTL_DAYS=7
DEFAULT_SUBSET_LIMIT=5
JOB_TIMEOUT_SECONDS=600
EOF
}

if [[ ! -f .env ]]; then
  write_env
  success ".env 파일 생성됨"
else
  # vLLM 값만 교체 (다른 설정 보존)
  sed -i "s|^VLLM_ENDPOINT_SMALL=.*|VLLM_ENDPOINT_SMALL=${VLLM_ENDPOINT}|" .env
  sed -i "s|^VLLM_MODEL_SMALL=.*|VLLM_MODEL_SMALL=${VLLM_MODEL}|" .env
  # VLLM 항목이 아예 없는 경우 추가
  grep -q '^VLLM_ENDPOINT_SMALL=' .env || echo "VLLM_ENDPOINT_SMALL=${VLLM_ENDPOINT}" >> .env
  grep -q '^VLLM_MODEL_SMALL=' .env   || echo "VLLM_MODEL_SMALL=${VLLM_MODEL}" >> .env
  success ".env 업데이트됨"
fi

# ── 5. 내장 데이터셋 생성 (없는 경우) ────────────────────────────────────────
DATASETS_DIR="$SCRIPT_DIR/datasets_builtin"
PARQUET_COUNT=$(ls "$DATASETS_DIR"/*.parquet 2>/dev/null | wc -l)
if [[ "$PARQUET_COUNT" -lt 1 ]]; then
  info "내장 데이터셋 생성 중..."
  if command -v python3 &>/dev/null; then
    python3 "$DATASETS_DIR/generate_datasets.py" && success "데이터셋 생성 완료"
  else
    warn "python3가 없어 데이터셋 생성을 건너뜁니다. 컨테이너 내에서 나중에 실행하세요."
  fi
else
  success "내장 데이터셋 확인됨 (${PARQUET_COUNT}개)"
fi

# ── 5. 기존 컨테이너 정리 (선택) ─────────────────────────────────────────────
if $COMPOSE_CMD $COMPOSE_OPTS ps --quiet 2>/dev/null | grep -q .; then
  warn "기존 컨테이너가 실행 중입니다."
  read -rp "  기존 컨테이너를 중지하고 재시작할까요? 데이터는 보존됩니다. [Y/n] " yn
  yn=${yn:-Y}
  if [[ "$yn" =~ ^[Yy]$ ]]; then
    info "기존 컨테이너 중지 중..."
    $COMPOSE_CMD $COMPOSE_OPTS down --remove-orphans
    success "기존 컨테이너 중지 완료"
  fi
fi

# ── 6. 이미지 빌드 ────────────────────────────────────────────────────────────
info "Docker 이미지 빌드 중... (첫 실행 시 수 분 소요)"
$COMPOSE_CMD $COMPOSE_OPTS build --parallel
success "이미지 빌드 완료"

# ── 7. 서비스 시작 ────────────────────────────────────────────────────────────
info "서비스 시작 중..."
$COMPOSE_CMD $COMPOSE_OPTS up -d
success "컨테이너 시작됨"

# ── 8. PostgreSQL 헬스체크 대기 ───────────────────────────────────────────────
info "PostgreSQL 준비 대기 중..."
MAX_WAIT=60
WAITED=0
while ! $COMPOSE_CMD $COMPOSE_OPTS exec -T postgres \
    pg_isready -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-regression_platform}" &>/dev/null; do
  sleep 2
  WAITED=$((WAITED + 2))
  if [[ $WAITED -ge $MAX_WAIT ]]; then
    error "PostgreSQL이 ${MAX_WAIT}초 내에 준비되지 않았습니다."
  fi
  echo -n "."
done
echo ""
success "PostgreSQL 준비 완료"

# ── 9. DB 마이그레이션 ────────────────────────────────────────────────────────
info "DB 마이그레이션 실행 중..."
$COMPOSE_CMD $COMPOSE_OPTS exec -T backend alembic upgrade head
success "마이그레이션 완료"

# ── 10. job_type enum에 inverse_optimization 추가 (멱등) ─────────────────────
info "DB enum 값 확인 및 업데이트 중..."
$COMPOSE_CMD $COMPOSE_OPTS exec -T postgres psql \
  -U "${POSTGRES_USER:-app}" \
  -d "${POSTGRES_DB:-regression_platform}" \
  -c "DO \$\$ BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM pg_enum
          WHERE enumlabel = 'inverse_optimization'
            AND enumtypid = 'job_type'::regtype
        ) THEN
          ALTER TYPE job_type ADD VALUE 'inverse_optimization';
        END IF;
      END \$\$;" 2>/dev/null || true
success "enum 업데이트 완료"

# ── 11. 시드 데이터 입력 ──────────────────────────────────────────────────────
info "시드 데이터 입력 중..."
$COMPOSE_CMD $COMPOSE_OPTS exec -T backend python -m app.db.seed && success "시드 데이터 완료" \
  || warn "시드 데이터 입력 실패 (이미 있을 수 있음, 계속 진행합니다)"

# ── 12. 백엔드 헬스체크 ───────────────────────────────────────────────────────
info "백엔드 API 준비 대기 중..."
MAX_WAIT=60
WAITED=0
while ! curl -sf http://localhost:8000/health &>/dev/null && \
      ! curl -sf http://localhost:8000/api/v1/health &>/dev/null && \
      ! curl -sf http://localhost:8000/docs &>/dev/null; do
  sleep 3
  WAITED=$((WAITED + 3))
  if [[ $WAITED -ge $MAX_WAIT ]]; then
    warn "백엔드 헬스체크 타임아웃 — 서비스는 계속 시작 중일 수 있습니다."
    break
  fi
  echo -n "."
done
echo ""
success "백엔드 준비 완료"

# ── 완료 ──────────────────────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo -e "${GREEN}  서비스 시작 완료!${NC}"
echo "========================================================"
echo ""
echo "  프론트엔드:  http://localhost:8501"
echo "  백엔드 API:  http://localhost:8000/docs"
echo "  PostgreSQL:  localhost:5432  (user: ${POSTGRES_USER:-app})"
echo ""
echo "  로그 확인:   docker compose logs -f"
echo "  서비스 중지: docker compose down"
echo "========================================================"
echo ""
