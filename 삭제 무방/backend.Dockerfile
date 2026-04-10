FROM python:3.11-slim

# 작업 디렉토리 설정
WORKDIR /app

# 시스템 패키지 설치 (한글 폰트 포함)
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    curl \
    fonts-nanum \
    fontconfig \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

# matplotlib 폰트 캐시 초기화 (설치된 폰트 인식)
RUN python -c "import matplotlib; matplotlib.font_manager._load_fontmanager(try_read_cache=False)" 2>/dev/null || true

# pip 업그레이드 및 uv 설치
RUN pip install --upgrade pip && pip install uv

# 의존성 파일 복사
COPY pyproject.toml .

# 의존성 설치 (uv 사용)
RUN uv pip install --system ".[dev]"

# /app을 Python 경로에 추가 (site-packages .pth 방식 - alembic 패키지 shadowing 방지)
RUN python -c "import site; open(site.getsitepackages()[0]+'/_app_src.pth','w').write('/app\n')"

# 애플리케이션 코드 복사
COPY . .

# 아티팩트 디렉토리 생성
RUN mkdir -p /data/app/artifacts

# 포트 노출
EXPOSE 8000

# 기본 실행 명령
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
