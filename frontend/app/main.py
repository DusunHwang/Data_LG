"""회귀 분석 플랫폼 - Streamlit 메인"""

import json
import os
import re
import time
from datetime import datetime

import httpx
import pandas as pd
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
API_BASE = f"{BACKEND_URL}/api/v1"
POLL_INTERVAL = 5  # 5초 폴링

# ─────────────────────────────────────────────
# vLLM 모니터 설정
# ─────────────────────────────────────────────
VLLM_METRICS_URL = os.getenv("VLLM_METRICS_URL", "http://your-vllm-server/metrics")


def _extract_vllm_metric(text: str, name_patterns: list) -> float:
    for name in name_patterns:
        pattern = rf'{re.escape(name)}\{{.*?\}}\s+([\d\.e\-\+]+)'
        match = re.search(pattern, text)
        if match:
            try:
                return float(match.group(1))
            except Exception:
                continue
    return 0.0


def _get_vllm_metrics() -> dict:
    try:
        resp = httpx.get(VLLM_METRICS_URL, timeout=1.5)
        if resp.status_code == 200:
            text = resp.text
            gpu_val = _extract_vllm_metric(
                text, ["vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc"]
            ) * 100
            if gpu_val == 0.0:
                gpu_val = 0.001
            return {
                "time": datetime.now().strftime("%H:%M:%S"),
                "gpu": gpu_val,
                "run": _extract_vllm_metric(text, ["vllm:num_requests_running"]),
                "wait": _extract_vllm_metric(text, ["vllm:num_requests_waiting"]),
                "gen": _extract_vllm_metric(text, ["vllm:generation_tokens_total"]),
            }
    except Exception:
        pass
    return {
        "time": datetime.now().strftime("%H:%M:%S"),
        "gpu": 0.0, "run": 0.0, "wait": 0.0, "gen": 0.0,
    }


@st.fragment(run_every=1)
def _render_vllm_monitor():
    """vLLM 서버 모니터 — KV-Cache 및 토큰 생성 속도 실시간 차트"""
    if "vllm_history" not in st.session_state:
        st.session_state.vllm_history = pd.DataFrame(
            columns=["time", "gpu", "run", "wait", "gen"]
        )

    current = _get_vllm_metrics()
    new_row = pd.DataFrame([current])
    st.session_state.vllm_history = pd.concat(
        [st.session_state.vllm_history, new_row]
    ).tail(120)
    hist = st.session_state.vllm_history

    st.caption("⚡ GPU MEM TREND")
    st.line_chart(hist.set_index("time")[["gpu"]], height=130, color=["#76b900"])

    st.caption("🚀 GEN TOKENS / SEC")
    gen_data = hist.copy().set_index("time")[["gen"]]
    if len(gen_data) > 1:
        gen_data["gen"] = gen_data["gen"].diff().fillna(0).clip(lower=0)
    else:
        gen_data["gen"] = 0
    st.line_chart(gen_data, height=130, color=["#00aaff"])

    gpu_display = f"{current['gpu']:.1f}%"
    c1, c2, c3 = st.columns(3)
    c1.metric("KV-Cache", gpu_display)
    c2.metric("실행중", int(current["run"]))
    c3.metric("대기중", int(current["wait"]))

st.set_page_config(
    page_title="회귀 분석 플랫폼",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
# 전역 CSS: 80% 스케일 (더 많은 정보 표시)
# ─────────────────────────────────────────────
st.markdown("""
<style>
/* 전체 폰트 80% */
html, body, [class*="css"] {
    font-size: 12.8px !important;
}

/* 제목 크기 축소 */
h1 { font-size: 1.5rem !important; }
h2 { font-size: 1.25rem !important; }
h3 { font-size: 1.1rem !important; }
h4, h5, h6 { font-size: 1rem !important; }

/* 버튼 패딩/크기 축소 */
.stButton > button {
    font-size: 0.78rem !important;
    padding: 0.25rem 0.6rem !important;
    min-height: 1.8rem !important;
    line-height: 1.2 !important;
}

/* 입력 필드 축소 */
.stTextInput > div > div > input,
.stTextArea > div > div > textarea,
.stSelectbox > div > div > div,
.stNumberInput > div > div > input {
    font-size: 0.78rem !important;
    padding: 0.25rem 0.5rem !important;
    min-height: 1.8rem !important;
}

/* 라벨 축소 */
.stTextInput > label,
.stTextArea > label,
.stSelectbox > label,
.stNumberInput > label,
.stRadio > label,
.stCheckbox > label {
    font-size: 0.75rem !important;
}

/* 사이드바 너비 축소 */
section[data-testid="stSidebar"] {
    min-width: 220px !important;
    max-width: 260px !important;
}
section[data-testid="stSidebar"] .block-container {
    padding: 0.5rem 0.6rem !important;
}

/* 메인 영역 패딩 축소 */
.main .block-container {
    padding-top: 0.5rem !important;
    padding-bottom: 0.5rem !important;
    max-width: 100% !important;
}

/* 구분선 마진 축소 */
hr { margin: 0.3rem 0 !important; }

/* 진행바 높이 축소 */
.stProgress > div > div > div {
    height: 0.4rem !important;
}

/* expander 패딩 축소 */
.streamlit-expanderHeader {
    font-size: 0.78rem !important;
    padding: 0.3rem 0.5rem !important;
}
.streamlit-expanderContent {
    padding: 0.3rem 0.5rem !important;
}

/* 탭 축소 */
.stTabs [data-baseweb="tab"] {
    font-size: 0.78rem !important;
    padding: 0.3rem 0.7rem !important;
}

/* 데이터프레임 폰트 */
.stDataFrame {
    font-size: 0.75rem !important;
}

/* 알림 박스 축소 */
.stAlert {
    padding: 0.4rem 0.6rem !important;
    font-size: 0.78rem !important;
}

/* 캡션 */
.stCaption {
    font-size: 0.72rem !important;
}

/* 채팅 메시지 */
.stChatMessage {
    padding: 0.3rem 0.5rem !important;
    font-size: 0.78rem !important;
}

/* 이미지 기본 여백 축소 */
.stImage {
    margin: 0.2rem 0 !important;
}

/* 수직 여백 줄이기 */
div[data-testid="stVerticalBlock"] > div {
    gap: 0.3rem !important;
}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# 공통 헬퍼
# ─────────────────────────────────────────────

def get_headers() -> dict:
    """인증 헤더 반환"""
    token = st.session_state.get("access_token")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def api_call(method: str, path: str, **kwargs):
    """API 호출 헬퍼 - 실패 시 None 반환"""
    try:
        with httpx.Client(
            base_url=API_BASE, timeout=30.0, headers=get_headers()
        ) as client:
            response = getattr(client, method)(path, **kwargs)
            if response.status_code == 401:
                st.session_state.clear()
                st.rerun()
            return response.json()
    except httpx.ConnectError:
        st.error("백엔드 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.")
        return None
    except Exception as e:
        st.error(f"API 오류: {e}")
        return None


def api_upload(path: str, files: dict) -> dict | None:
    """파일 업로드 전용 API 호출"""
    try:
        with httpx.Client(
            base_url=API_BASE, timeout=120.0, headers=get_headers()
        ) as client:
            response = client.post(path, files=files)
            if response.status_code == 401:
                st.session_state.clear()
                st.rerun()
            return response.json()
    except httpx.ConnectError:
        st.error("백엔드 서버에 연결할 수 없습니다.")
        return None
    except Exception as e:
        st.error(f"업로드 오류: {e}")
        return None


def check_auth() -> bool:
    """인증 상태 확인"""
    return bool(st.session_state.get("access_token"))


# ─────────────────────────────────────────────
# 로그인 페이지
# ─────────────────────────────────────────────

def login_page():
    """로그인 페이지"""
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.title("📊 회귀 분석 플랫폼")
        st.subheader("로그인")
        st.caption("다중 턴 테이블형 회귀 분석 시스템")
        st.divider()

        with st.form("login_form"):
            username = st.text_input("사용자명", placeholder="예: demo_user_1")
            password = st.text_input(
                "비밀번호", type="password", placeholder="비밀번호 입력"
            )
            submit = st.form_submit_button("로그인", use_container_width=True)

        if submit:
            if not username or not password:
                st.error("사용자명과 비밀번호를 모두 입력하세요.")
                return

            try:
                with httpx.Client(base_url=BACKEND_URL, timeout=10.0) as client:
                    resp = client.post(
                        "/api/v1/auth/login",
                        json={"username": username, "password": password},
                    )

                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success"):
                        token_data = data["data"]
                        st.session_state.access_token = token_data["access_token"]
                        st.session_state.refresh_token = token_data.get("refresh_token")
                        st.session_state.username = username
                        st.session_state.active_job_id = None
                        st.success("로그인 성공!")
                        st.rerun()
                    else:
                        err = data.get("error", {})
                        st.error(err.get("message", "로그인에 실패했습니다."))
                elif resp.status_code == 401:
                    err_data = resp.json()
                    err = err_data.get("error", {})
                    if isinstance(err, dict):
                        msg = err.get("message", "인증 정보가 올바르지 않습니다.")
                    else:
                        msg = "인증 정보가 올바르지 않습니다."
                    st.error(msg)
                else:
                    st.error(f"서버 오류 ({resp.status_code}). 잠시 후 다시 시도하세요.")
            except httpx.ConnectError:
                st.error("백엔드 서버에 연결할 수 없습니다.")
            except Exception as exc:
                st.error(f"오류가 발생했습니다: {exc}")

        st.divider()
        st.caption("기본 계정: demo_user_1 / Demo123!")


# ─────────────────────────────────────────────
# 사이드바
# ─────────────────────────────────────────────

def render_sidebar():
    """사이드바 렌더링"""
    with st.sidebar:
        # ── vLLM 모니터 (최상단) ───────────────
        _render_vllm_monitor()
        st.divider()

        # 사용자 정보 + 로그아웃
        col_user, col_logout = st.columns([3, 1])
        with col_user:
            st.markdown(f"**{st.session_state.get('username', '사용자')}**")
        with col_logout:
            if st.button("나가기", key="logout_btn"):
                _do_logout()

        st.divider()

        # ── 세션 관리 ──────────────────────────
        st.markdown("### 📁 세션 관리")
        if st.button("+ 새 세션 만들기", use_container_width=True, key="new_session_btn"):
            st.session_state.show_create_session = True

        if st.session_state.get("show_create_session"):
            _render_create_session_form()

        # 세션 목록 가져오기
        sessions_result = api_call("get", "/sessions")
        sessions = []
        if sessions_result and sessions_result.get("success"):
            sessions = sessions_result["data"]

        if sessions:
            st.caption(f"총 {len(sessions)}개 세션")
            for sess in sessions:
                sess_id = str(sess["id"])
                is_active = st.session_state.get("current_session_id") == sess_id
                col_sess, col_del = st.columns([5, 1])
                with col_sess:
                    label = f"{'▶ ' if is_active else ''}{sess['name']}"
                    if st.button(label, key=f"sess_{sess_id}", use_container_width=True):
                        if not is_active:
                            st.session_state.current_session_id = sess_id
                            st.session_state.current_session_name = sess["name"]
                            st.session_state.current_dataset_id = None
                            st.session_state.current_dataset_name = None
                            st.session_state.active_job_id = None
                            st.session_state.selected_step_id = None
                            st.session_state.target_column = None
                            st.session_state.selected_branch_id = None
                            # 복원 플래그 초기화 → main()에서 자동 복원
                            st.session_state.pop(f"_restored_{sess_id}", None)
                        st.rerun()
                with col_del:
                    if is_active:
                        if st.button("🗑", key=f"del_{sess_id}", help="세션 삭제"):
                            st.session_state[f"confirm_delete_{sess_id}"] = True

                # 삭제 확인 팝업
                if st.session_state.get(f"confirm_delete_{sess_id}"):
                    st.warning(f"**'{sess['name']}'** 세션을 삭제하시겠습니까?")
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("삭제 확인", key=f"confirm_yes_{sess_id}", type="primary"):
                            result = api_call("delete", f"/sessions/{sess_id}")
                            if result and result.get("success"):
                                # 삭제된 세션의 채팅 히스토리 제거
                                histories = st.session_state.get("chat_histories", {})
                                histories.pop(sess_id, None)
                                st.session_state.chat_histories = histories
                                # 현재 세션이 삭제된 세션이면 초기화
                                if st.session_state.get("current_session_id") == sess_id:
                                    st.session_state.current_session_id = None
                                    st.session_state.current_session_name = None
                                    st.session_state.current_dataset_id = None
                                    st.session_state.current_dataset_name = None
                                    st.session_state.active_job_id = None
                                    st.session_state.selected_step_id = None
                                    st.session_state.target_column = None
                                st.session_state.pop(f"confirm_delete_{sess_id}", None)
                                st.rerun()
                            else:
                                st.error("삭제 실패")
                    with c2:
                        if st.button("취소", key=f"confirm_no_{sess_id}"):
                            st.session_state.pop(f"confirm_delete_{sess_id}", None)
                            st.rerun()
        else:
            st.caption("세션이 없습니다.")

        st.divider()

        # ── 데이터셋 ──────────────────────────
        session_id = st.session_state.get("current_session_id")
        st.markdown("### 📂 데이터셋")

        if not session_id:
            st.caption("세션을 선택하세요.")
        else:
            current_ds = st.session_state.get("current_dataset_name")
            if current_ds:
                st.success(f"현재: {current_ds}")
            else:
                st.caption("데이터셋 없음")

            # 파일 업로드
            uploaded = st.file_uploader(
                "파일 업로드 (CSV/Parquet)",
                type=["csv", "xlsx", "parquet"],
                key="sidebar_uploader",
            )
            if uploaded and st.button("업로드", key="upload_btn"):
                _upload_dataset(session_id, uploaded)

            # 내장 데이터셋
            builtin_result = api_call("get", f"/sessions/{session_id}/datasets/builtin-list")
            builtin_list = []
            if builtin_result and builtin_result.get("success"):
                builtin_list = builtin_result["data"]

            if builtin_list:
                builtin_options = {d["name"]: d["key"] for d in builtin_list}
                selected_builtin = st.selectbox(
                    "내장 데이터셋 선택",
                    options=["(선택 안 함)"] + list(builtin_options.keys()),
                    key="builtin_select",
                )
                if selected_builtin != "(선택 안 함)":
                    if st.button("내장 데이터셋 사용", key="use_builtin_btn"):
                        _select_builtin_dataset(
                            session_id, builtin_options[selected_builtin], selected_builtin
                        )

        st.divider()

        # ── 타겟 컬럼 ──────────────────────────
        dataset_id = st.session_state.get("current_dataset_id")
        _render_sidebar_target_selector(session_id, dataset_id)

        st.divider()

        # ── 분석 단계 트리 ──────────────────────
        st.markdown("### 📊 분석 단계")
        _render_step_tree(session_id)


def _render_sidebar_target_selector(session_id: str | None, dataset_id: str | None):
    """사이드바 타겟 컬럼 선택기 — 항상 표시, 언제든 변경 가능"""
    st.markdown("### 🎯 타겟 컬럼")

    current_target = st.session_state.get("target_column")

    if current_target:
        st.success(f"**{current_target}**")
    else:
        st.warning("타겟 미설정")

    if not session_id or not dataset_id:
        # 데이터셋 없이도 직접 입력은 가능
        custom = st.text_input("컬럼명 직접 입력", key="sidebar_target_custom_nodataset", placeholder="예: quality")
        if st.button("설정", key="sidebar_target_set_nodataset", use_container_width=True):
            if custom.strip():
                st.session_state.target_column = custom.strip()
                st.rerun()
        return

    # 후보 목록 캐싱 (dataset_id 바뀔 때만 재조회)
    cache_key = f"_target_candidates_{dataset_id}"
    candidates = st.session_state.get(cache_key)
    if candidates is None:
        result = api_call("get", f"/sessions/{session_id}/datasets/{dataset_id}/target-candidates")
        if result and result.get("success"):
            candidates = result["data"].get("candidates", [])
        else:
            candidates = []
        st.session_state[cache_key] = candidates

    col_names = [c.get("column", c.get("name", "")) for c in candidates if c.get("column") or c.get("name")]

    # selectbox 옵션: 후보 컬럼 + 직접 입력
    CUSTOM_OPTION = "✏️ 직접 입력..."
    options = col_names + [CUSTOM_OPTION]
    current_idx = options.index(current_target) if current_target in options else len(options) - 1

    selected = st.selectbox(
        "컬럼 선택",
        options=options,
        index=current_idx,
        key="sidebar_target_select",
        label_visibility="collapsed",
    )

    if selected == CUSTOM_OPTION:
        custom = st.text_input("컬럼명 입력", key="sidebar_target_custom", placeholder="예: quality_score")
        target_value = custom.strip() if custom.strip() else None
    else:
        target_value = selected

    if st.button("타겟 설정", key="sidebar_target_btn", use_container_width=True, type="primary"):
        if target_value:
            st.session_state.target_column = target_value
            st.rerun()
        else:
            st.error("컬럼명을 입력하세요.")


def _do_logout():
    """로그아웃 처리"""
    refresh_token = st.session_state.get("refresh_token")
    if refresh_token:
        api_call("post", "/auth/logout", json={"refresh_token": refresh_token})
    st.session_state.clear()
    st.rerun()


def _render_create_session_form():
    """세션 생성 폼"""
    with st.form("create_session_form"):
        title = st.text_input("세션 이름", placeholder="새 분석 세션")
        ttl = st.number_input("유효 기간(일)", min_value=1, max_value=365, value=7)
        if st.form_submit_button("생성"):
            if not title.strip():
                st.error("세션 이름을 입력하세요.")
            else:
                result = api_call(
                    "post",
                    "/sessions",
                    json={"name": title.strip(), "ttl_days": int(ttl)},
                )
                if result and result.get("success"):
                    sess = result["data"]
                    st.session_state.current_session_id = str(sess["id"])
                    st.session_state.current_session_name = sess["name"]
                    st.session_state.current_dataset_id = None
                    st.session_state.current_dataset_name = None
                    st.session_state.show_create_session = False
                    st.success(f"세션 '{title}' 생성됨!")
                    st.rerun()
                else:
                    st.error("세션 생성에 실패했습니다.")


def _refresh_session_dataset(session_id: str):
    """세션의 활성 데이터셋 정보 갱신 (하위 호환 유지)"""
    _restore_session(session_id)


def _restore_session(session_id: str):
    """세션 복원: 데이터셋 + 목표변수 + 채팅 히스토리"""
    # 1. 히스토리 API 한 번에 호출 (target_column + chat_history + active_dataset_id 포함)
    history_result = api_call("get", f"/sessions/{session_id}/history")

    if history_result and history_result.get("success"):
        data = history_result["data"]

        # 데이터셋 복원
        active_ds_id = data.get("active_dataset_id")
        if active_ds_id:
            st.session_state.current_dataset_id = active_ds_id
            ds_list = api_call("get", f"/sessions/{session_id}/datasets")
            if ds_list and ds_list.get("success"):
                for ds in ds_list["data"]:
                    if str(ds["id"]) == str(active_ds_id):
                        st.session_state.current_dataset_name = ds.get(
                            "original_filename", ds.get("name", "데이터셋")
                        )
                        break

        # 목표 변수 복원
        target_column = data.get("target_column")
        if target_column:
            st.session_state.target_column = target_column

        # 브랜치 복원
        branch_id = data.get("branch_id")
        if branch_id:
            st.session_state.selected_branch_id = branch_id

        # 채팅 히스토리 복원 (이미 로드된 게 없을 때만)
        chat_history = data.get("chat_history", [])
        if chat_history:
            if "chat_histories" not in st.session_state:
                st.session_state.chat_histories = {}
            # 기존 인메모리 히스토리가 없으면 DB에서 복원
            existing = st.session_state.chat_histories.get(session_id, [])
            if not existing:
                st.session_state.chat_histories[session_id] = chat_history
                # 마지막 어시스턴트 메시지의 step 자동 선택
                for msg in reversed(chat_history):
                    if msg.get("role") == "assistant" and msg.get("step_id"):
                        st.session_state.selected_step_id = msg["step_id"]
                        if msg.get("branch_id"):
                            st.session_state.selected_branch_id = msg["branch_id"]
                        break
    else:
        # 히스토리 API 실패 시 기존 방식으로 데이터셋만 복원
        result = api_call("get", f"/sessions/{session_id}")
        if result and result.get("success"):
            sess_data = result["data"]
            active_ds_id = sess_data.get("active_dataset_id")
            if active_ds_id:
                st.session_state.current_dataset_id = active_ds_id
                ds_list = api_call("get", f"/sessions/{session_id}/datasets")
                if ds_list and ds_list.get("success"):
                    for ds in ds_list["data"]:
                        if str(ds["id"]) == str(active_ds_id):
                            st.session_state.current_dataset_name = ds.get(
                                "original_filename", ds.get("name", "데이터셋")
                            )
                            break


def _upload_dataset(session_id: str, uploaded_file):
    """데이터셋 업로드"""
    with st.spinner("업로드 중..."):
        files = {
            "file": (
                uploaded_file.name,
                uploaded_file.getvalue(),
                uploaded_file.type or "application/octet-stream",
            )
        }
        result = api_upload(f"/sessions/{session_id}/datasets/upload", files)
    if result and result.get("success"):
        ds = result["data"]
        st.session_state.current_dataset_id = str(ds["id"])
        st.session_state.current_dataset_name = ds.get(
            "original_filename", ds.get("name", "데이터셋")
        )
        # 새 데이터셋 → 이전 target 초기화 (선택 화면 다시 표시)
        st.session_state.target_column = None
        st.success(f"업로드 완료: {uploaded_file.name}")
        st.rerun()
    else:
        if result:
            err = result.get("error", {})
            st.error(f"업로드 실패: {err.get('message', '알 수 없는 오류')}")
        else:
            st.error("업로드 실패")


def _select_builtin_dataset(session_id: str, builtin_key: str, display_name: str):
    """내장 데이터셋 선택"""
    with st.spinner("데이터셋 로드 중..."):
        result = api_call(
            "post",
            f"/sessions/{session_id}/datasets/builtin",
            json={"builtin_key": builtin_key},
        )
    if result and result.get("success"):
        ds = result["data"]
        st.session_state.current_dataset_id = str(ds["id"])
        st.session_state.current_dataset_name = display_name
        # 새 데이터셋 → 이전 target 초기화 (선택 화면 다시 표시)
        st.session_state.target_column = None
        st.success(f"데이터셋 선택: {display_name}")
        st.rerun()
    else:
        if result:
            err = result.get("error", {})
            st.error(f"데이터셋 선택 실패: {err.get('message', '알 수 없는 오류')}")
        else:
            st.error("데이터셋 선택 실패")


def _render_step_tree(session_id: str | None):
    """분석 단계 트리"""
    if not session_id:
        st.caption("세션을 선택하면 분석 단계가 표시됩니다.")
        return

    # 세션의 브랜치 조회 (branches 엔드포인트 사용)
    branches_result = api_call("get", f"/sessions/{session_id}/branches")
    if not branches_result or not branches_result.get("success"):
        st.caption("분석 단계 없음")
        return

    branches = branches_result.get("data", [])
    if not branches:
        st.caption("분석 단계 없음")
        return

    active_branch_id = st.session_state.get("selected_branch_id")

    for branch in branches:
        branch_id = str(branch["id"])
        branch_name = branch.get("name", "기본 브랜치")
        branch_config = branch.get("config") or {}
        is_active_branch = (branch_id == active_branch_id)

        # 이 브랜치가 사용하는 데이터셋 표시
        branch_dataset_path = branch_config.get("dataset_path")
        if branch_dataset_path:
            ds_label = f"📂 {branch_dataset_path.split('/')[-1]}"
        else:
            ds_name = st.session_state.get("current_dataset_name", "원본 데이터셋")
            ds_label = f"📂 {ds_name}"

        expander_label = f"{'▶ ' if is_active_branch else '🌿 '}{branch_name}"
        with st.expander(expander_label, expanded=is_active_branch):
            # 데이터셋 정보 + 활성화 버튼
            st.caption(ds_label)
            if not is_active_branch:
                if st.button("이 브랜치로 전환", key=f"activate_branch_{branch_id}",
                             use_container_width=True, type="primary"):
                    st.session_state.selected_branch_id = branch_id
                    st.session_state.selected_step_id = None
                    st.rerun()
            else:
                st.success("현재 분석 브랜치", icon="✅")

            steps_result = api_call(
                "get",
                f"/sessions/{session_id}/branches/{branch_id}/steps",
            )
            if steps_result and steps_result.get("success"):
                steps = steps_result["data"]
                if steps:
                    for step in steps[-10:]:  # 최근 10개
                        step_id = str(step["id"])
                        is_selected = st.session_state.get("selected_step_id") == step_id
                        step_title = step.get("title", step.get("step_type", "분석"))
                        icon = "✅" if step.get("status") == "completed" else "⏳"
                        label = f"{'→ ' if is_selected else ''}{icon} {step_title}"
                        if st.button(label, key=f"step_{step_id}", use_container_width=True):
                            st.session_state.selected_step_id = step_id
                            st.session_state.selected_branch_id = branch_id
                            st.rerun()
                else:
                    st.caption("단계 없음")


# ─────────────────────────────────────────────
# 메인 패널 (중앙)
# ─────────────────────────────────────────────

def render_main_panel():
    """메인 중앙 패널"""
    session_id = st.session_state.get("current_session_id")
    dataset_id = st.session_state.get("current_dataset_id")

    if not session_id:
        st.info("📁 세션을 생성하거나 선택해주세요.")
        st.markdown("""
        **시작하는 방법:**
        1. 왼쪽 사이드바에서 **+ 새 세션 만들기** 버튼을 클릭하세요.
        2. 또는 기존 세션을 클릭하여 선택하세요.
        3. 세션을 선택하면 데이터셋을 업로드하거나 내장 데이터셋을 선택할 수 있습니다.
        """)
        return

    session_name = st.session_state.get("current_session_name", session_id)
    st.title(f"📊 {session_name}")

    if not dataset_id:
        st.warning("📂 데이터셋을 선택하거나 업로드해주세요.")
        st.markdown("""
        **데이터셋을 추가하는 방법:**
        - 왼쪽 사이드바에서 파일을 업로드하거나
        - 내장 데이터셋 드롭다운에서 선택하세요.

        **사용 가능한 내장 데이터셋:**
        - manufacturing_regression (제조 공정, 12,000행)
        - instrument_measurement (계측 장비, 8,000행)
        - general_tabular_regression (일반 회귀, 5,000행)
        - large_sampling_regression (대용량, 250,000행)
        """)
        return

    target_col = st.session_state.get("target_column")

    # ── 활성 컨텍스트 바 ──────────────────────────────────
    _render_context_bar(session_id)

    if not target_col:
        st.info("💡 왼쪽 사이드바에서 타겟 컬럼을 설정하면 모델링 기능을 사용할 수 있습니다. EDA·프로파일 분석은 타겟 없이도 가능합니다.")

    # 빠른 액션 버튼
    _render_quick_actions(session_id, dataset_id)

    st.divider()

    # 채팅 인터페이스 + 진행 상황
    col_chat, col_artifact = st.columns([2, 1])

    with col_chat:
        _render_chat_interface(session_id, dataset_id, target_col)

    with col_artifact:
        _render_artifact_panel(session_id)


def _render_context_bar(session_id: str):
    """활성 브랜치·데이터셋·타겟을 한눈에 보여주는 컨텍스트 바"""
    selected_branch_id = st.session_state.get("selected_branch_id")
    target_col = st.session_state.get("target_column")

    # 브랜치 정보 조회 (캐시)
    branch_info = None
    if selected_branch_id:
        cache_key = f"_branch_info_{selected_branch_id}"
        branch_info = st.session_state.get(cache_key)
        if branch_info is None:
            r = api_call("get", f"/sessions/{session_id}/branches")
            if r and r.get("success"):
                for b in r["data"]:
                    st.session_state[f"_branch_info_{b['id']}"] = b
                branch_info = st.session_state.get(cache_key)

    # 데이터셋 레이블 결정
    if branch_info:
        branch_config = branch_info.get("config") or {}
        branch_dataset_path = branch_config.get("dataset_path")
        if branch_dataset_path:
            fname = branch_dataset_path.split("/")[-1]
            dataset_label = f"📂 {fname} _(필터링된 데이터)_"
        else:
            ds_name = st.session_state.get("current_dataset_name", "원본 데이터셋")
            dataset_label = f"📂 {ds_name} _(원본)_"
        branch_label = branch_info.get("name", selected_branch_id[:8])
    else:
        ds_name = st.session_state.get("current_dataset_name", "미선택")
        dataset_label = f"📂 {ds_name}"
        branch_label = "기본 브랜치"

    target_label = f"🎯 {target_col}" if target_col else "🎯 타겟 미설정"

    # 컨텍스트 바 렌더링
    with st.container(border=True):
        cols = st.columns([3, 3, 2, 1])
        with cols[0]:
            st.markdown(f"**🌿 {branch_label}**")
        with cols[1]:
            st.markdown(dataset_label)
        with cols[2]:
            st.markdown(f"**{target_label}**")
        with cols[3]:
            if st.button("브랜치 전환", key="ctx_switch_branch", use_container_width=True):
                st.session_state["_show_branch_switcher"] = not st.session_state.get("_show_branch_switcher", False)
                st.rerun()

    # 브랜치 전환 패널 (토글)
    if st.session_state.get("_show_branch_switcher"):
        _render_branch_switcher_panel(session_id)


def _render_branch_switcher_panel(session_id: str):
    """인라인 브랜치 전환 패널"""
    r = api_call("get", f"/sessions/{session_id}/branches")
    if not r or not r.get("success") or not r["data"]:
        st.warning("브랜치가 없습니다.")
        return

    branches = r["data"]
    active_branch_id = st.session_state.get("selected_branch_id")

    with st.container(border=True):
        st.markdown("#### 브랜치 전환")
        for b in branches:
            bid = str(b["id"])
            bname = b.get("name", "브랜치")
            config = b.get("config") or {}
            ds_path = config.get("dataset_path")
            is_active = bid == active_branch_id

            if ds_path:
                ds_desc = f"필터링 데이터: `{ds_path.split('/')[-1]}`"
            else:
                ds_desc = f"원본 데이터셋: `{st.session_state.get('current_dataset_name', '-')}`"

            col_info, col_btn = st.columns([4, 1])
            with col_info:
                if is_active:
                    st.markdown(f"**▶ {bname}** ✅ 현재 활성")
                else:
                    st.markdown(f"🌿 **{bname}**")
                st.caption(ds_desc)
            with col_btn:
                if not is_active:
                    if st.button("전환", key=f"switch_{bid}", use_container_width=True, type="primary"):
                        st.session_state.selected_branch_id = bid
                        st.session_state.selected_step_id = None
                        # 브랜치별 branch_info 캐시 무효화
                        st.session_state.pop(f"_branch_info_{bid}", None)
                        st.session_state["_show_branch_switcher"] = False
                        st.rerun()
                else:
                    st.caption("활성")

        if st.button("닫기", key="ctx_switch_close"):
            st.session_state["_show_branch_switcher"] = False
            st.rerun()


def _render_target_selection(session_id: str, dataset_id: str):
    """타겟 컬럼 선택"""
    st.subheader("분석 목표 변수(Target) 선택")
    st.caption("회귀 분석할 목표 변수를 선택하거나 직접 입력하세요.")

    # 타겟 후보 조회
    with st.spinner("타겟 후보 분석 중..."):
        result = api_call(
            "get",
            f"/sessions/{session_id}/datasets/{dataset_id}/target-candidates",
        )

    candidates = []
    if result and result.get("success"):
        candidates = result["data"].get("candidates", [])

    tab1, tab2 = st.tabs(["추천 후보", "직접 입력"])

    with tab1:
        if candidates:
            st.markdown("**분석 결과 추천 타겟 후보:**")
            for cand in candidates[:8]:
                col_name = cand.get("column", cand.get("name", ""))
                score = cand.get("score", 0.0)
                reason = cand.get("reason", "")
                label = f"**{col_name}** (점수: {score:.3f})"
                if st.button(label, key=f"target_{col_name}", use_container_width=True):
                    st.session_state.target_column = col_name
                    st.session_state.target_dataset_id = dataset_id
                    st.success(f"타겟 선택: {col_name}")
                    st.rerun()
                if reason:
                    st.caption(f"  {reason}")
        else:
            st.caption("타겟 후보를 분석할 수 없습니다.")

    with tab2:
        custom_target = st.text_input(
            "타겟 컬럼명 직접 입력", placeholder="예: quality_score"
        )
        if st.button("타겟 설정", key="set_custom_target"):
            if custom_target.strip():
                st.session_state.target_column = custom_target.strip()
                st.session_state.target_dataset_id = dataset_id
                st.success(f"타겟 설정: {custom_target.strip()}")
                st.rerun()
            else:
                st.error("타겟 컬럼명을 입력하세요.")


def _render_quick_actions(session_id: str, dataset_id: str):
    """빠른 액션 버튼"""
    cols = st.columns(5)
    actions = [
        ("프로파일 분석", "데이터셋 프로파일 분석"),
        ("Subset 발견", "dense subset 5개 발견"),
        ("기준 모델링", "LightGBM baseline 모델링 실행"),
        ("SHAP 분석", "champion 모델 SHAP 분석"),
        ("최적화 설정", None),  # 특별 처리
    ]

    for i, (label, message) in enumerate(actions):
        with cols[i]:
            if st.button(label, use_container_width=True, key=f"quick_{label}"):
                if message is None:
                    st.session_state.show_optimization_form = True
                else:
                    _submit_analysis(session_id, message)

    if st.session_state.get("show_optimization_form"):
        _render_optimization_form(session_id)


def _render_optimization_form(session_id: str):
    """역최적화 위저드 (3단계)"""
    st.subheader("🔍 역최적화 설정")
    st.caption("학습된 모델이 예측하는 값을 최대화/최소화하는 최적 피처 조합을 탐색합니다.")

    # 닫기 버튼
    if st.button("✖ 닫기", key="opt_wizard_close"):
        _clear_opt_state()
        st.rerun()

    # 브랜치 선택
    branches_result = api_call("get", f"/sessions/{session_id}/branches")
    branches = branches_result.get("data", []) if branches_result and branches_result.get("success") else []
    if not branches:
        st.warning("사용 가능한 브랜치가 없습니다. 먼저 모델링을 실행하세요.")
        return

    branch_options = {
        f"{b.get('name', '브랜치')}{'  ✅' if b.get('is_active') else ''}": str(b["id"])
        for b in branches
    }
    current_branch_id = st.session_state.get("selected_branch_id")
    default_label = next(
        (lb for lb, bid in branch_options.items() if bid == current_branch_id),
        list(branch_options.keys())[0],
    )
    selected_label = st.selectbox("모델 브랜치 선택", list(branch_options.keys()),
                                  index=list(branch_options.keys()).index(default_label),
                                  key="opt_branch_select")
    branch_id = branch_options[selected_label]

    st.divider()

    # ── 단계 1: Null Importance 분석 ──────────────────────
    st.markdown("### 단계 1 · 피처 유의성 분석 (Null Importance)")

    ni_job_id = st.session_state.get("ni_job_id")
    ni_result = st.session_state.get("ni_result")

    if not ni_result:
        col_n, col_run = st.columns([2, 1])
        with col_n:
            n_perm = st.number_input("순열 횟수 (많을수록 정확, 느림)", 10, 100, 30, key="opt_n_perm")
        with col_run:
            st.write("")
            if st.button("▶ 분석 시작", key="opt_run_ni", use_container_width=True):
                r = api_call("post", "/optimization/null-importance", json={
                    "session_id": session_id,
                    "branch_id": branch_id,
                    "n_permutations": int(n_perm),
                })
                if r and r.get("success"):
                    st.session_state.ni_job_id = r["data"]["job_id"]
                    st.rerun()
                else:
                    err = (r.get("error", {}) if r else {})
                    st.error(f"분석 실패: {err.get('message', '오류')}")

        if ni_job_id:
            job_r = api_call("get", f"/jobs/{ni_job_id}")
            if job_r and job_r.get("success"):
                job = job_r["data"]
                st.progress(job.get("progress", 0) / 100, text=job.get("progress_message", ""))
                if job["status"] == "completed":
                    st.session_state.ni_result = job.get("result", {})
                    st.rerun()
                elif job["status"] == "failed":
                    st.error(f"분석 실패: {job.get('error_message', '')}")
                    st.session_state.ni_job_id = None
                else:
                    st.button("🔄 새로고침", key="opt_refresh_ni")
        return  # 단계 1 완료 전 이후 단계 숨김

    # ── 단계 1 결과 표시 ───────────────────────────────────
    actual_imp = ni_result.get("actual_importance", {})
    null_imp = ni_result.get("null_importance", {})
    recommended = ni_result.get("recommended_features", [])
    recommended_n = ni_result.get("recommended_n", min(8, len(recommended)))
    feature_ranges = ni_result.get("feature_ranges", {})
    all_features = ni_result.get("feature_names", list(actual_imp.keys()))

    # 중요도 테이블 표시
    rows = []
    for feat in list(actual_imp.keys())[:20]:
        ni = null_imp.get(feat, {})
        actual = actual_imp[feat]
        p90 = ni.get("p90", 0)
        significant = "✅" if actual > p90 else "—"
        rows.append({
            "피처": feat,
            "실제 중요도": f"{actual:.4f}",
            "Null p90": f"{p90:.4f}",
            "유의": significant,
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=220)
    st.caption(f"추천 피처 수: **{recommended_n}개** (Null p90 초과 기준)")

    col_reset, _ = st.columns([1, 3])
    with col_reset:
        if st.button("↩ 재분석", key="opt_reset_ni"):
            st.session_state.ni_result = None
            st.session_state.ni_job_id = None
            st.rerun()

    st.divider()

    # ── 단계 2: 피처 선택 및 설정 ─────────────────────────
    st.markdown("### 단계 2 · 최적화 피처 선택 및 제약 설정")

    n_feat = st.slider(
        "최적화 피처 수 (상위 N개 선택)",
        min_value=2, max_value=min(15, len(all_features)),
        value=recommended_n, key="opt_n_feat",
    )
    selected_features = recommended[:n_feat] if len(recommended) >= n_feat else all_features[:n_feat]

    st.caption(f"선택된 피처: {', '.join(selected_features)}")

    # 피처별 설정 (고정/범위 확장)
    st.markdown("**피처별 설정** (기본값: 데이터 범위 ±12.5% 탐색)")
    fixed_values = {}
    custom_ranges = {}

    for feat in selected_features:
        rng = feature_ranges.get(feat, [None, None])
        lo, hi = rng[0], rng[1]
        with st.expander(f"⚙️ {feat}  [범위: {lo:.3f} ~ {hi:.3f}]" if lo is not None else f"⚙️ {feat}", expanded=False):
            fix = st.checkbox("이 피처를 고정값으로 설정", key=f"opt_fix_{feat}")
            if fix:
                fixed_val = st.number_input(
                    "고정값", value=float((lo + hi) / 2) if lo is not None else 0.0,
                    key=f"opt_fixval_{feat}"
                )
                fixed_values[feat] = fixed_val
            else:
                if lo is not None:
                    expand = st.slider(
                        "탐색 공간 확장 비율 (%)", 0, 50, 12,
                        key=f"opt_expand_{feat}"
                    )
                    custom_ranges[feat] = [lo, hi]  # expand는 서버에서 처리

    expand_ratio = st.slider(
        "전체 탐색 공간 확장 비율 (%)", 0, 50, 12,
        help="학습 데이터의 범위를 기준으로 해당 비율만큼 탐색 공간 확장"
    ) / 100.0

    st.divider()

    # ── 단계 3: 방향 및 실행 ──────────────────────────────
    st.markdown("### 단계 3 · 최적화 방향 및 실행")

    target_col = st.session_state.get("target_column", "target")
    direction = st.radio(
        f"**{target_col}** 예측값 방향",
        ["maximize", "minimize"],
        format_func=lambda x: "▲ 최대화 (maximize)" if x == "maximize" else "▼ 최소화 (minimize)",
        horizontal=True, key="opt_direction",
    )
    n_calls = st.number_input("탐색 횟수 (많을수록 정확, 느림)", 100, 2000, 300, step=100, key="opt_n_calls")

    inv_job_id = st.session_state.get("inv_job_id")
    inv_result = st.session_state.get("inv_result")

    if not inv_result:
        if st.button("🚀 역최적화 실행", key="opt_run_inv", type="primary", use_container_width=True):
            opt_features = [f for f in selected_features if f not in fixed_values]
            if not opt_features:
                st.error("고정되지 않은 피처가 최소 1개 이상 필요합니다.")
            else:
                r = api_call("post", "/optimization/inverse-run", json={
                    "session_id": session_id,
                    "branch_id": branch_id,
                    "selected_features": selected_features,
                    "fixed_values": fixed_values,
                    "feature_ranges": custom_ranges or feature_ranges,
                    "expand_ratio": expand_ratio,
                    "direction": direction,
                    "n_calls": int(n_calls),
                    "target_column": target_col,
                })
                if r and r.get("success"):
                    st.session_state.inv_job_id = r["data"]["job_id"]
                    st.rerun()
                else:
                    err = (r.get("error", {}) if r else {})
                    st.error(f"실행 실패: {err.get('message', '오류')}")

        if inv_job_id:
            job_r = api_call("get", f"/jobs/{inv_job_id}")
            if job_r and job_r.get("success"):
                job = job_r["data"]
                st.progress(job.get("progress", 0) / 100, text=job.get("progress_message", ""))
                if job["status"] == "completed":
                    st.session_state.inv_result = job.get("result", {})
                    st.rerun()
                elif job["status"] == "failed":
                    st.error(f"최적화 실패: {job.get('error_message', '')}")
                    st.session_state.inv_job_id = None
                else:
                    st.button("🔄 새로고침", key="opt_refresh_inv")
    else:
        # ── 결과 표시 ──────────────────────────────────────
        st.success("✅ 역최적화 완료!")
        col_pred, col_base = st.columns(2)
        with col_pred:
            st.metric("최적 예측값", f"{inv_result.get('optimal_prediction', 0):.4f}")
        with col_base:
            base = inv_result.get("baseline_prediction")
            impr = inv_result.get("improvement")
            st.metric(
                "베이스라인 (중앙값)",
                f"{base:.4f}" if base is not None else "-",
                delta=f"{impr:+.4f}" if impr is not None else None,
            )

        st.markdown("**최적 피처 값**")
        opt_feats = inv_result.get("optimal_features", {})
        rows = [{"피처": k, "최적값": f"{v:.4f}" if isinstance(v, float) else str(v)}
                for k, v in opt_feats.items()]
        if inv_result.get("fixed_features"):
            for k, v in inv_result["fixed_features"].items():
                rows.append({"피처": f"{k} (고정)", "최적값": f"{v:.4f}" if isinstance(v, float) else str(v)})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        conv = "✅ 수렴" if inv_result.get("convergence") else "⚠️ 미수렴 (결과 참고용)"
        st.caption(f"탐색 횟수: {inv_result.get('n_evaluations', '-')}회  |  수렴: {conv}")

        if st.button("↩ 다시 설정", key="opt_reset_inv"):
            st.session_state.inv_result = None
            st.session_state.inv_job_id = None
            st.rerun()


def _clear_opt_state():
    for key in ["ni_job_id", "ni_result", "inv_job_id", "inv_result", "show_optimization_form"]:
        st.session_state.pop(key, None)


def _submit_analysis(session_id: str, message: str):
    """분석 요청 제출"""
    target_col = st.session_state.get("target_column")
    branch_id = st.session_state.get("selected_branch_id")

    if not branch_id:
        # 기본 브랜치 가져오기
        branches_result = api_call("get", f"/sessions/{session_id}/branches")
        if branches_result and branches_result.get("success"):
            branches = branches_result["data"]
            if branches:
                branch_id = str(branches[0]["id"])

    if not branch_id:
        st.error("브랜치를 찾을 수 없습니다. 세션에 브랜치가 있는지 확인하세요.")
        return

    selected_artifact_id = st.session_state.get("selected_artifact_id")
    context: dict = {"mode": st.session_state.get("analysis_mode", "auto")}
    if selected_artifact_id:
        context["selected_artifact_id"] = selected_artifact_id

    with st.spinner("분석 요청 제출 중..."):
        result = api_call(
            "post",
            "/analysis/analyze",
            json={
                "session_id": session_id,
                "branch_id": branch_id,
                "message": message,
                "target_column": target_col,
                "context": context,
            },
        )

    if result and result.get("success"):
        job_id = result["data"]["job_id"]
        st.session_state.active_job_id = job_id
        st.session_state.last_job_error = None
        # 세션별 채팅 히스토리에 추가
        if "chat_histories" not in st.session_state:
            st.session_state.chat_histories = {}
        if session_id not in st.session_state.chat_histories:
            st.session_state.chat_histories[session_id] = []
        st.session_state.chat_histories[session_id].append(
            {"role": "user", "content": message, "timestamp": datetime.now().isoformat()}
        )
        st.success(f"분석 요청 접수 (작업 ID: {job_id[:8]}...)")
        st.rerun()
    else:
        if result:
            err = result.get("error", {})
            st.error(f"분석 실패: {err.get('message', '알 수 없는 오류')}")
        else:
            st.error("분석 요청 실패")


def _render_chat_interface(session_id: str, dataset_id: str, target_col: str):
    """채팅 인터페이스"""
    st.subheader("분석 채팅")

    histories = st.session_state.get("chat_histories", {})
    history = histories.get(session_id, [])
    selected_step_id = st.session_state.get("selected_step_id")

    # 단계 선택 시 해당 단계의 대화만 필터링
    display_history = history
    if selected_step_id:
        filtered = _filter_chat_for_step(history, selected_step_id)
        if filtered:
            display_history = filtered
            col_label, col_all = st.columns([3, 1])
            with col_label:
                st.caption("선택된 단계의 대화")
            with col_all:
                if st.button("전체 보기", key="show_all_chat"):
                    st.session_state.selected_step_id = None
                    st.rerun()

    for msg in display_history[-20:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            with st.chat_message("user"):
                st.write(content)
        else:
            with st.chat_message("assistant"):
                st.markdown(content)
                # 아티팩트 인라인 표시:
                # - 단계 선택 중: 해당 단계 메시지에만
                # - 단계 미선택(전체 보기): 모든 어시스턴트 메시지에 표시
                show_artifacts = (
                    not selected_step_id  # 전체 보기
                    or msg.get("step_id") == selected_step_id  # 선택된 단계
                )
                if show_artifacts:
                    artifact_ids = msg.get("artifact_ids", [])
                    if artifact_ids:
                        _render_inline_artifacts(session_id, artifact_ids)

    # 진행 중인 작업 표시 (채팅 하단에 append)
    _render_job_progress(session_id)

    # 입력 영역
    st.divider()

    # 선택된 아티팩트 표시
    selected_artifact_id = st.session_state.get("selected_artifact_id")
    if selected_artifact_id:
        col_badge, col_clear = st.columns([5, 1])
        with col_badge:
            st.info(f"🎯 선택된 아티팩트: `{selected_artifact_id[:8]}...` — 이 아티팩트에 대해 질문하세요")
        with col_clear:
            if st.button("✖ 해제", key="clear_artifact_sel"):
                st.session_state.selected_artifact_id = None
                st.rerun()

    col_mode, col_input = st.columns([1, 3])
    with col_mode:
        mode = st.selectbox(
            "분석 모드",
            ["auto", "eda", "create_dataframe", "subset_discovery", "modeling", "optimization"],
            key="analysis_mode",
            label_visibility="collapsed",
        )

    with col_input:
        user_input = st.chat_input("분석 요청을 입력하세요...", key="chat_input")

    if user_input:
        _submit_analysis(session_id, user_input)


def _render_job_progress(session_id: str):
    """활성 작업 진행 상황 폴링 및 표시"""
    job_id = st.session_state.get("active_job_id")

    # 이전 작업의 에러/취소 메시지 표시 (rerun 후에도 유지)
    last_error = st.session_state.get("last_job_error")
    if last_error and not job_id:
        if last_error.startswith("⚠️"):
            st.warning(last_error)
        else:
            st.error(last_error)

    if not job_id:
        # 활성 작업 확인
        result = api_call("get", f"/jobs/session/{session_id}/active")
        if result and result.get("success"):
            data = result["data"]
            if data.get("job_id"):
                st.session_state.active_job_id = data["job_id"]
                job_id = data["job_id"]

    if not job_id:
        return

    result = api_call("get", f"/jobs/{job_id}")
    if not result or not result.get("success"):
        return

    job = result["data"]
    status = job.get("status", "")
    progress = job.get("progress", 0) or 0
    stage = job.get("stage", job.get("job_type", "분석"))
    message = job.get("message", job.get("progress_message", ""))
    recent_logs = job.get("recent_logs", [])

    with st.container():
        st.markdown("**🔄 작업 진행 중...**")
        st.progress(int(progress) / 100)
        st.caption(f"단계: {stage} - {message}")

        if recent_logs:
            for log in recent_logs[-3:]:
                st.caption(f"  • {log}")

        col_cancel, col_refresh = st.columns([1, 3])
        with col_cancel:
            if st.button("⏹ 취소", key="cancel_job_btn"):
                cancel_result = api_call("post", f"/jobs/{job_id}/cancel")
                if cancel_result and cancel_result.get("success"):
                    st.success("취소 요청됨")
                    st.session_state.active_job_id = None
                    st.rerun()

    if status in ("completed", "failed", "cancelled"):
        if status == "completed":
            st.success("✅ 분석 완료!")
            # 세션별 채팅 히스토리에 추가
            if "chat_histories" not in st.session_state:
                st.session_state.chat_histories = {}
            if session_id not in st.session_state.chat_histories:
                st.session_state.chat_histories[session_id] = []
            # result 필드에서 message, step_id, artifact_ids 추출
            result_data = job.get("result") or {}
            summary = (
                result_data.get("message")
                or job.get("summary")
                or job.get("result_summary")
                or "분석이 완료되었습니다."
            )
            step_id = result_data.get("step_id")
            artifact_ids = result_data.get("artifact_ids", [])
            # 브랜치 ID: 현재 선택된 브랜치 사용
            branch_id = st.session_state.get("selected_branch_id")
            if not branch_id:
                branches_result = api_call("get", f"/sessions/{session_id}/branches")
                if branches_result and branches_result.get("success"):
                    branches = branches_result["data"]
                    if branches:
                        branch_id = str(branches[0]["id"])
            st.session_state.chat_histories[session_id].append({
                "role": "assistant",
                "content": str(summary),
                "step_id": step_id,
                "branch_id": branch_id,
                "artifact_ids": artifact_ids,
            })
            # 완료된 스텝 자동 선택 (아티팩트 패널)
            if step_id and branch_id:
                st.session_state.selected_step_id = step_id
                st.session_state.selected_branch_id = branch_id
        elif status == "failed":
            err_msg = job.get("error_message", "알 수 없는 오류가 발생했습니다.")
            st.session_state.last_job_error = f"❌ 분석 실패: {err_msg}"
        elif status == "cancelled":
            st.session_state.last_job_error = "⚠️ 작업이 취소되었습니다."

        st.session_state.active_job_id = None
        st.rerun()
    else:
        # 아직 실행 중 - 5초 후 재실행
        time.sleep(POLL_INTERVAL)
        st.rerun()


# ─────────────────────────────────────────────
# 오른쪽 패널 - 아티팩트 미리보기
# ─────────────────────────────────────────────

def _render_artifact_panel(session_id: str):
    """아티팩트 미리보기 패널"""
    st.subheader("아티팩트")

    step_id = st.session_state.get("selected_step_id")
    branch_id = st.session_state.get("selected_branch_id")

    if not step_id or not branch_id:
        st.caption("왼쪽 단계 트리에서 분석 단계를 선택하면 결과가 표시됩니다.")
        return

    # 스텝 상세 조회
    step_result = api_call(
        "get",
        f"/sessions/{session_id}/branches/{branch_id}/steps/{step_id}",
    )

    if not step_result or not step_result.get("success"):
        st.caption("단계 정보를 불러올 수 없습니다.")
        return

    step = step_result["data"]
    st.markdown(f"**{step.get('title', '분석 단계')}**")
    st.caption(
        f"유형: {step.get('step_type', '-')} | 상태: {step.get('status', '-')}"
    )

    summary = step.get("summary") or step.get("output_data")
    if summary:
        if isinstance(summary, str):
            st.markdown(summary[:500])
        elif isinstance(summary, dict):
            st.json(summary)

    # 아티팩트 목록
    artifact_ids = step.get("artifact_ids", [])
    if not artifact_ids:
        st.caption("아티팩트 없음")
        return

    st.markdown(f"**아티팩트 ({len(artifact_ids)}개)**")
    for art_id in artifact_ids[:10]:
        _render_single_artifact(session_id, str(art_id))


def _compact_json(data, max_items: int = 8) -> str:
    """JSON 데이터를 읽기 쉬운 한 줄 요약으로 축약"""
    if isinstance(data, dict):
        items = list(data.items())[:max_items]
        parts = []
        for k, v in items:
            if isinstance(v, float):
                parts.append(f"{k}: {v:.4f}")
            elif isinstance(v, (list, dict)):
                parts.append(f"{k}: [{len(v)} items]" if isinstance(v, list) else f"{k}: {{...}}")
            else:
                parts.append(f"{k}: {str(v)[:30]}")
        suffix = f" ... (+{len(data)-max_items})" if len(data) > max_items else ""
        return "  |  ".join(parts) + suffix
    elif isinstance(data, list):
        return f"[{len(data)} items]: " + ", ".join(str(x)[:20] for x in data[:3]) + ("..." if len(data) > 3 else "")
    return str(data)[:100]


def _render_compact_data(label: str, preview: dict, icon: str = "📊", max_rows: int = 5):
    """JSON/dict 데이터를 테이블 또는 축약 형태로 렌더링"""
    # 숫자형 key-value 지표는 테이블로 표시
    numeric_kv = {k: v for k, v in preview.items()
                  if isinstance(v, (int, float)) and not isinstance(v, bool)}
    list_kv = {k: v for k, v in preview.items() if isinstance(v, list)}
    other_kv = {k: v for k, v in preview.items()
                if not isinstance(v, (int, float, list, dict, bool)) or isinstance(v, bool)}

    st.caption(f"{icon} {label}")

    # 숫자 지표: 콤팩트 테이블
    if numeric_kv:
        metric_df = pd.DataFrame([
            {"항목": k, "값": f"{v:.4f}" if isinstance(v, float) else str(v)}
            for k, v in list(numeric_kv.items())[:12]
        ])
        st.dataframe(metric_df, use_container_width=True, height=min(35 * len(metric_df) + 38, 200), hide_index=True)

    # 리스트 항목: 축약해서 expander에
    if list_kv:
        for k, v in list(list_kv.items())[:3]:
            if v and isinstance(v[0], dict):
                with st.expander(f"{k} ({len(v)}개)", expanded=False):
                    sub_df = pd.DataFrame(v[:max_rows])
                    st.dataframe(sub_df, use_container_width=True, height=min(35 * len(sub_df) + 38, 180), hide_index=True)
            else:
                st.caption(f"{k}: {', '.join(str(x)[:15] for x in v[:5])}{'...' if len(v)>5 else ''}")

    # 기타 문자열 항목
    if other_kv:
        for k, v in list(other_kv.items())[:5]:
            st.caption(f"**{k}**: {str(v)[:80]}")


def _filter_chat_for_step(history: list, step_id: str) -> list:
    """선택된 step_id에 해당하는 user+assistant 메시지 쌍 반환"""
    for i, msg in enumerate(history):
        if msg.get("role") == "assistant" and msg.get("step_id") == step_id:
            if i > 0 and history[i - 1].get("role") == "user":
                return [history[i - 1], msg]
            return [msg]
    return []


def _render_inline_artifacts(session_id: str, artifact_ids: list):
    """채팅 메시지 안에서 아티팩트를 인라인으로 표시"""
    code_artifacts = []

    for art_id in artifact_ids[:10]:
        preview_result = api_call(
            "get",
            f"/sessions/{session_id}/artifacts/{str(art_id)}/preview",
        )
        if not preview_result or not preview_result.get("success"):
            continue
        artifact = preview_result["data"]
        art_name = artifact.get("name", "")
        art_type = artifact.get("artifact_type", "")
        preview = artifact.get("preview_json")

        # 코드 아티팩트는 별도 수집 후 마지막에 expander로 표시
        if art_type == "code":
            code_artifacts.append((art_name, preview))
            continue

        if art_type == "plot" and isinstance(preview, dict):
            data_url = preview.get("data_url")
            if data_url:
                st.caption(f"📈 {art_name}")
                st.image(data_url, use_container_width=True)

        elif art_type in ("dataframe", "table", "leaderboard", "feature_importance"):
            if preview and isinstance(preview, dict):
                columns = preview.get("columns", [])
                rows = preview.get("rows", []) or preview.get("data", [])
                if columns and rows:
                    st.caption(f"📋 {art_name}")
                    try:
                        df_inline = pd.DataFrame(rows, columns=columns)
                        row_h = min(38 * (len(df_inline) + 1) + 10, 800)
                        st.dataframe(df_inline, use_container_width=True, height=row_h, hide_index=True)
                    except Exception:
                        _render_compact_data(art_name, preview, "📋")

        elif art_type in ("metric", "report", "shap_summary") and preview and isinstance(preview, dict):
            _render_compact_data(art_name, preview, "📊")

        elif art_type == "text" and preview:
            st.caption(f"📝 {art_name}")
            st.markdown(str(preview))

    # 코드 아티팩트: 항상 마지막에 collapsible expander로 표시
    for art_name, preview in code_artifacts:
        used_fallback = isinstance(preview, dict) and preview.get("used_fallback", False)
        error_msg = isinstance(preview, dict) and preview.get("error")
        code_text = preview.get("code", "") if isinstance(preview, dict) else ""

        if used_fallback:
            st.warning(f"⚠️ 코드 실행 실패 — 기본 분석으로 대체되었습니다.\n\n**오류:** `{error_msg}`" if error_msg else "⚠️ 코드 실행 실패 — 기본 분석으로 대체되었습니다.")

        label = f"{'⚠️' if used_fallback else '✅'} code — {art_name}"
        with st.expander(label, expanded=False):
            if code_text:
                st.code(code_text, language="python")
            else:
                st.caption("코드 없음")


def _render_single_artifact(session_id: str, artifact_id: str):
    """단일 아티팩트 렌더링"""
    preview_result = api_call(
        "get",
        f"/sessions/{session_id}/artifacts/{artifact_id}/preview",
    )
    if not preview_result or not preview_result.get("success"):
        return

    artifact = preview_result["data"]
    art_name = artifact.get("name", "아티팩트")
    art_type = artifact.get("artifact_type", "")
    art_file_path = artifact.get("file_path")
    preview = artifact.get("preview_json")

    is_selected = st.session_state.get("selected_artifact_id") == artifact_id
    expander_label = f"{'✅ ' if is_selected else '📄 '}{art_name}"

    with st.expander(expander_label, expanded=True):
        if art_type in ("dataframe", "table", "leaderboard", "feature_importance"):
            if preview and isinstance(preview, dict):
                columns = preview.get("columns", [])
                rows = preview.get("rows", []) or preview.get("data", [])
                if columns and rows:
                    try:
                        df = pd.DataFrame(rows, columns=columns)
                        row_h = min(38 * (len(df) + 1) + 10, 800)
                        st.dataframe(df, use_container_width=True, height=row_h, hide_index=True)
                    except Exception:
                        _render_compact_data(art_name, preview)
                else:
                    _render_compact_data(art_name, preview)
            else:
                st.caption("미리보기 없음")

        elif art_type == "plot":
            if preview and isinstance(preview, dict):
                data_url = preview.get("data_url")
                if data_url:
                    st.image(data_url, use_container_width=True)
                else:
                    _render_compact_data(art_name, preview, "📈")
            elif isinstance(preview, str):
                st.image(preview, use_container_width=True)
            else:
                st.caption("이미지 없음")

        elif art_type in ("metric", "report"):
            if preview and isinstance(preview, dict):
                _render_compact_data(art_name, preview)
            elif preview:
                st.caption(str(preview)[:300])

        elif art_type == "text":
            if preview:
                st.markdown(str(preview)[:500])

        elif art_type == "code":
            if isinstance(preview, dict):
                used_fallback = preview.get("used_fallback", False)
                error_msg = preview.get("error")
                code_text = preview.get("code", "")
                if used_fallback:
                    st.warning(f"⚠️ 실행 실패 — 기본 분석으로 대체\n\n**오류:** `{error_msg}`" if error_msg else "⚠️ 실행 실패 — 기본 분석으로 대체")
                if code_text:
                    st.code(code_text, language="python")
            elif preview:
                st.code(str(preview), language="python")

        elif art_type == "model":
            if preview and isinstance(preview, dict):
                _render_compact_data(art_name, preview, "🤖")
            st.caption("모델 파일 (다운로드 가능)")

        elif art_type == "shap_summary":
            if preview and isinstance(preview, dict):
                features = preview.get("feature_rankings", [])
                if features:
                    rows_data = [
                        {"순위": i, "피처": f.get("feature",""), "중요도": f"{f.get('importance',0):.4f}"}
                        for i, f in enumerate(features[:10], 1)
                    ]
                    st.dataframe(pd.DataFrame(rows_data), use_container_width=True, height=180, hide_index=True)
                else:
                    _render_compact_data(art_name, preview)
        else:
            if preview and isinstance(preview, dict):
                _render_compact_data(art_name, preview)
            elif preview:
                st.caption(str(preview)[:200])

        # 액션 버튼 영역
        btn_cols = st.columns([2, 2, 1])

        # 아티팩트 선택/해제
        with btn_cols[0]:
            if is_selected:
                if st.button("✅ 선택 해제", key=f"desel_{artifact_id}", use_container_width=True):
                    st.session_state.selected_artifact_id = None
                    st.rerun()
            else:
                if st.button("🎯 이 아티팩트로 질문", key=f"sel_{artifact_id}", use_container_width=True):
                    st.session_state.selected_artifact_id = artifact_id
                    st.rerun()

        # DataFrame 아티팩트 → 새 브랜치 생성
        if art_type in ("dataframe", "table") and art_file_path:
            with btn_cols[1]:
                if st.button("🌿 이 데이터로 새 브랜치", key=f"branch_{artifact_id}", use_container_width=True):
                    branch_name = f"브랜치_{art_name[:20]}"
                    result = api_call(
                        "post",
                        f"/sessions/{session_id}/branches",
                        json={
                            "name": branch_name,
                            "description": f"아티팩트 '{art_name}'의 필터링된 데이터",
                            "config": {"dataset_path": art_file_path},
                        },
                    )
                    if result and result.get("success"):
                        new_branch = result["data"]
                        new_bid = str(new_branch["id"])
                        st.session_state.selected_branch_id = new_bid
                        # 새 브랜치 캐시 저장
                        st.session_state[f"_branch_info_{new_bid}"] = new_branch
                        st.success(f"새 브랜치 생성: {new_branch['name']}")
                        st.rerun()
                    else:
                        err = result.get("error", {}) if result else {}
                        st.error(f"브랜치 생성 실패: {err.get('message', '오류')}")

        # 다운로드
        with btn_cols[2]:
            download_url = f"{API_BASE}/sessions/{session_id}/artifacts/{artifact_id}/download"
            st.markdown(
                f'<a href="{download_url}" target="_blank">⬇️</a>',
                unsafe_allow_html=True,
            )


# ─────────────────────────────────────────────
# 메인 앱 진입점
# ─────────────────────────────────────────────

def main():
    if not check_auth():
        login_page()
        return

    # 페이지 첫 로드 시 현재 세션 자동 복원
    session_id = st.session_state.get("current_session_id")
    if session_id and not st.session_state.get(f"_restored_{session_id}"):
        _restore_session(session_id)
        st.session_state[f"_restored_{session_id}"] = True

    render_sidebar()
    render_main_panel()


main()
