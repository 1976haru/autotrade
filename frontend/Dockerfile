# 체크리스트 #67: Frontend Dockerfile (staging 우선).
#
# 절대 원칙:
#   - frontend는 *Secret을 저장하지 않는다* — 빌드 시점에 inline되는 VITE_*
#     환경변수만 사용. backend Token / 계좌번호는 절대 build args / env로
#     주입 X.
#   - non-root user.
#   - production 빌드 산출물(dist/)만 final 이미지에 포함 — 소스 미포함.
#   - serve는 vite preview (단순 정적 SPA serving + SPA fallback).

# ---- builder ----
FROM node:20-alpine AS builder

WORKDIR /build

# 의존성 먼저
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

# 소스 + index.html
COPY . .

# Staging 빌드 default — 운영자가 build arg로 override 가능.
# VITE_BACKEND_URL은 staging compose 안에서 backend 컨테이너로 향한다.
ARG VITE_BACKEND_URL=http://localhost:18000
ARG VITE_BASE_PATH=/
ARG VITE_DEMO_MODE=false
ARG VITE_ENABLE_FUTURES_TAB=false
ARG VITE_FEEDBACK_EMAIL=

ENV VITE_BACKEND_URL=${VITE_BACKEND_URL} \
    VITE_BASE_PATH=${VITE_BASE_PATH} \
    VITE_DEMO_MODE=${VITE_DEMO_MODE} \
    VITE_ENABLE_FUTURES_TAB=${VITE_ENABLE_FUTURES_TAB} \
    VITE_FEEDBACK_EMAIL=${VITE_FEEDBACK_EMAIL}

RUN npm run build

# ---- runtime ----
FROM node:20-alpine AS runtime

# vite는 dev dep이지만 preview는 npm scripts 통해 vite 바이너리 호출이 필요.
# 더 가벼운 대안은 정적 web server(nginx) — 운영 PR에서 검토.
WORKDIR /app

RUN addgroup -S app && adduser -S app -G app
# vite preview만 사용하므로 node_modules 최소화 — 빌드 dir만 가져옴.
COPY --from=builder /build/dist ./dist
COPY --from=builder /build/package.json ./package.json
COPY --from=builder /build/node_modules ./node_modules

USER app

EXPOSE 5173

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=4 \
  CMD wget -q --spider http://127.0.0.1:5173/ || exit 1

# vite preview — SPA fallback 자동 (vite 8+). host 0.0.0.0으로 컨테이너 외부 접근 허용.
CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "5173"]
