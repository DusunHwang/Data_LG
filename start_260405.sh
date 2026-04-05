#!/usr/bin/env bash
# =============================================================================
#  Data_LG — 서비스 시작 스크립트 (setup_260405.sh 완료 후 사용)
#
#  사용법:
#      bash start_260405.sh
#
#  vLLM 주소를 변경하려면 .env_vllm 파일에 입력:
#      VLLM_ENDPOINT=http://192.168.1.100:8000/v1
#
#  backend/.env 가 없으면 setup_260405.sh 를 먼저 실행하세요.
# =============================================================================
set -euo pipefail

R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' C='\033[0;36m' B='\033[1m' N='\033[0m'
info()  { echo -e "${C}[INFO]${N}  $*"; }
ok()    { echo -e "${G}[ OK ]${N}  $*"; }
warn()  { echo -e "${Y}[WARN]${N}  $*"; }
err()   { echo -e "${R}[ERR ]${N}  $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
mkdir -p logs

echo ""
echo -e "${B}${C}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║   Data_LG — 서비스 시작                     ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${N}"

# ── 사전 확인 ─────────────────────────────────────────────────────────────────
if [[ ! -f "$SCRIPT_DIR/backend/.env" ]]; then
  err "backend/.env 가 없습니다. 먼저 setup_260405.sh 를 실행하세요."
fi

if ! command -v uv &>/dev/null; then
  err "uv 가 설치되어 있지 않습니다. setup_260405.sh 를 먼저 실행하세요."
fi

if ! command -v npm &>/dev/null; then
  err "npm 이 설치되어 있지 않습니다."
fi

# ── .env_vllm 로 vLLM 주소 업데이트 (선택) ───────────────────────────────────
if [[ -f "$SCRIPT_DIR/.env_vllm" ]]; then
  _ep=$(grep -E '^VLLM_ENDPOINT=' "$SCRIPT_DIR/.env_vllm" 2>/dev/null | cut -d'=' -f2- || true)
  if [[ -n "$_ep" ]]; then
    _ep="${_ep%/}"
    [[ "$_ep" != */v1 ]] && _ep="${_ep}/v1"

    info "vLLM 주소 업데이트 중: $_ep"

    # 모델 자동 조회
    _models=$(curl -sf --max-time 8 "${_ep}/models" 2>/dev/null | \
      python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    ids = [m['id'] for m in data.get('data', [])]
    print('\n'.join(ids))
except Exception:
    pass
" 2>/dev/null || true)

    if [[ -n "$_models" ]]; then
      _count=$(echo "$_models" | grep -c . || true)
      if [[ $_count -eq 1 ]]; then
        _model=$(echo "$_models" | head -1)
        ok "모델 자동 선택: $_model"
      else
        echo ""
        echo -e "  ${B}사용 가능한 모델 목록:${N}"
        _idx=1
        while IFS= read -r m; do
          printf "    [%d] %s\n" "$_idx" "$m"
          _idx=$((_idx+1))
        done <<< "$_models"
        echo ""
        read -rp "  모델 번호 선택 [1]: " _sel
        _sel="${_sel:-1}"
        _model=$(echo "$_models" | sed -n "${_sel}p")
        [[ -z "$_model" ]] && _model=$(echo "$_models" | head -1)
        ok "선택된 모델: $_model"
      fi

      sed -i "s|^VLLM_ENDPOINT_SMALL=.*|VLLM_ENDPOINT_SMALL=${_ep}|"    "$SCRIPT_DIR/backend/.env"
      sed -i "s|^VLLM_MODEL_SMALL=.*|VLLM_MODEL_SMALL=${_model}|"        "$SCRIPT_DIR/backend/.env"
      ok "backend/.env 업데이트 완료"
    else
      warn "vLLM 연결 실패 — 기존 설정 유지 (확인: backend/.env)"
    fi
  fi
fi

# ── 현재 설정 표시 ────────────────────────────────────────────────────────────
_cur_ep=$(grep -E '^VLLM_ENDPOINT_SMALL=' "$SCRIPT_DIR/backend/.env" 2>/dev/null | cut -d'=' -f2- || echo "미설정")
_cur_model=$(grep -E '^VLLM_MODEL_SMALL=' "$SCRIPT_DIR/backend/.env" 2>/dev/null | cut -d'=' -f2- || echo "미설정")
echo ""
echo "  ┌──────────────────────────────────────────────┐"
printf "  │  엔드포인트: %-30s │\n" "$_cur_ep"
printf "  │  모델:       %-30s │\n" "$_cur_model"
echo "  └──────────────────────────────────────────────┘"
echo ""

# ── 기존 프로세스 정리 ────────────────────────────────────────────────────────
pkill -f "uvicorn app.main:app" 2>/dev/null || true
pkill -f "vite.*3000"           2>/dev/null || true
sleep 1

# ── 백엔드 시작 ───────────────────────────────────────────────────────────────
info "백엔드 시작 중 (포트 8000)..."
cd "$SCRIPT_DIR/backend"
nohup uv run uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 --log-level info \
  > "$SCRIPT_DIR/logs/backend.log" 2>&1 &
echo $! > "$SCRIPT_DIR/logs/backend.pid"

# 준비 대기
printf "  대기 중"
MAX_WAIT=40; WAITED=0
while ! curl -sf http://localhost:8000/docs &>/dev/null; do
  printf "."; sleep 2; WAITED=$((WAITED+2))
  [[ $WAITED -ge $MAX_WAIT ]] && { echo ""; warn "백엔드 타임아웃 — 로그: logs/backend.log"; break; }
done
echo ""
ok "백엔드 시작 완료 (PID: $(cat "$SCRIPT_DIR/logs/backend.pid"))"

# ── 프론트엔드 시작 ───────────────────────────────────────────────────────────
info "프론트엔드 시작 중 (포트 3000)..."
cd "$SCRIPT_DIR/frontend-react"
nohup npm run dev -- --host 0.0.0.0 \
  > "$SCRIPT_DIR/logs/frontend.log" 2>&1 &
echo $! > "$SCRIPT_DIR/logs/frontend.pid"

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
echo "  ║           서비스 시작 완료!                     ║"
echo "  ╠══════════════════════════════════════════════════╣"
printf "  ║  프론트엔드:  http://%-26s║\n" "${HOST_IP}:3000  "
printf "  ║  백엔드 API:  http://%-26s║\n" "${HOST_IP}:8000/docs  "
echo "  ╠══════════════════════════════════════════════════╣"
echo "  ║  기본 계정                                      ║"
echo "  ║    admin       /  Admin123!                     ║"
echo "  ║    demo_user_1 /  Demo123!                      ║"
echo "  ╠══════════════════════════════════════════════════╣"
echo "  ║  로그:  logs/backend.log  /  logs/frontend.log  ║"
echo "  ║  종료:  Ctrl+C                                  ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${N}"

# 로그 실시간 출력 (Ctrl+C로 종료)
echo -e "${C}[백엔드 로그 — Ctrl+C 로 종료]${N}"
tail -f "$SCRIPT_DIR/logs/backend.log"
