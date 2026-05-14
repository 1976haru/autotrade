# Agent Trader v1 — 앱 아이콘 placeholder

본 PR 시점에는 실제 아이콘 binary 가 커밋되지 않았다. 빌드 전에 다음 파일을
이 디렉터리에 채워 넣어야 `cargo tauri build` 가 성공한다.

## 필요한 파일

| 파일 | 용도 | 권장 크기 |
|---|---|---|
| `32x32.png`        | 시스템 tray / 작은 아이콘 | 32×32 |
| `128x128.png`      | 일반 아이콘 | 128×128 |
| `128x128@2x.png`   | HiDPI | 256×256 |
| `icon.ico`         | Windows installer / 작업 표시줄 | multi-resolution `.ico` (16/24/32/48/64/128/256) |
| `icon.icns`        | macOS bundle (선택 — Windows 전용 빌드면 생략 가능) | multi-resolution `.icns` |

## 생성 방법 (권장)

`tauri-icon` CLI 가 PNG 한 장으로부터 ico / icns / 모든 크기 PNG 를 자동
생성해 준다:

```powershell
# Rust 툴체인 + cargo install tauri-cli@^2 설치 후
cargo tauri icon path/to/source.png
```

또는 GUI 도구(예: GIMP, ImageMagick `convert`)로 수동 변환.

## 라이선스 / 브랜드

- 아이콘 소재는 자체 제작 또는 라이선스 허용 자산만 사용한다.
- 베타 시점에는 단순 모노톤 마크면 충분 — 정식 브랜딩은 별도 PR.
- 외부 trademark / 증권사 로고를 변형해 사용하지 않는다 (라이선스 / 오해 소지).

## 본 PR 에서 commit 한 이유

`src-tauri/tauri.conf.json` 의 `bundle.icon` 가 본 디렉터리를 참조하므로,
README 라도 commit 해 두지 않으면 디렉터리 자체가 git 에 등장하지 않아 후속
PR 의 빌드 가이드가 혼란스러워진다.
