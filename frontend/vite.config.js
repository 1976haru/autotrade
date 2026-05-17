import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

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

// https://vite.dev/config/
export default defineConfig({
  base: _basePath,
  plugins: [react()],
  // build-time inject — runtime fetch 없이 항상 사용 가능.
  define: {
    'import.meta.env.VITE_APP_VERSION': JSON.stringify(_pkgVersion),
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.{test,spec}.{js,jsx}'],
    globals: false,
  },
})
