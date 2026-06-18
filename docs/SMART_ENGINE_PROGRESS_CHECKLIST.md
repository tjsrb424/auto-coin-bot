# Smart Engine Progress Checklist

이 문서는 `btc_bot_smart_engine_dev_instructions.md`와 `btc_bot_v1_patch_autonomous_policy_no_capital.md` 기준으로 현재 개발 위치를 추적합니다.

## 현재 원칙

- 사용자는 전략을 직접 고르지 않는다.
- 사용자가 직접 수정하는 값은 자동매매 ON/OFF, 최대 투입 금액, 일 손실률 제한 3개만 유지한다.
- 운용자본금 입력 기능은 만들지 않는다.
- 기준 자본은 `max_total_exposure_krw`만 사용한다.
- 운영 서버는 `SMART_ENGINE_LIVE_MODE=limited`, `SMART_ENGINE_SHADOW_MODE=false`로 실전 자동주문을 준비한다.
- `SMART_ENGINE_LIVE_MODE=limited`에서도 정책, 리스크, Shadow report, 리허설 리뷰 승인, 리허설 게이트를 모두 통과해야 주문 후보가 제출된다.
- `SMART_REHEARSAL_MAX_DAILY_ORDERS=0`은 리허설 일일 건수 제한 없음으로 해석한다.

## 완료

- [x] 보완 지시문을 repo 문서로 저장
- [x] Decision Snapshot 저장 구조 추가
- [x] Smart Engine Shadow Mode 판단 기록 추가
- [x] 분석근거 API 추가
- [x] 분석근거 프론트 화면 추가
- [x] 운용정책 테이블 추가
- [x] 운용정책 조회/수정 API 추가
- [x] 프론트 전략관리 흐름을 운용설정 중심으로 전환
- [x] Smart Engine 기준 자본을 `max_total_exposure_krw`로 전환
- [x] 실제 주문 리스크 필터에 운용정책 강제 적용
- [x] 정책 차단 사유를 분석근거/자동매매/알림로그에 노출
- [x] 정책 차단 상세 계산값을 risk log/API로 확장
- [x] 정책 차단 상세 드릴다운 UI 추가
- [x] Shadow Mode 성과 리포트 API 추가
- [x] Shadow 판단 이후 캔들 markout 평가 추가
- [x] 분석근거 화면에 Shadow 승격 준비도/방향 적중률 표시
- [x] 내부 신호 라이브러리 모듈화
- [x] Market Regime Engine 모듈화
- [x] Target Exposure Engine 모듈화
- [x] 외부요인 Provider placeholder 인터페이스 추가
- [x] BTC/USD momentum 실제 provider 연결
- [x] 김치프리미엄 preview 계산 연결
- [x] 공포탐욕 지수 provider 연결
- [x] 거래소 공지 리스크 provider 연결
- [x] 뉴스 감성 provider-ready 연결
- [x] 외부요인 hard block/risk score 집계 추가
- [x] 외부요인 값을 Target Exposure 산식에 보수적으로 반영
- [x] Smart Engine 주문 후보 승격 게이트 추가
- [x] 제한 실주문 연결 코드 추가
- [x] 제한 실주문 기본 OFF 유지
- [x] 제한 실주문 1회 상한을 `max_total_exposure_krw * 20%`, 기존 리스크 상한, 남은 exposure, 사용 가능 KRW 중 최솟값으로 제한
- [x] 제한 실주문 소액 리허설 게이트 코드화
- [x] 분석근거 화면에 내부 신호/외부요인/승격상태/리허설 상태 표시
- [x] limited 전환 전 운영자용 readiness API 확장
- [x] 운용설정 화면에 limited 전환 점검 패널 추가
- [x] Smart Engine SELL/REDUCE 제한 실주문 경로 추가
- [x] 리허설 주문 prefix `smart-rehearsal-` 로그 기록
- [x] Shadow report에 리허설 결과 요약 및 review recommendation 반영

## 리허설 게이트 기준

- [x] 하루 Smart 제한 주문 수 제한: 기본 1회
- [x] 최소 주문 금액 제한: 기본 10,000 KRW
- [x] 리스크 점수 제한: 기본 60 이하
- [x] 허용 시간대 제한: 기본 KST 09:00-23:00
- [x] 실패 시 주문하지 않고 `promotion_blockers`와 `policy_preview.rehearsal`에 사유 기록

## 아직 남은 단계

- [ ] 실계좌에서 `SMART_ENGINE_LIVE_MODE=limited` 소액 리허설 1회 수행
- [ ] 관리자 화면에서 최신 리허설 결과를 검토 승인 또는 반려
- [ ] 리허설 결과를 Shadow report/주문 로그와 대조해 승격 기준 보정
- [ ] 전략관리 잔여 UI를 내부 신호 읽기 전용 화면으로 더 정리

## 현재 위치

현재는 "제한 실주문 코드 준비, 기본 OFF, 소액 리허설 게이트 구현, limited 전환 점검 화면/API, 외부요인 provider 2차 반영, SELL/REDUCE 제한 실주문 경로 준비" 단계까지 완료되었습니다.

다음 운영 단계는 소액 수동 주문/취소 smoke test, 리허설 결과 UI 승인, `AUTO STRATEGY ENABLE` 수동 시작, live order log의 `order_uuid` 확인입니다.
