"""테스트 설정 및 공유 픽스처"""

import asyncio
import os
import pytest

# 테스트용 환경 변수 설정 (DB 없이 실행 가능하도록)
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_DB", "regression_platform_test")
os.environ.setdefault("POSTGRES_USER", "app")
os.environ.setdefault("POSTGRES_PASSWORD", "changeme")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
os.environ.setdefault("ARTIFACT_STORE_ROOT", "/tmp/test_artifacts")
os.environ.setdefault("BUILTIN_DATASET_PATH", "/tmp/datasets_builtin")


@pytest.fixture(scope="session")
def event_loop():
    """이벤트 루프 공유 픽스처"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def client():
    """FastAPI 테스트 클라이언트"""
    try:
        from fastapi.testclient import TestClient
        from app.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    except Exception as e:
        pytest.skip(f"앱 초기화 실패 (DB 연결 필요): {e}")


@pytest.fixture(scope="session")
def auth_headers(client):
    """인증 헤더 반환"""
    try:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "demo_user_1", "password": "Demo123!"},
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                token = data["data"]["access_token"]
                return {"Authorization": f"Bearer {token}"}
        return {}
    except Exception:
        return {}


@pytest.fixture
def test_session_id(client, auth_headers):
    """테스트용 세션 생성"""
    if not auth_headers:
        return None
    try:
        response = client.post(
            "/api/v1/sessions",
            json={"name": "테스트 세션", "ttl_days": 1},
            headers=auth_headers,
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                session_id = data["data"]["id"]
                yield session_id
                # 정리: 세션 삭제
                client.delete(f"/api/v1/sessions/{session_id}", headers=auth_headers)
                return
    except Exception:
        pass
    yield None
