"""데이터셋 API 테스트"""

import io
import pytest


class TestBuiltinDatasets:
    """내장 데이터셋 테스트"""

    def test_list_builtin_datasets(self, client, auth_headers, test_session_id):
        """내장 데이터셋 목록 조회"""
        if not test_session_id:
            pytest.skip("세션 생성 불가")
        response = client.get(
            f"/api/v1/sessions/{test_session_id}/datasets/builtin-list",
            headers=auth_headers,
        )
        if response.status_code == 401:
            pytest.skip("인증 서버 미연결")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        items = data["data"]
        assert len(items) == 4
        keys = [item["key"] for item in items]
        assert "manufacturing_regression" in keys
        assert "instrument_measurement" in keys
        assert "general_tabular_regression" in keys
        assert "large_sampling_regression" in keys

    def test_select_builtin_dataset(self, client, auth_headers, test_session_id):
        """내장 데이터셋 선택"""
        if not test_session_id:
            pytest.skip("세션 생성 불가")
        response = client.post(
            f"/api/v1/sessions/{test_session_id}/datasets/builtin",
            json={"builtin_key": "general_tabular_regression"},
            headers=auth_headers,
        )
        if response.status_code == 401:
            pytest.skip("인증 서버 미연결")
        # 내장 파일 없으면 404 가능 - 파일 존재 시 성공
        if response.status_code == 404:
            pytest.skip("내장 데이터셋 파일 없음 (generate_datasets.py 실행 필요)")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "id" in data["data"]

    def test_select_invalid_builtin_key(self, client, auth_headers, test_session_id):
        """잘못된 내장 데이터셋 키"""
        if not test_session_id:
            pytest.skip("세션 생성 불가")
        response = client.post(
            f"/api/v1/sessions/{test_session_id}/datasets/builtin",
            json={"builtin_key": "nonexistent_dataset"},
            headers=auth_headers,
        )
        if response.status_code == 401:
            pytest.skip("인증 서버 미연결")
        assert response.status_code in (400, 404)


class TestDatasetUpload:
    """데이터셋 업로드 테스트"""

    def test_upload_csv(self, client, auth_headers, test_session_id):
        """CSV 파일 업로드"""
        if not test_session_id:
            pytest.skip("세션 생성 불가")
        # 간단한 CSV 생성
        csv_content = "a,b,target\n1.0,2.0,10.0\n2.0,3.0,20.0\n3.0,4.0,30.0\n"
        response = client.post(
            f"/api/v1/sessions/{test_session_id}/datasets/upload",
            files={"file": ("test.csv", csv_content.encode(), "text/csv")},
            headers=auth_headers,
        )
        if response.status_code == 401:
            pytest.skip("인증 서버 미연결")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "id" in data["data"]

    def test_upload_invalid_extension(self, client, auth_headers, test_session_id):
        """허용되지 않는 파일 형식"""
        if not test_session_id:
            pytest.skip("세션 생성 불가")
        response = client.post(
            f"/api/v1/sessions/{test_session_id}/datasets/upload",
            files={"file": ("test.txt", b"hello", "text/plain")},
            headers=auth_headers,
        )
        if response.status_code == 401:
            pytest.skip("인증 서버 미연결")
        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False


class TestTargetCandidates:
    """타겟 후보 테스트"""

    def test_target_candidates_from_csv(self, client, auth_headers, test_session_id):
        """CSV 업로드 후 타겟 후보 조회"""
        if not test_session_id:
            pytest.skip("세션 생성 불가")
        # CSV 업로드
        csv_content = (
            "process_line,temp,pressure,yield_strength\n"
            "A,150.0,2.5,85.0\nB,155.0,2.6,87.0\nA,148.0,2.4,83.0\n"
        )
        upload_resp = client.post(
            f"/api/v1/sessions/{test_session_id}/datasets/upload",
            files={"file": ("test.csv", csv_content.encode(), "text/csv")},
            headers=auth_headers,
        )
        if upload_resp.status_code != 200:
            pytest.skip("업로드 실패")
        dataset_id = upload_resp.json()["data"]["id"]

        # 타겟 후보 조회
        response = client.get(
            f"/api/v1/sessions/{test_session_id}/datasets/{dataset_id}/target-candidates",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        candidates = data["data"].get("candidates", [])
        assert len(candidates) <= 3  # 최대 3개 추천
