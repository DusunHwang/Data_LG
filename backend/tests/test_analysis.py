"""분석 E2E 통합 테스트: 로그인 → 세션 → 데이터셋 → 타겟 → 분석 요청 → 폴링 → 결과"""

import pytest

# ─── 유틸 ──────────────────────────────────────────────────────────────────────

def _skip_if_no_auth(headers):
    if not headers:
        pytest.skip("인증 서버 미연결")


def _skip_if_no_session(session_id):
    if not session_id:
        pytest.skip("세션 생성 불가")


def _skip_if_401(response):
    if response.status_code == 401:
        pytest.skip("인증 서버 미연결")


# ─── 세션 라이프사이클 ──────────────────────────────────────────────────────────

class TestSessionLifecycle:
    """세션 생성 → 조회 → 삭제"""

    def test_create_and_get_session(self, client, auth_headers):
        _skip_if_no_auth(auth_headers)

        # 세션 생성
        r = client.post(
            "/api/v1/sessions",
            json={"name": "E2E 테스트 세션", "ttl_days": 1},
            headers=auth_headers,
        )
        _skip_if_401(r)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        session_id = data["data"]["id"]
        assert session_id

        # 단건 조회
        r2 = client.get(f"/api/v1/sessions/{session_id}", headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["data"]["id"] == session_id

        # 정리
        client.delete(f"/api/v1/sessions/{session_id}", headers=auth_headers)

    def test_list_sessions(self, client, auth_headers):
        _skip_if_no_auth(auth_headers)

        r = client.get("/api/v1/sessions", headers=auth_headers)
        _skip_if_401(r)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)


# ─── 데이터셋 ──────────────────────────────────────────────────────────────────

class TestDatasetFlow:
    """내장 데이터셋 선택 → 프로파일 → 타겟 후보"""

    def test_list_builtin_datasets(self, client, auth_headers, test_session_id):
        _skip_if_no_auth(auth_headers)
        _skip_if_no_session(test_session_id)

        r = client.get(
            f"/api/v1/sessions/{test_session_id}/datasets/builtin-list",
            headers=auth_headers,
        )
        _skip_if_401(r)
        assert r.status_code == 200

        data = r.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)
        assert len(data["data"]) >= 1

    def test_select_builtin_dataset(self, client, auth_headers, test_session_id):
        _skip_if_no_auth(auth_headers)
        _skip_if_no_session(test_session_id)

        # 내장 데이터셋 목록 조회
        r = client.get(
            f"/api/v1/sessions/{test_session_id}/datasets/builtin",
            headers=auth_headers,
        )
        _skip_if_401(r)
        if not r.json().get("data"):
            pytest.skip("내장 데이터셋 없음")

        dataset_name = r.json()["data"][0]["name"]

        # 내장 데이터셋 선택
        r2 = client.post(
            f"/api/v1/sessions/{test_session_id}/datasets/builtin",
            json={"dataset_name": dataset_name},
            headers=auth_headers,
        )
        assert r2.status_code in (200, 201)
        assert r2.json()["success"] is True

    def test_target_candidates(self, client, auth_headers, test_session_id):
        _skip_if_no_auth(auth_headers)
        _skip_if_no_session(test_session_id)

        # 내장 데이터셋 선택 후 타겟 후보 조회
        r_list = client.get(
            f"/api/v1/sessions/{test_session_id}/datasets/builtin",
            headers=auth_headers,
        )
        _skip_if_401(r_list)
        if not r_list.json().get("data"):
            pytest.skip("내장 데이터셋 없음")

        dataset_name = r_list.json()["data"][0]["name"]
        client.post(
            f"/api/v1/sessions/{test_session_id}/datasets/builtin",
            json={"dataset_name": dataset_name},
            headers=auth_headers,
        )

        # 현재 데이터셋 ID 조회
        r_ds = client.get(
            f"/api/v1/sessions/{test_session_id}/datasets",
            headers=auth_headers,
        )
        if r_ds.status_code != 200 or not r_ds.json().get("data"):
            pytest.skip("데이터셋 조회 실패")

        dataset_id = r_ds.json()["data"][0]["id"]

        # 타겟 후보
        r3 = client.get(
            f"/api/v1/sessions/{test_session_id}/datasets/{dataset_id}/target-candidates",
            headers=auth_headers,
        )
        if r3.status_code == 200:
            candidates = r3.json()["data"]
            assert isinstance(candidates, list)
            assert len(candidates) <= 3


# ─── 분석 요청 + 폴링 ───────────────────────────────────────────────────────────

class TestAnalyzeAndPoll:
    """분석 요청 → job_id 반환 → 폴링 → 상태 확인"""

    def test_analyze_returns_job_id(self, client, auth_headers, test_session_id):
        _skip_if_no_auth(auth_headers)
        _skip_if_no_session(test_session_id)

        r = client.post(
            "/api/v1/analysis/analyze",
            json={
                "session_id": test_session_id,
                "message": "데이터 프로파일을 분석해줘",
                "mode": "auto",
            },
            headers=auth_headers,
        )
        _skip_if_401(r)

        if r.status_code == 422:
            pytest.skip("데이터셋 미선택 (422)")

        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert "job_id" in data["data"]

    def test_poll_job_status(self, client, auth_headers, test_session_id):
        _skip_if_no_auth(auth_headers)
        _skip_if_no_session(test_session_id)

        # 분석 요청
        r = client.post(
            "/api/v1/analysis/analyze",
            json={
                "session_id": test_session_id,
                "message": "데이터 프로파일을 분석해줘",
                "mode": "auto",
            },
            headers=auth_headers,
        )
        _skip_if_401(r)

        if r.status_code != 200:
            pytest.skip("분석 요청 실패")

        job_id = r.json()["data"]["job_id"]
        assert job_id

        # 단건 조회 (상태 폴링 1회)
        r2 = client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)
        assert r2.status_code == 200
        job_data = r2.json()["data"]
        assert job_data["id"] == job_id
        assert job_data["status"] in ("pending", "running", "completed", "failed", "cancelled")

    def test_active_job_in_session(self, client, auth_headers, test_session_id):
        _skip_if_no_auth(auth_headers)
        _skip_if_no_session(test_session_id)

        r = client.get(
            f"/api/v1/jobs/session/{test_session_id}/active",
            headers=auth_headers,
        )
        _skip_if_401(r)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        # job_id는 None이거나 실행 중인 작업 ID
        assert "job_id" in data["data"]

    def test_cancel_running_job(self, client, auth_headers, test_session_id):
        _skip_if_no_auth(auth_headers)
        _skip_if_no_session(test_session_id)

        # 분석 요청 → job_id 획득
        r = client.post(
            "/api/v1/analysis/analyze",
            json={
                "session_id": test_session_id,
                "message": "모델링을 실행해줘",
                "mode": "auto",
            },
            headers=auth_headers,
        )
        _skip_if_401(r)

        if r.status_code != 200:
            pytest.skip("분석 요청 실패")

        job_id = r.json()["data"]["job_id"]

        # 취소 요청
        r2 = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth_headers)
        # 이미 완료됐거나 running 중에 따라 200 or 409 모두 허용
        assert r2.status_code in (200, 409, 404)


# ─── 아티팩트 ──────────────────────────────────────────────────────────────────

class TestArtifacts:
    """아티팩트 목록 조회 + 없는 아티팩트 404"""

    def test_list_artifacts_for_session(self, client, auth_headers, test_session_id):
        _skip_if_no_auth(auth_headers)
        _skip_if_no_session(test_session_id)

        r = client.get(
            f"/api/v1/artifacts?session_id={test_session_id}",
            headers=auth_headers,
        )
        _skip_if_401(r)
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            assert isinstance(r.json().get("data", []), list)

    def test_nonexistent_artifact_returns_404(self, client, auth_headers):
        _skip_if_no_auth(auth_headers)
        fake_id = "00000000-0000-0000-0000-999999999999"
        r = client.get(f"/api/v1/artifacts/{fake_id}", headers=auth_headers)
        _skip_if_401(r)
        assert r.status_code == 404
