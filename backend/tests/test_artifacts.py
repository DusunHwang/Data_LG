"""Artifact API 테스트"""

import pytest


class TestArtifactAPI:
    """Artifact API 테스트"""

    def test_get_nonexistent_artifact(self, client, auth_headers, test_session_id):
        """존재하지 않는 아티팩트 조회"""
        if not test_session_id:
            pytest.skip("세션 생성 불가")
        fake_artifact_id = "00000000-0000-0000-0000-000000000003"
        response = client.get(
            f"/api/v1/sessions/{test_session_id}/artifacts/{fake_artifact_id}",
            headers=auth_headers,
        )
        if response.status_code == 401:
            pytest.skip("인증 서버 미연결")
        assert response.status_code == 404
        data = response.json()
        assert data["success"] is False

    def test_artifact_download_nonexistent(self, client, auth_headers, test_session_id):
        """존재하지 않는 아티팩트 다운로드"""
        if not test_session_id:
            pytest.skip("세션 생성 불가")
        fake_artifact_id = "00000000-0000-0000-0000-000000000004"
        response = client.get(
            f"/api/v1/sessions/{test_session_id}/artifacts/{fake_artifact_id}/download",
            headers=auth_headers,
        )
        if response.status_code == 401:
            pytest.skip("인증 서버 미연결")
        assert response.status_code == 404
