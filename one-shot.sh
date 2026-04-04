#!/usr/bin/env bash
# =============================================================================
#  Data_LG  —  One-Shot 설치·실행 스크립트
#
#  사용법:
#      sudo bash one-shot.sh
#
#  진행 순서:
#    1) 시스템 패키지 설치 (git, curl, python3, 한글폰트 등)
#    2) uv 설치 / Python 3.11 확보
#    3) GitHub에서 소스코드 클론
#    4) vLLM 엔드포인트 입력 → 모델 목록 자동 조회 → 자동 선택
#    5) 의존성 설치 / DB 초기화 / 데이터셋 생성
#    6) 백엔드 + 프론트엔드 서비스 시작
# =============================================================================
set -euo pipefail

# ── 색상/로그 ──────────────────────────────────────────────────────────────────
R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' C='\033[0;36m' B='\033[1m' N='\033[0m'
info()  { echo -e "${C}[INFO]${N}  $*"; }
ok()    { echo -e "${G}[ OK ]${N}  $*"; }
warn()  { echo -e "${Y}[WARN]${N}  $*"; }
err()   { echo -e "${R}[ERR ]${N}  $*" >&2; exit 1; }
banner(){ echo -e "\n${B}${C}── $* ──${N}"; }

# ── 설정 ───────────────────────────────────────────────────────────────────────
REPO_URL="https://github.com/DusunHwang/Data_LG.git"
GIT_BRANCH="backend-simplify"
INSTALL_DIR="/opt/data_lg"
LOG_DIR="$INSTALL_DIR/logs"

# ── 배너 ───────────────────────────────────────────────────────────────────────
clear 2>/dev/null || true
echo -e "${B}${G}"
cat <<'BANNER'
  ╔══════════════════════════════════════════════╗
  ║   Data_LG  —  회귀 분석 플랫폼              ║
  ║   One-Shot 자동 설치 스크립트               ║
  ╚══════════════════════════════════════════════╝
BANNER
echo -e "${N}"

# ── root 확인 ──────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
  err "root 권한이 필요합니다.  →  sudo bash $0"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1  시스템 패키지
# ══════════════════════════════════════════════════════════════════════════════
banner "STEP 1 / 5  시스템 패키지 설치"

install_pkgs_apt() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends \
    git curl wget ca-certificates \
    gcc g++ make libffi-dev libssl-dev \
    fonts-nanum fontconfig \
    procps lsof python3 python3-pip \
    >/dev/null 2>&1
  fc-cache -fv >/dev/null 2>&1 || true
}

install_pkgs_apk() {
  apk add --no-cache \
    git curl wget ca-certificates \
    gcc g++ musl-dev libffi-dev openssl-dev \
    procps bash python3 py3-pip \
    >/dev/null 2>&1
}

install_pkgs_dnf() {
  dnf install -y \
    git curl wget ca-certificates \
    gcc gcc-c++ make libffi-devel openssl-devel \
    procps-ng python3 python3-pip \
    >/dev/null 2>&1
}

if   command -v apt-get &>/dev/null; then install_pkgs_apt; PM="apt"
elif command -v apk     &>/dev/null; then install_pkgs_apk; PM="apk"
elif command -v dnf     &>/dev/null; then install_pkgs_dnf; PM="dnf"
elif command -v yum     &>/dev/null; then PM="yum"
  yum install -y git curl wget ca-certificates gcc gcc-c++ python3 python3-pip \
    >/dev/null 2>&1
else
  warn "알 수 없는 패키지 매니저 — git, curl, python3이 이미 설치돼 있어야 합니다."
  PM="unknown"
fi
ok "시스템 패키지 완료 ($PM)"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2  uv + Python 3.11
# ══════════════════════════════════════════════════════════════════════════════
banner "STEP 2 / 5  uv 및 Python 3.11 설치"

export PATH="/root/.cargo/bin:/root/.local/bin:$PATH"

if ! command -v uv &>/dev/null; then
  info "uv 설치 중..."
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
  export PATH="/root/.cargo/bin:/root/.local/bin:$PATH"
fi
ok "uv $(uv --version)"

# Python 3.11 이 없으면 uv로 설치
if ! uv python find 3.11 &>/dev/null 2>&1; then
  info "Python 3.11 설치 중..."
  uv python install 3.11 >/dev/null 2>&1
fi
ok "Python $(uv run --python 3.11 python3 --version 2>&1)"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3  소스코드 클론
# ══════════════════════════════════════════════════════════════════════════════
banner "STEP 3 / 5  소스코드 준비"

if [[ -d "$INSTALL_DIR/.git" ]]; then
  info "기존 설치 발견 — 최신 코드로 업데이트 중..."
  cd "$INSTALL_DIR"
  git fetch -q origin
  git checkout -q "$GIT_BRANCH"
  git pull -q origin "$GIT_BRANCH"
  ok "코드 업데이트 완료"
else
  info "GitHub에서 클론 중..."
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone -q --branch "$GIT_BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
  ok "클론 완료: $INSTALL_DIR"
fi

cd "$INSTALL_DIR"
mkdir -p "$LOG_DIR"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4  vLLM 설정 (엔드포인트 입력 → 모델 자동 조회)
# ══════════════════════════════════════════════════════════════════════════════
banner "STEP 4 / 5  vLLM 서버 설정"

# .env 초기화
[[ -f .env ]] || cp .env.simple .env

echo ""
echo -e "  ${B}외부 vLLM 서버 주소를 입력하세요.${N}"
echo -e "  예) http://192.168.1.100:8000/v1"
echo ""

# ── 엔드포인트 입력 (재시도 최대 3회) ─────────────────────────────────────────
fetch_models() {
  # /v1/models 또는 /models 모두 시도
  local ep="$1"
  local result
  result=$(curl -sf --max-time 8 "${ep}/models" 2>/dev/null | \
    python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    ids = [m['id'] for m in data.get('data', [])]
    print('\n'.join(ids))
except Exception:
    pass
" 2>/dev/null)
  echo "$result"
}

SELECTED_ENDPOINT=""
SELECTED_MODEL=""
MAX_TRIES=3

for attempt in $(seq 1 $MAX_TRIES); do
  read -rp "  vLLM 엔드포인트: " _raw_ep
  # 끝의 슬래시 제거, /v1 없으면 붙이기
  _raw_ep="${_raw_ep%/}"
  [[ "$_raw_ep" != */v1 ]] && _raw_ep="${_raw_ep}/v1"

  info "연결 확인 중: $_raw_ep"
  MODELS=$(fetch_models "$_raw_ep")

  if [[ -z "$MODELS" ]]; then
    warn "모델 조회 실패 (시도 $attempt/$MAX_TRIES) — 주소를 다시 확인하세요."
    [[ $attempt -lt $MAX_TRIES ]] && continue
    err "vLLM 서버에 연결할 수 없습니다. 서버 상태와 주소를 확인하세요."
  fi

  SELECTED_ENDPOINT="$_raw_ep"

  # ── 모델 자동 선택 ──────────────────────────────────────────────────────────
  MODEL_COUNT=$(echo "$MODELS" | grep -c . || true)

  if [[ $MODEL_COUNT -eq 1 ]]; then
    SELECTED_MODEL=$(echo "$MODELS" | head -1)
    ok "모델 자동 선택: $SELECTED_MODEL"
  else
    echo ""
    echo -e "  ${B}사용 가능한 모델 목록:${N}"
    IDX=1
    while IFS= read -r m; do
      printf "    [%d] %s\n" "$IDX" "$m"
      IDX=$((IDX+1))
    done <<< "$MODELS"
    echo ""
    read -rp "  모델 번호 선택 [1]: " _model_idx
    _model_idx="${_model_idx:-1}"
    SELECTED_MODEL=$(echo "$MODELS" | sed -n "${_model_idx}p")
    if [[ -z "$SELECTED_MODEL" ]]; then
      SELECTED_MODEL=$(echo "$MODELS" | head -1)
    fi
    ok "선택된 모델: $SELECTED_MODEL"
  fi
  break
done

# .env 업데이트
sed -i "s|^VLLM_ENDPOINT_SMALL=.*|VLLM_ENDPOINT_SMALL=${SELECTED_ENDPOINT}|" .env
sed -i "s|^VLLM_MODEL_SMALL=.*|VLLM_MODEL_SMALL=${SELECTED_MODEL}|"         .env

echo ""
echo -e "  ${G}설정 완료${N}"
echo "  ┌──────────────────────────────────────────────┐"
printf "  │  엔드포인트: %-30s │\n" "$SELECTED_ENDPOINT"
printf "  │  모델:       %-30s │\n" "$SELECTED_MODEL"
echo "  └──────────────────────────────────────────────┘"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5  의존성 설치 / DB 초기화 / 서비스 시작
# ══════════════════════════════════════════════════════════════════════════════
banner "STEP 5 / 5  설치 및 서비스 시작"

# ── 백엔드 의존성 ──────────────────────────────────────────────────────────────
info "백엔드 의존성 설치 중..."
cd "$INSTALL_DIR/backend"
uv sync --python 3.11 -q 2>/dev/null || uv pip install --python 3.11 -e ".[dev]" -q
ok "백엔드 의존성 완료"

# ── DB 마이그레이션 + 시드 ─────────────────────────────────────────────────────
info "DB 초기화 중..."
mkdir -p data
uv run --python 3.11 alembic upgrade head -q
ok "DB 마이그레이션 완료"

uv run --python 3.11 python -m app.db.seed >/dev/null 2>&1 && ok "시드 데이터 완료" || \
  warn "시드 스킵 (이미 존재)"

# ── 내장 데이터셋 ──────────────────────────────────────────────────────────────
PARQUET_COUNT=$(ls "$INSTALL_DIR/datasets_builtin/"*.parquet 2>/dev/null | wc -l || echo 0)
if [[ "$PARQUET_COUNT" -lt 1 ]]; then
  info "내장 데이터셋 생성 중..."
  cd "$INSTALL_DIR"
  uv run --python 3.11 --project backend \
    python datasets_builtin/generate_datasets.py >/dev/null 2>&1 && \
    ok "데이터셋 생성 완료" || warn "데이터셋 생성 실패 (나중에 수동 실행 가능)"
else
  ok "내장 데이터셋 존재 (${PARQUET_COUNT}개)"
fi

# ── 프론트엔드 의존성 ──────────────────────────────────────────────────────────
info "프론트엔드 의존성 설치 중..."
cd "$INSTALL_DIR/frontend"
uv sync --python 3.11 -q 2>/dev/null || uv pip install --python 3.11 -e . -q
ok "프론트엔드 의존성 완료"

# ── 기존 프로세스 정리 ─────────────────────────────────────────────────────────
pkill -f "uvicorn app.main:app" 2>/dev/null || true
pkill -f "streamlit run"        2>/dev/null || true
sleep 1

# ── 백엔드 시작 ────────────────────────────────────────────────────────────────
info "백엔드 시작 중..."
cd "$INSTALL_DIR/backend"
nohup uv run --python 3.11 uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 --log-level info \
  > "$LOG_DIR/backend.log" 2>&1 &
echo $! > "$LOG_DIR/backend.pid"

# 준비 대기
MAX_WAIT=40; WAITED=0
printf "  대기 중"
while ! curl -sf http://localhost:8000/docs &>/dev/null; do
  printf "."; sleep 2; WAITED=$((WAITED+2))
  [[ $WAITED -ge $MAX_WAIT ]] && { echo ""; warn "백엔드 타임아웃 — 로그: $LOG_DIR/backend.log"; break; }
done
echo ""
ok "백엔드 시작 완료 (PID: $(cat "$LOG_DIR/backend.pid"))"

# ── 종료 시 클린업 ─────────────────────────────────────────────────────────────
cleanup() {
  echo ""
  info "종료 중..."
  kill "$(cat "$LOG_DIR/backend.pid" 2>/dev/null)" 2>/dev/null || true
  ok "종료 완료"
}
trap cleanup EXIT INT TERM

# ── 완료 배너 ──────────────────────────────────────────────────────────────────
HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
echo ""
echo -e "${B}${G}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║           설치 및 시작 완료!                    ║"
echo "  ╠══════════════════════════════════════════════════╣"
printf "  ║  프론트엔드:  http://%-26s║\n" "${HOST_IP}:8501  "
printf "  ║  백엔드 API:  http://%-26s║\n" "${HOST_IP}:8000/docs  "
echo "  ╠══════════════════════════════════════════════════╣"
echo "  ║  기본 계정                                      ║"
echo "  ║    admin       /  Admin123!                     ║"
echo "  ║    demo_user_1 /  Demo123!                      ║"
echo "  ╠══════════════════════════════════════════════════╣"
printf "  ║  로그: %-42s║\n" "$LOG_DIR/backend.log  "
echo "  ║  종료: Ctrl+C                                   ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${N}"

# ── 프론트엔드 (포그라운드) ────────────────────────────────────────────────────
cd "$INSTALL_DIR/frontend"
uv run --python 3.11 streamlit run app/main.py \
  --server.port 8501 \
  --server.address 0.0.0.0 \
  --server.headless true
