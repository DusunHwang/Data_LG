"""공통 응답 스키마"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

DataT = TypeVar("DataT")


class ErrorDetail(BaseModel):
    """에러 상세 정보"""
    code: str
    message: str
    details: dict[str, Any] | None = None


class SuccessResponse(BaseModel, Generic[DataT]):
    """성공 응답"""
    success: bool = True
    data: DataT


class ErrorResponse(BaseModel):
    """에러 응답"""
    success: bool = False
    error: ErrorDetail


class PaginatedMeta(BaseModel):
    """페이지네이션 메타 정보"""
    total: int
    skip: int
    limit: int
    has_more: bool


class PaginatedResponse(BaseModel, Generic[DataT]):
    """페이지네이션 응답"""
    success: bool = True
    data: list[DataT]
    meta: PaginatedMeta


def success_response(data: Any) -> dict[str, Any]:
    """성공 응답 딕셔너리 생성"""
    return {"success": True, "data": data}


def error_response(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """에러 응답 딕셔너리 생성"""
    return {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
    }


# 에러 코드 상수
class ErrorCode:
    """에러 코드 상수"""
    # 인증
    UNAUTHORIZED = "UNAUTHORIZED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_INVALID = "TOKEN_INVALID"
    FORBIDDEN = "FORBIDDEN"

    # 리소스
    NOT_FOUND = "NOT_FOUND"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    DATASET_NOT_FOUND = "DATASET_NOT_FOUND"
    BRANCH_NOT_FOUND = "BRANCH_NOT_FOUND"
    STEP_NOT_FOUND = "STEP_NOT_FOUND"
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"

    # 비즈니스 로직
    ACTIVE_JOB_EXISTS = "ACTIVE_JOB_EXISTS"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    INVALID_FILE_TYPE = "INVALID_FILE_TYPE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    NO_ACTIVE_DATASET = "NO_ACTIVE_DATASET"
    NO_CHAMPION_MODEL = "NO_CHAMPION_MODEL"
    BUILTIN_NOT_FOUND = "BUILTIN_NOT_FOUND"
    JOB_NOT_CANCELLABLE = "JOB_NOT_CANCELLABLE"

    # 서버
    INTERNAL_ERROR = "INTERNAL_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"


# 에러 메시지 (한국어)
ERROR_MESSAGES = {
    ErrorCode.UNAUTHORIZED: "인증이 필요합니다.",
    ErrorCode.INVALID_CREDENTIALS: "아이디 또는 비밀번호가 올바르지 않습니다.",
    ErrorCode.TOKEN_EXPIRED: "토큰이 만료되었습니다.",
    ErrorCode.TOKEN_INVALID: "유효하지 않은 토큰입니다.",
    ErrorCode.FORBIDDEN: "접근 권한이 없습니다.",
    ErrorCode.NOT_FOUND: "리소스를 찾을 수 없습니다.",
    ErrorCode.SESSION_NOT_FOUND: "세션을 찾을 수 없습니다.",
    ErrorCode.DATASET_NOT_FOUND: "데이터셋을 찾을 수 없습니다.",
    ErrorCode.BRANCH_NOT_FOUND: "브랜치를 찾을 수 없습니다.",
    ErrorCode.STEP_NOT_FOUND: "스텝을 찾을 수 없습니다.",
    ErrorCode.ARTIFACT_NOT_FOUND: "아티팩트를 찾을 수 없습니다.",
    ErrorCode.JOB_NOT_FOUND: "작업을 찾을 수 없습니다.",
    ErrorCode.MODEL_NOT_FOUND: "모델을 찾을 수 없습니다.",
    ErrorCode.ACTIVE_JOB_EXISTS: "현재 세션에서 이미 실행 중인 작업이 있습니다.",
    ErrorCode.SESSION_EXPIRED: "세션이 만료되었습니다.",
    ErrorCode.INVALID_FILE_TYPE: "허용되지 않는 파일 형식입니다. CSV, XLSX, Parquet 파일만 허용됩니다.",
    ErrorCode.FILE_TOO_LARGE: "파일 크기가 제한을 초과했습니다.",
    ErrorCode.NO_ACTIVE_DATASET: "활성 데이터셋이 없습니다.",
    ErrorCode.NO_CHAMPION_MODEL: "챔피언 모델이 없습니다.",
    ErrorCode.BUILTIN_NOT_FOUND: "내장 데이터셋을 찾을 수 없습니다.",
    ErrorCode.JOB_NOT_CANCELLABLE: "취소할 수 없는 작업입니다.",
    ErrorCode.INTERNAL_ERROR: "내부 서버 오류가 발생했습니다.",
    ErrorCode.VALIDATION_ERROR: "입력 데이터가 유효하지 않습니다.",
}
