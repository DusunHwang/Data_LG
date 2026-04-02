"""Job API 테스트"""

import pytest


class TestJobStatus:
    """작업 상태 조회 테스트"""

    def test_get_nonexistent_job(self, client, auth_headers):
        """존재하지 않는 작업 조회"""
        fake_id = "00000000-0000-0000-0000-000000000001"
        response = client.get(f"/api/v1/jobs/{fake_id}", headers=auth_headers)
        if response.status_code == 401:
            pytest.skip("인증 서버 미연결")
        assert response.status_code == 404
        data = response.json()
        assert data["success"] is False

    def test_get_active_job_empty(self, client, auth_headers, test_session_id):
        """활성 작업 없는 경우"""
        if not test_session_id:
            pytest.skip("세션 생성 불가")
        response = client.get(
            f"/api/v1/jobs/session/{test_session_id}/active",
            headers=auth_headers,
        )
        if response.status_code == 401:
            pytest.skip("인증 서버 미연결")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["job_id"] is None


class TestJobCancel:
    """작업 취소 테스트"""

    def test_cancel_nonexistent_job(self, client, auth_headers):
        """존재하지 않는 작업 취소"""
        fake_id = "00000000-0000-0000-0000-000000000002"
        response = client.post(
            f"/api/v1/jobs/{fake_id}/cancel",
            headers=auth_headers,
        )
        if response.status_code == 401:
            pytest.skip("인증 서버 미연결")
        assert response.status_code == 404
