// fix(main-frontend-ci-baseline): CI 안정화 setup.
//
// 근본 원인: Frontend CI 의 "Test (fast)" 단계가 *간헐적* 으로 실패했다(로컬
// 6/6 통과 — Node24/fresh npm ci/UTC tz 모두 안정, 그러나 ubuntu 2-core CI
// runner 부하 시 async 단언 타임아웃). testing-library `waitFor` 의 기본
// asyncUtilTimeout 은 1000ms 라, 부하가 큰 jsdom 대형 스위트(2490 tests)에서
// 느린 CI runner 가 1초를 *간헐적* 으로 초과해 정상 테스트가 spurious 실패했다.
//
// 본 setup 은 *테스트를 skip/삭제하지 않는다*. 비동기 단언의 허용 시간만 늘려
// 느린-but-정상 테스트에 여유를 준다 — 실제로 깨진 테스트는 시간과 무관하게
// 여전히 실패한다(가짜 통과 0건).
import { configure } from "@testing-library/react";

configure({
  // waitFor / findBy* 의 기본 1000ms → 15000ms. ubuntu 2-core CI runner 가
  // 대형 jsdom 스위트(2600+ tests)를 병렬 실행할 때 event loop 가 starve 되어
  // 정상 테스트의 비동기 단언(예: PaperCapitalCard P-16 의 localStorage mirror
  // waitFor)이 5s 를 *간헐적* 으로 초과해 spurious 실패하던 회귀를 막는다.
  // skip/삭제 0건 — 실제로 깨진 테스트는 시간과 무관하게 여전히 실패한다.
  asyncUtilTimeout: 15000,
});
