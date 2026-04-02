.PHONY: help build up down logs migrate seed generate-datasets test lint fmt

# 기본 도움말
help:
	@echo "사용 가능한 명령어:"
	@echo "  make build             - Docker 이미지 빌드"
	@echo "  make up                - 서비스 시작"
	@echo "  make down              - 서비스 종료"
	@echo "  make logs              - 로그 확인"
	@echo "  make migrate           - DB 마이그레이션 실행"
	@echo "  make seed              - 시드 데이터 입력"
	@echo "  make generate-datasets - 내장 데이터셋 생성"
	@echo "  make test              - 테스트 실행"
	@echo "  make lint              - 린트 검사"
	@echo "  make fmt               - 코드 포맷팅"
	@echo "  make reset             - DB 초기화 및 재시작"

# Docker 이미지 빌드
build:
	docker compose build

# 서비스 시작
up:
	docker compose up -d

# 서비스 종료
down:
	docker compose down

# 로그 확인
logs:
	docker compose logs -f

# 백엔드 로그만 확인
logs-backend:
	docker compose logs -f backend

# 워커 로그만 확인
logs-worker:
	docker compose logs -f worker

# DB 마이그레이션 실행
migrate:
	docker compose exec backend alembic upgrade head

# 시드 데이터 입력
seed:
	docker compose exec backend python -m app.db.seed

# 내장 데이터셋 생성
generate-datasets:
	python datasets_builtin/generate_datasets.py

# 테스트 실행
test:
	docker compose exec backend pytest tests/ -v

# 린트 검사
lint:
	docker compose exec backend ruff check app/

# 코드 포맷팅
fmt:
	docker compose exec backend ruff format app/

# DB 초기화 및 재시작
reset:
	docker compose down -v
	docker compose up -d
	@echo "DB 초기화 완료. 마이그레이션 대기 중..."
	sleep 5
	$(MAKE) migrate
	$(MAKE) seed

# 개발 환경 전체 설정
setup-dev:
	cp -n .env.example .env || true
	$(MAKE) generate-datasets
	$(MAKE) build
	$(MAKE) up
	sleep 5
	$(MAKE) migrate
	$(MAKE) seed
	@echo "개발 환경 설정 완료!"

# 백엔드 쉘 접속
shell-backend:
	docker compose exec backend bash

# DB 접속
shell-db:
	docker compose exec postgres psql -U $${POSTGRES_USER:-app} -d $${POSTGRES_DB:-regression_platform}
