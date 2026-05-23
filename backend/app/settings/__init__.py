"""Backend persistent user-settings package.

Paper 자금 운용 기준 등 *사용자가 EXE 에서 설정한 값* 을 OS 사용자 설정 폴더에
영구 저장한다. **API key / Secret / 계좌번호 / 토큰은 절대 저장하지 않는다** —
그것들은 `.env` 만의 책임이며 본 패키지와 분리된다.
"""
