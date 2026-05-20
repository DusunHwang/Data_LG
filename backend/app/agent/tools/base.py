"""도메인 도구의 공통 베이스 클래스.

smolagents ``Tool``은 ``forward`` 메서드 시그니처가 ``inputs`` 키와 정확히
일치해야 한다 (``**kwargs`` 불가). 따라서 베이스는 ``forward``를 직접 갖지
않고, 자식이 정의한 ``forward``가 호출하는 ``_persist_execution`` 헬퍼를
제공한다.

자식 클래스 작성 규약:

    class ProfileTool(ArtifactRecordingTool):
        name = "profile_dataset"
        description = "..."
        inputs = {"columns": {"type": "array", "description": "..."}}
        output_type = "object"

        def forward(self, columns=None):
            return self._persist_execution(self._execute(columns=columns))

        def _execute(self, columns=None) -> dict:
            # 실제 분석 로직
            return {
                "summary": "...",
                "artifacts": [
                    {
                        "type": "report",
                        "name": "...",
                        "content_bytes": b"...",
                        "filename": "...",
                        "mime_type": "...",
                    },
                ],
                "extra": {...},   # 선택
            }

``_persist_execution``은 각 artifact을 recorder에 위임하고 agent에게 돌려줄
dict(``summary``, ``recorded_artifact_ids``, ``artifacts`` 메타, extra)를 만든다.
"""

from __future__ import annotations

from typing import Any

from smolagents import Tool

from app.agent.callbacks.persist import ArtifactRecorder
from app.core.logging import get_logger

logger = get_logger(__name__)


class ArtifactRecordingTool(Tool):
    """프로젝트 내 모든 도메인 도구의 공통 부모.

    smolagents.Tool의 클래스 속성 규약(name/description/inputs/output_type)을
    그대로 따른다. ``__init__``으로 recorder/context를 보존하고,
    자식 클래스의 ``forward``는 ``_persist_execution(self._execute(...))``를
    반환하면 된다.
    """

    # 자식 클래스가 반드시 채워야 하는 메타데이터
    name: str = ""
    description: str = ""
    inputs: dict[str, dict[str, Any]] = {}
    output_type: str = "object"

    def __init__(self, recorder: ArtifactRecorder, context: dict) -> None:
        super().__init__()
        self.recorder = recorder
        self.context = context

    # 자식 클래스가 구현
    def _execute(self, **kwargs: Any) -> dict:
        raise NotImplementedError(
            f"{type(self).__name__}._execute must be implemented by subclass"
        )

    # 자식의 forward가 호출하는 헬퍼
    def _persist_execution(self, result: Any) -> dict:
        if not isinstance(result, dict):
            raise TypeError(
                f"{type(self).__name__}._execute는 dict를 반환해야 합니다. got: {type(result)}"
            )

        recorded_ids: list[str] = []
        artifacts_meta: list[dict] = []
        for artifact in result.get("artifacts") or []:
            try:
                artifact_id = self.recorder.record_artifact(
                    artifact_type=artifact["type"],
                    name=artifact.get("name", artifact.get("filename", "artifact")),
                    content_bytes=artifact["content_bytes"],
                    filename=artifact.get("filename", artifact.get("name", "artifact.bin")),
                    mime_type=artifact.get("mime_type"),
                    preview=artifact.get("preview"),
                    meta=artifact.get("meta"),
                    dataset_id=artifact.get("dataset_id"),
                )
                recorded_ids.append(artifact_id)
                artifacts_meta.append(
                    {
                        "id": artifact_id,
                        "type": artifact["type"],
                        "name": artifact.get("name"),
                    }
                )
            except Exception as e:
                logger.warning(
                    "도구 산출물 영속화 실패",
                    tool=type(self).__name__,
                    artifact_name=artifact.get("name"),
                    error=str(e),
                )

        return {
            "summary": result.get("summary", ""),
            "recorded_artifact_ids": recorded_ids,
            "artifacts": artifacts_meta,
            **(result.get("extra") or {}),
        }


__all__ = ["ArtifactRecordingTool"]
