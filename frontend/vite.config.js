import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 214: GitHub Pages는 https://1976haru.github.io/autotrade/ 경로에 배포되므로
// 빌드 산출물이 /autotrade/assets/... 를 참조해야 한다. 로컬 dev / preview /
// 일반 npm run build는 base="/"로 동작 — Pages 배포 워크플로만
// VITE_BASE_PATH=/autotrade/ 를 env로 주입해 prefix를 갈아끼운다.
const _basePath = process.env.VITE_BASE_PATH || '/'

// https://vite.dev/config/
export default defineConfig({
  base: _basePath,
  plugins: [react()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.{test,spec}.{js,jsx}'],
    globals: false,
  },
})
