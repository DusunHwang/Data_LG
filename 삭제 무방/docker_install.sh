#!/usr/bin/env bash
# =============================================================================
#  Data_LG — Docker 컨테이너 내 단일 명령 설치 스크립트
#
#  지원 환경: Ubuntu / Debian / Alpine / CentOS(RHEL) 기반 컨테이너
#
#  사용법 (1) — 이미 repo를 클론한 경우:
#      bash docker_install.sh
#
#  사용법 (2) — 빈 컨테이너에서 한 번에:
#      curl -fsSL https://raw.githubusercontent.com/DusunHwang/Data_LG/backend-simplify/docker_install.sh | bash
#
#  옵션:
#      --no-start          설치만 하고 서비스 시작 안 함
#      --endpoint  URL     vLLM 엔드포인트 (대화형 입력 스킵)
#      --model     NAME    vLLM 모델명    (대화형 입력 스킵)
#      --dir       PATH    설치 디렉토리 (기본: /opt/data_lg)
#      --branch    NAME    git 브랜치    (기본: backend-simplify)
# =============================================================================
set -euo pipefail

# ── 색상/로깅 ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
step()    { echo -e "\n${BOLD}${CYAN}══ $* ${NC}"; }

# ── 옵션 파싱 ─────────────────────────────────────────────────────────────────
INSTALL_DIR="/opt/data_lg"
REPO_URL="https://github.com/DusunHwang/Data_LG.git"
GIT_BRANCH="backend-simplify"
VLLM_ENDPOINT=""
VLLM_MODEL=""
NO_START=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --no-start)           NO_START=true;          shift   ;;
    --endpoint)           VLLM_ENDPOINT="$2";     shift 2 ;;
    --model)              VLLM_MODEL="$2";         shift 2 ;;
    --dir)                INSTALL_DIR="$2";        shift 2 ;;
    --branch)             GIT_BRANCH="$2";         shift 2 ;;
    *) shift ;;
  esac
done

# ── 배너 ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}"
echo "  ██████╗  █████╗ ████████╗ █████╗      ██╗      ██████╗ "
echo "  ██╔══██╗██╔══██╗╚══██╔══╝██╔══██╗    ██╔╝     ██╔════╝ "
echo "  ██║  ██║███████║   ██║   ███████║   ██╔╝      ██║  ███╗"
echo "  ██║  ██║██╔══██║   ██║   ██╔══██║  ██╔╝       ██║   ██║"
echo "  ██████╔╝██║  ██║   ██║   ██║  ██║ ██╔╝        ╚██████╔╝"
echo "  ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝          ╚═════╝ "
echo -e "${NC}"
echo "  회귀 분석 플랫폼 — Docker 자동 설치 스크립트"
echo "  ─────────────────────────────────────────────"
echo ""

# ── 현재 위치가 이미 프로젝트 디렉토리인지 확인 ────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-/proc/$$/fd/255}" 2>/dev/null || echo .)" && pwd)"
if [[ -f "$SCRIPT_DIR/backend/pyproject.toml" && -f "$SCRIPT_DIR/frontend/pyproject.toml" ]]; then
  info "현재 디렉토리에서 프로젝트 감지됨: $SCRIPT_DIR"
  INSTALL_DIR="$SCRIPT_DIR"
  SKIP_CLONE=true
else
  SKIP_CLONE=false
fi

# ═════════════════════════════════════════════════════════════════════════════
# 1. OS 감지 및 시스템 패키지 설치
# ═════════════════════════════════════════════════════════════════════════════
step "1/7  시스템 패키지 설치"

# OS 감지
PKG_MANAGER=""
if command -v apt-get &>/dev/null; then
  PKG_MANAGER="apt"
elif command -v apk &>/dev/null; then
  PKG_MANAGER="apk"
elif command -v dnf &>/dev/null; then
  PKG_MANAGER="dnf"
elif command -v yum &>/dev/null; then
  PKG_MANAGER="yum"
else
  warn "패키지 매니저를 감지하지 못했습니다. 수동으로 curl, git, gcc를 설치하세요."
fi

install_sys_pkgs() {
  case "$PKG_MANAGER" in
    apt)
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -qq
      apt-get install -y --no-install-recommends \
        curl wget git ca-certificates \
        gcc g++ make libffi-dev libssl-dev \
        fonts-nanum fontconfig \
        procps 2>/dev/null || \
      apt-get install -y --no-install-recommends \
        curl wget git ca-certificates gcc g++ make procps
      fc-cache -fv &>/dev/null || true
      ;;
    apk)
      apk add --no-cache \
        curl wget git ca-certificates \
        gcc g++ musl-dev libffi-dev openssl-dev \
        font-noto procps bash 2>/dev/null || \
      apk add --no-cache curl wget git ca-certificates gcc g++ musl-dev procps bash
      ;;
    dnf|yum)
      $PKG_MANAGER install -y \
        curl wget git ca-certificates \
        gcc gcc-c++ make libffi-devel openssl-devel \
        procps-ng
      ;;
  esac
}

if [[ -n "$PKG_MANAGER" ]]; then
  info "패키지 설치 중 ($PKG_MANAGER)..."
  install_sys_pkgs
  success "시스템 패키지 설치 완료"
fi

# ═════════════════════════════════════════════════════════════════════════════
# 2. uv 설치 및 Python 3.11 확보
# ═════════════════════════════════════════════════════════════════════════════
step "2/7  uv 및 Python 3.11 설치"

export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

if ! command -v uv &>/dev/null; then
  info "uv 설치 중..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
fi
success "uv $(uv --version)"

# Python 3.11 확보 (uv로 관리)
if ! uv python find 3.11 &>/dev/null 2>&1; then
  info "Python 3.11 설치 중 (uv)..."
  uv python install 3.11
fi
success "Python $(uv run --python 3.11 python --version 2>&1 | head -1)"

# ═════════════════════════════════════════════════════════════════════════════
# 3. 저장소 클론 또는 업데이트
# ═════════════════════════════════════════════════════════════════════════════
step "3/7  소스코드 준비"

if [[ "$SKIP_CLONE" == "true" ]]; then
  info "기존 프로젝트 디렉토리 사용: $INSTALL_DIR"
else
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "기존 설치 발견 — 최신 코드로 업데이트 중..."
    cd "$INSTALL_DIR"
    git fetch origin
    git checkout "$GIT_BRANCH"
    git pull origin "$GIT_BRANCH"
    success "코드 업데이트 완료"
  else
    info "저장소 클론 중: $REPO_URL (브랜치: $GIT_BRANCH)"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --branch "$GIT_BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
    success "클론 완료: $INSTALL_DIR"
  fi
fi

cd "$INSTALL_DIR"

# ═════════════════════════════════════════════════════════════════════════════
# 4. vLLM 설정 (사용자 입력)
# ═════════════════════════════════════════════════════════════════════════════
step "4/7  vLLM 서버 설정"

# .env 초기화
if [[ ! -f .env ]]; then
  cp .env.simple .env
fi

DEFAULT_EP=$(grep -E '^VLLM_ENDPOINT_SMALL=' .env 2>/dev/null | cut -d'=' -f2- || echo "http://localhost:8000/v1")
DEFAULT_MODEL=$(grep -E '^VLLM_MODEL_SMALL=' .env 2>/dev/null | cut -d'=' -f2- || echo "")

echo ""
echo -e "  ${BOLD}외부 vLLM 서버 정보를 입력하세요.${NC}"
echo "  (Enter 입력 시 현재 설정값 유지)"
echo ""

# 엔드포인트 입력
if [[ -z "$VLLM_ENDPOINT" ]]; then
  if [[ -t 0 ]]; then
    read -rp "  vLLM 엔드포인트 URL [${DEFAULT_EP}]: " _input_ep
    VLLM_ENDPOINT="${_input_ep:-$DEFAULT_EP}"
  else
    VLLM_ENDPOINT="$DEFAULT_EP"
    warn "비대화형 환경 — 기본 엔드포인트 사용: $VLLM_ENDPOINT"
    warn "변경하려면: --endpoint URL 옵션을 사용하세요"
  fi
fi

# 모델명 입력
if [[ -z "$VLLM_MODEL" ]]; then
  if [[ -t 0 ]]; then
    read -rp "  모델명              [${DEFAULT_MODEL}]: " _input_model
    VLLM_MODEL="${_input_model:-$DEFAULT_MODEL}"
  else
    VLLM_MODEL="$DEFAULT_MODEL"
  fi
fi

# .env 업데이트
sed -i "s|^VLLM_ENDPOINT_SMALL=.*|VLLM_ENDPOINT_SMALL=${VLLM_ENDPOINT}|" .env
sed -i "s|^VLLM_MODEL_SMALL=.*|VLLM_MODEL_SMALL=${VLLM_MODEL}|"         .env
success "vLLM 설정 완료"
echo "    엔드포인트: $VLLM_ENDPOINT"
echo "    모델명:     $VLLM_MODEL"

# vLLM 연결 테스트
echo ""
info "vLLM 서버 연결 확인 중..."
if curl -sf --max-time 5 "$VLLM_ENDPOINT/models" -H "Content-Type: application/json" &>/dev/null; then
  success "vLLM 서버 연결 성공"
else
  warn "vLLM 서버 연결 실패 — 나중에 다시 확인하세요 (설치는 계속 진행)"
fi

# ═════════════════════════════════════════════════════════════════════════════
# 5. Python 의존성 설치
# ═════════════════════════════════════════════════════════════════════════════
step "5/7  Python 의존성 설치"

info "백엔드 의존성 설치 중..."
cd "$INSTALL_DIR/backend"
uv sync --python 3.11 2>/dev/null || uv pip install --python 3.11 -e ".[dev]"
success "백엔드 의존성 완료"

info "프론트엔드 의존성 설치 중..."
cd "$INSTALL_DIR/frontend"
uv sync --python 3.11 2>/dev/null || uv pip install --python 3.11 -e .
success "프론트엔드 의존성 완료"

# ═════════════════════════════════════════════════════════════════════════════
# 6. DB 초기화 및 데이터 준비
# ═════════════════════════════════════════════════════════════════════════════
step "6/7  데이터베이스 초기화"

cd "$INSTALL_DIR/backend"
mkdir -p data

info "DB 마이그레이션 실행 중..."
uv run --python 3.11 alembic upgrade head
success "DB 마이그레이션 완료"

info "시드 데이터 입력 중..."
uv run --python 3.11 python -m app.db.seed && success "시드 데이터 완료" || \
  warn "시드 데이터 스킵 (이미 존재할 수 있음)"

info "내장 데이터셋 생성 중..."
PARQUET_COUNT=$(ls "$INSTALL_DIR/datasets_builtin"/*.parquet 2>/dev/null | wc -l || echo 0)
if [[ "$PARQUET_COUNT" -lt 1 ]]; then
  cd "$INSTALL_DIR"
  uv run --python 3.11 --project backend python datasets_builtin/generate_datasets.py && \
    success "내장 데이터셋 생성 완료" || \
    warn "데이터셋 생성 실패 — 나중에 수동 실행: python datasets_builtin/generate_datasets.py"
else
  success "내장 데이터셋 이미 존재 (${PARQUET_COUNT}개)"
fi

# ═════════════════════════════════════════════════════════════════════════════
# 7. 서비스 시작
# ═════════════════════════════════════════════════════════════════════════════
step "7/7  서비스 시작"

mkdir -p "$INSTALL_DIR/logs"

if [[ "$NO_START" == "true" ]]; then
  echo ""
  echo "════════════════════════════════════════════════════════"
  echo -e "${GREEN}  설치 완료! (--no-start 옵션으로 서비스 시작 스킵)${NC}"
  echo "════════════════════════════════════════════════════════"
  echo "  서비스 시작: cd $INSTALL_DIR && bash start.sh"
  echo "════════════════════════════════════════════════════════"
  exit 0
fi

# 기존 프로세스 정리
pkill -f "uvicorn app.main:app" 2>/dev/null || true
pkill -f "streamlit run"        2>/dev/null || true
sleep 1

# 백엔드 시작
info "백엔드 시작 중..."
cd "$INSTALL_DIR/backend"
nohup uv run --python 3.11 uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 --log-level info \
  > "$INSTALL_DIR/logs/backend.log" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" > "$INSTALL_DIR/logs/backend.pid"

# 백엔드 준비 대기
info "백엔드 준비 대기 중..."
MAX_WAIT=40; WAITED=0
while ! curl -sf http://localhost:8000/docs &>/dev/null; do
  sleep 2; WAITED=$((WAITED+2))
  echo -n "."
  if [[ $WAITED -ge $MAX_WAIT ]]; then
    echo ""
    warn "백엔드 준비 타임아웃 — 로그 확인: $INSTALL_DIR/logs/backend.log"
    break
  fi
done
echo ""
success "백엔드 시작 완료 (PID: $BACKEND_PID)"

# 종료 시 클린업
cleanup() {
  echo ""
  info "서비스 종료 중..."
  [[ -f "$INSTALL_DIR/logs/backend.pid" ]] && \
    kill "$(cat "$INSTALL_DIR/logs/backend.pid")" 2>/dev/null || true
  success "종료 완료"
}
trap cleanup EXIT INT TERM

# 완료 배너
echo ""
echo "════════════════════════════════════════════════════════"
echo -e "${BOLD}${GREEN}  설치 및 시작 완료!${NC}"
echo "════════════════════════════════════════════════════════"
echo "  프론트엔드:  http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo localhost):8501"
echo "  백엔드 API:  http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo localhost):8000/docs"
echo "  로그:        $INSTALL_DIR/logs/backend.log"
echo "  종료:        Ctrl+C"
echo "════════════════════════════════════════════════════════"
echo ""
echo "  기본 계정:"
echo "    관리자: admin / Admin123!"
echo "    데모:   demo_user_1 / Demo123!"
echo "════════════════════════════════════════════════════════"
echo ""

# 프론트엔드 포그라운드 실행
cd "$INSTALL_DIR/frontend"
uv run --python 3.11 streamlit run app/main.py \
  --server.port 8501 \
  --server.address 0.0.0.0 \
  --server.headless true
