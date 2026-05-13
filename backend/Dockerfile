# 체크리스트 #67: Backend Dockerfile (staging 우선).
#
# 절대 원칙 (CLAUDE.md):
#   - 실 API key / Secret을 이미지에 굽지 *않는다*. .env / .env.staging은
#     .dockerignore로 제외. 운영자가 compose의 environment 또는 secrets로 주입.
#   - LIVE flag default false — 본 이미지로 만든 컨테이너가 켜지자마자 실거래
#     주문을 만드는 일이 없도록 보수적.
#   - non-root user로 실행. 컨테이너 escape 위험 축소.
#   - multi-stage build로 빌드 도구를 final 이미지에 포함하지 않음.

# ---- builder ----
FROM python:3.13-slim AS builder

WORKDIR /build
# requirements 먼저 복사 — 의존성 캐시 활용
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- runtime ----
FROM python:3.13-slim AS runtime

# 보안 / OS 패치 정도. 최소.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# non-root user
RUN useradd -m -s /bin/bash -u 10001 app
WORKDIR /app

# 의존성만 final 이미지로 복사
COPY --from=builder /install /usr/local

# 애플리케이션 코드
COPY --chown=app:app app ./app
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app alembic ./alembic
# .env / .env.staging 은 .dockerignore로 제외 (Secret 격리)

USER app

# ----- staging 안전 default -----
# 본 이미지로 만든 컨테이너는 *기본적으로* LIVE flag OFF.
# compose 또는 운영자가 env로 override할 수 있지만, 본 PR 정책상 staging에서는
# true로 설정하지 *않는다* (docs/staging_environment.md §1).
ENV APP_ENV=staging \
    DEFAULT_MODE=SIMULATION \
    ENABLE_LIVE_TRADING=false \
    ENABLE_AI_EXECUTION=false \
    ENABLE_FUTURES_LIVE_TRADING=false \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=4 \
  CMD curl -fsS http://127.0.0.1:8000/api/status || exit 1

# uvicorn — single worker로 가볍게. 운영은 별도 PR에서 gunicorn + uvicorn worker.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
