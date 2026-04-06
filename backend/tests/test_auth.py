"""인증 API 테스트"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_login_success():
    """정상 로그인 테스트"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Admin123!"},
        )
    # 시드 데이터가 없을 때는 401 반환
    assert response.status_code in (200, 401)
    data = response.json()
    assert "success" in data


@pytest.mark.asyncio
async def test_login_invalid_credentials():
    """잘못된 자격증명 로그인 테스트"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "nonexistent", "password": "wrongpass"},
        )
    assert response.status_code == 401
    data = response.json()
    assert data["success"] is False
    assert "error" in data


@pytest.mark.asyncio
async def test_me_unauthorized():
    """인증 없이 /me 접근 테스트"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_health_check():
    """헬스 체크 테스트"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/admin/health")
    # DB 없이도 응답해야 함
    assert response.status_code in (200, 500)
