FROM python:3.11-slim

# 작업 디렉토리 설정
WORKDIR /app

# 시스템 패키지 설치
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# pip 업그레이드 및 uv 설치
RUN pip install --upgrade pip && pip install uv

# 의존성 파일 복사
COPY pyproject.toml .

# 의존성 설치
RUN uv pip install --system -e .

# 애플리케이션 코드 복사
COPY . .

# 포트 노출
EXPOSE 8501

# 기본 실행 명령
CMD ["streamlit", "run", "app/main.py", "--server.port", "8501", "--server.address", "0.0.0.0"]
