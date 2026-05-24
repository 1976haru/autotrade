import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { execSync } from 'node:child_process'

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 214: GitHub Pages는 https://1976haru.github.io/autotrade/ 경로에 배포되므로
// 빌드 산출물이 /autotrade/assets/... 를 참조해야 한다. 로컬 dev / preview /
// 일반 npm run build는 base="/"로 동작 — Pages 배포 워크플로만
// VITE_BASE_PATH=/autotrade/ 를 env로 주입해 prefix를 갈아끼운다.
const _basePath = process.env.VITE_BASE_PATH || '/'

// fix/update-banner-stale-release-notes: package.json::version 을 build-time
// 에 주입 — appInfo.js / UpdateBanner / VersionBadge 가 모두 *단일 진실*
// (package.json) 에서 버전을 읽도록 한다. tauri.conf.json::version 도
// package.json 과 1:1 매핑 (release 시 함께 갱신). hard-coded fallback 은
// "0.0.0-unknown" 으로 의도적으로 *부자연스럽게* 두어, 주입 실패 시 화면에서
// 즉시 감지 가능.
const _here = dirname(fileURLToPath(import.meta.url))
let _pkgVersion = '0.0.0-unknown'
try {
  const pkg = JSON.parse(readFileSync(resolve(_here, 'package.json'), 'utf-8'))
  if (typeof pkg.version === 'string' && pkg.version.trim()) {
    _pkgVersion = pkg.version.trim()
  }
} catch {
  // package.json read 실패 — fallback 유지. 정상 빌드라면 절대 도달 X.
}

// #57 / 7-05: build metadata 주입. 사용자가 실행 중인 EXE 가 어느 commit /
// 빌드 시각인지 확인할 수 있도록 git 정보를 build-time 에 baking 한다.
// 우선순위: 명시 env(CI 가 주입) → git 명령 → 'unknown' fallback.
//   - git 명령 실패(checkout 에 .git 없음 등)해도 빌드는 절대 실패하지 않는다.
//   - secret / env dump 0건 — git short/long hash, branch, dirty 여부만.
function _git(args, fallback = 'unknown') {
  try {
    const out = execSync(`git ${args}`, {
      cwd: _here,
      stdio: ['ignore', 'pipe', 'ignore'],
    }).toString().trim()
    return out || fallback
  } catch {
    return fallback
  }
}

const _env = process.env
const _commitFull = _env.VITE_GIT_COMMIT_FULL || _git('rev-parse HEAD')
const _commit =
  _env.VITE_GIT_COMMIT ||
  (_commitFull !== 'unknown' ? _commitFull.slice(0, 7) : _git('rev-parse --short HEAD'))
const _branch = _env.VITE_GIT_BRANCH || _git('rev-parse --abbrev-ref HEAD')
const _buildTime = _env.VITE_BUILD_TIME || new Date().toISOString()
const _buildSource =
  _env.VITE_BUILD_SOURCE || (_env.GITHUB_ACTIONS ? 'github-actions' : 'local-build')
const _buildChannel = _env.VITE_BUILD_CHANNEL || 'paper-beta'
const _gitDirty =
  _env.VITE_GIT_DIRTY != null
    ? (_env.VITE_GIT_DIRTY === 'true' || _env.VITE_GIT_DIRTY === '1')
    : (_git('status --porcelain', '') !== '')

// https://vite.dev/config/
export default defineConfig({
  base: _basePath,
  plugins: [react()],
  // build-time inject — runtime fetch 없이 항상 사용 가능.
  define: {
    'import.meta.env.VITE_APP_VERSION': JSON.stringify(_pkgVersion),
    // #57 / 7-05 build metadata (secret 0건 — git hash / branch / time 만).
    'import.meta.env.VITE_GIT_COMMIT': JSON.stringify(_commit),
    'import.meta.env.VITE_GIT_COMMIT_FULL': JSON.stringify(_commitFull),
    'import.meta.env.VITE_GIT_BRANCH': JSON.stringify(_branch),
    'import.meta.env.VITE_BUILD_TIME': JSON.stringify(_buildTime),
    'import.meta.env.VITE_BUILD_SOURCE': JSON.stringify(_buildSource),
    'import.meta.env.VITE_BUILD_CHANNEL': JSON.stringify(_buildChannel),
    'import.meta.env.VITE_GIT_DIRTY': JSON.stringify(String(_gitDirty)),
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.{test,spec}.{js,jsx}'],
    globals: false,
    // fix(main-frontend-ci-baseline): CI 안정화.
    // setupFiles 가 testing-library asyncUtilTimeout 을 5s 로 올려 느린 CI
    // runner 의 간헐적 waitFor 타임아웃(가짜 실패)을 방지한다 (skip/삭제 0건).
    setupFiles: ['./vitest.setup.js'],
    // 개별 테스트 본문 타임아웃도 부하 headroom 확보 (기본 5s → 30s).
    // asyncUtilTimeout(15s) 보다 충분히 커야 waitFor 가 timeout 되기 전에
    // 테스트 본문이 먼저 죽지 않는다.
    testTimeout: 30000,
    hookTimeout: 30000,
  },
})
