import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { registerServiceWorker } from './registerServiceWorker.js'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// #63: PWA service worker 등록. 실패해도 앱은 정상 동작 — registerServiceWorker
// 가 내부에서 try/catch로 모든 예외를 흡수하고 null을 반환한다.
// SSR / 테스트 환경에서는 navigator.serviceWorker가 없어 noop으로 끝남.
registerServiceWorker()
