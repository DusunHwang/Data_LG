"""세션 API 테스트"""

import pytest


class TestSessionCRUD:
    """세션 CRUD 테스트"""

    def test_create_session_success(self, client, auth_headers):
        """세션 생성 성공"""
        response = client.post(
            "/api/v1/sessions",
            json={"name": "테스트 세션", "ttl_days": 7},
            headers=auth_headers,
        )
        # 인증 서버 없으면 401 허용
        if response.status_code == 401:
            pytest.skip("인증 서버 미연결")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "id" in data["data"]
        assert data["data"]["name"] == "테스트 세션"

    def test_create_session_unauthenticated(self, client):
        """미인증 세션 생성 실패"""
        response = client.post(
            "/api/v1/sessions",
            json={"name": "테스트 세션"},
        )
        assert response.status_code == 401

    def test_list_sessions(self, client, auth_headers):
        """세션 목록 조회"""
        response = client.get("/api/v1/sessions", headers=auth_headers)
        if response.status_code == 401:
            pytest.skip("인증 서버 미연결")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)

    def test_get_session_not_found(self, client, auth_headers):
        """존재하지 않는 세션 조회"""
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = client.get(f"/api/v1/sessions/{fake_id}", headers=auth_headers)
        if response.status_code == 401:
            pytest.skip("인증 서버 미연결")
        assert response.status_code == 404

    def test_session_lifecycle(self, client, auth_headers):
        """세션 생성 → 조회 → 수정 → 삭제 흐름"""
        if not auth_headers:
            pytest.skip("인증 서버 미연결")

        # 생성
        create_resp = client.post(
            "/api/v1/sessions",
            json={"name": "라이프사이클 테스트"},
            headers=auth_headers,
        )
        if create_resp.status_code == 401:
            pytest.skip("인증 서버 미연결")
        assert create_resp.status_code == 200
        session_id = create_resp.json()["data"]["id"]

        # 조회
        get_resp = client.get(f"/api/v1/sessions/{session_id}", headers=auth_headers)
        assert get_resp.status_code == 200

        # 수정
        patch_resp = client.patch(
            f"/api/v1/sessions/{session_id}",
            json={"name": "수정된 세션"},
            headers=auth_headers,
        )
        assert patch_resp.status_code == 200

        # 삭제
        del_resp = client.delete(f"/api/v1/sessions/{session_id}", headers=auth_headers)
        assert del_resp.status_code == 200
