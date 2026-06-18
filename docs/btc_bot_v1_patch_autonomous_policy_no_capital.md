# BTC/KRW 스마트 자동매매 봇 개발지시문 보완 패치

## 문서 목적

이 문서는 이미 전달되어 개발 중인 기존 개발지시문 v1에 대한 추가 보완 지시문이다.

기존 v1 문서의 큰 방향인 스마트 의사결정 엔진, Decision Snapshot, Feature Engine, Market Regime Engine, Target Exposure Engine, Shadow Mode, 분석근거 API 구조는 유지한다.

단, 사용자 운용 방식이 변경되었으므로 전략관리와 운용자본금 관련 요구사항을 아래 기준으로 정정한다.

## 핵심 변경 요약

기존 v1 문서에서는 사용자가 전략을 선택하거나 활성 전략을 관리하는 흐름이 일부 남아 있을 수 있다. 앞으로는 사용자가 전략을 직접 관리하지 않는다.

봇이 시장상태, 기술적 지표, 외부요인, 리스크 조건을 종합해서 내부적으로 판단한다.

사용자는 아래 3개 항목만 직접 설정한다.

| 사용자 설정 항목 | 설명 |
|---|---|
| 자동매매 ON/OFF | 봇이 실거래 주문을 실행할 수 있는지 여부 |
| 최대 투입 금액 | 봇이 BTC/KRW에 투입할 수 있는 최대 KRW 한도 |
| 일 손실률 제한 | 하루 손실이 지정 비율을 넘으면 신규 주문 및 추가매수를 차단 |

운용자본금 입력 기능은 만들지 않는다.

자본금은 사용자가 거래소 계좌에 직접 입금하는 것이므로, 봇은 거래소 KRW 잔고를 조회해서 주문 가능 여부만 판단한다.

## 기존 v1 문서에서 유지할 내용

아래 항목은 기존 v1 개발 방향을 그대로 유지한다.

- 기존 실거래 로직 즉시 제거 금지
- 신규 스마트 엔진은 우선 Shadow Mode로 동작
- Decision Snapshot 저장
- Feature Engine 추가
- Market Regime Engine 추가
- Target Exposure Engine 추가
- 외부요인 Provider 인터페이스 추가
- 분석근거 API 추가
- 리스크 안전장치 유지
- 기존 주문 실행 안정성 유지

## 기존 v1 문서에서 정정할 내용

### 1. 전략관리 개념 변경

기존 프론트의 전략관리 화면에서 사용자가 RSI, 이동평균 교차, 변동성 돌파 같은 전략을 직접 선택하거나 활성화하는 구조는 최종 목표 구조에서 제거한다.

앞으로 RSI, 이동평균 교차, 변동성 돌파 등은 사용자 선택 전략이 아니라 봇 내부의 신호 라이브러리로 취급한다.

즉 사용자는 전략을 고르지 않고, 봇이 현재 시장상태에 맞게 내부 신호를 조합한다.

변경 전 개념:

- 사용자가 전략 선택
- 선택된 전략 1개가 BUY, SELL, HOLD 생성
- 전략별 실거래 세션 실행

변경 후 개념:

- 사용자는 전략 선택하지 않음
- 봇이 내부 신호들을 자동 평가
- 시장상태에 따라 신호별 가중치 적용
- 최종적으로 목표 보유비중을 계산
- 현재 보유비중과 목표 보유비중의 차이만큼 주문 후보 생성

### 2. 전략관리 화면 처리

프론트에서 기존 전략관리 탭이 이미 존재한다면 즉시 삭제하지 말고 다음 중 하나로 전환한다.

권장안:

- 전략관리 탭을 운용설정 탭으로 변경
- 사용자가 설정할 수 있는 항목은 자동매매 ON/OFF, 최대 투입 금액, 일 손실률 제한만 노출
- RSI, 이동평균, 변동성 돌파 같은 전략 선택 UI는 제거하거나 읽기 전용 내부 신호 상태로 변경

대체안:

- 기존 전략관리 탭은 숨김 처리
- 새 운용설정 탭을 별도로 추가
- 기존 API와 DB는 마이그레이션 안정화 후 제거

### 3. 운용자본금 설정 제거

운용자본금 또는 자본금 투입 금액을 사용자가 입력하는 기능은 만들지 않는다.

제거 또는 비활성화 대상 예시:

- operation_capital_krw
- trading_capital_krw
- allocated_capital_krw
- user_capital_input
- capital_deposit_amount
- 운용자본금 입력 필드
- 자본금 투입 입력 필드

이미 구현 중이라면 즉시 삭제하지 말고 다음 방식으로 처리한다.

- DB 컬럼이 이미 있으면 nullable 또는 deprecated 상태로 둔다.
- 신규 로직에서는 해당 값을 사용하지 않는다.
- 프론트에서는 노출하지 않는다.
- API 응답에서는 하위 호환이 필요할 때만 null로 반환한다.
- 주문 한도 계산에는 절대 사용하지 않는다.

## 사용자 설정 기준

### 최종 사용자 설정 항목

사용자가 직접 변경할 수 있는 설정은 아래 3개만 유지한다.

| 필드명 | 타입 | 설명 |
|---|---|---|
| auto_trading_enabled | boolean | 자동매매 ON/OFF |
| max_total_exposure_krw | number | 봇이 BTC/KRW에 투입 가능한 최대 KRW 금액 |
| daily_loss_limit_pct | number | 하루 손실률 제한 |

### 설정 예시

| 항목 | 예시 |
|---|---:|
| 자동매매 | ON |
| 최대 투입 금액 | 500,000 KRW |
| 일 손실률 제한 | 3% |

이 경우 봇은 BTC/KRW에 최대 500,000원까지만 노출될 수 있다.

하루 손실 제한 금액은 아래 기준으로 계산한다.

일 손실 제한 금액 = 최대 투입 금액 x 일 손실률 제한

예시:

| 최대 투입 금액 | 일 손실률 제한 | 하루 손실 제한 금액 |
|---:|---:|---:|
| 500,000 KRW | 3% | 15,000 KRW |
| 1,000,000 KRW | 2% | 20,000 KRW |
| 300,000 KRW | 5% | 15,000 KRW |

거래소에 실제로 입금된 KRW가 더 많아도 봇은 max_total_exposure_krw를 넘겨서는 안 된다.

거래소 KRW 잔고가 max_total_exposure_krw보다 적으면 봇은 실제 사용 가능한 KRW 잔고 안에서만 주문한다.

## 주문 가능 금액 계산 기준

운용자본금 설정은 사용하지 않는다.

주문 가능 금액은 아래 값들로만 계산한다.

- 거래소 실제 KRW 잔고
- 봇의 현재 BTC 포지션 평가금액
- 사용자가 설정한 최대 투입 금액
- Target Exposure Engine이 계산한 목표 보유비중
- 리스크 필터 결과

계산 기준:

1. max_total_exposure_krw를 봇의 최대 운용 한도로 본다.
2. target_exposure_pct는 0에서 100 사이 값이다.
3. target_position_value_krw는 max_total_exposure_krw에 target_exposure_pct를 곱해 계산한다.
4. current_position_value_krw는 봇이 관리하는 BTC 포지션의 현재 평가금액이다.
5. target_position_value_krw가 current_position_value_krw보다 크면 차액만큼 매수 후보를 만든다.
6. target_position_value_krw가 current_position_value_krw보다 작으면 차액만큼 매도 후보를 만든다.
7. 매수 주문은 실제 KRW 잔고를 초과할 수 없다.
8. 전체 포지션 평가금액은 max_total_exposure_krw를 초과할 수 없다.

## Target Exposure Engine 정정

기존 v1 문서의 Target Exposure Engine은 유지하되, 기준 자본을 운용자본금이 아니라 max_total_exposure_krw로 고정한다.

예시:

| max_total_exposure_krw | target_exposure_pct | 목표 BTC 보유 평가금액 |
|---:|---:|---:|
| 500,000 | 20% | 100,000 KRW |
| 500,000 | 50% | 250,000 KRW |
| 500,000 | 80% | 400,000 KRW |

봇은 이 목표 평가금액에 맞춰 추가매수, 일부매도, 전량청산, 관망을 결정한다.

## Decision Snapshot 정정

기존 v1 문서의 decision_snapshot 구조는 유지하되, 사용자 선택 전략 중심 필드는 내부 엔진 중심으로 정정한다.

### 유지할 필드

- decided_at
- market
- timeframe
- candle_time_utc
- candle_time_kst
- legacy_signal
- current_bot_position_qty
- current_bot_position_value_krw
- current_exposure_pct
- target_exposure_pct
- action_hint
- confidence_score
- risk_score
- market_regime
- one_line_summary
- positive_reasons_json
- negative_reasons_json
- blockers_json
- raw_features_json
- created_at

### 정정할 필드

selected_strategy_id는 사용자 선택 전략이라는 의미로 사용하지 않는다.

아래 중 하나로 변경한다.

권장 필드:

- decision_engine_version
- active_decision_profile
- internal_signal_set_version

하위 호환이 필요해 selected_strategy_id를 유지해야 한다면 다음 조건을 따른다.

- nullable 허용
- 사용자 선택값으로 사용하지 않음
- 기존 단일 전략 결과를 legacy_signal로 남길 때만 참고
- 프론트에서 수정 불가

### 추가 권장 필드

| 필드명 | 설명 |
|---|---|
| internal_signals_json | RSI, 이동평균, 변동성, 외부요인 등 내부 신호별 점수 |
| max_total_exposure_krw | 판단 당시 적용된 최대 투입 금액 |
| daily_loss_limit_pct | 판단 당시 적용된 일 손실률 제한 |
| daily_loss_limit_krw | 계산된 일 손실 제한 금액 |
| available_krw_balance | 거래소에서 조회한 실제 KRW 잔고 |
| exposure_limit_blocked | 최대 투입 금액 초과로 차단되었는지 여부 |

## Bot Operation Policy 테이블

새 운용정책 테이블을 추가하거나 기존 설정 테이블을 정리한다.

권장 테이블명:

bot_operation_policy

권장 필드:

| 필드명 | 타입 | 설명 |
|---|---|---|
| id | string 또는 integer | 정책 ID |
| market | string | KRW-BTC 또는 BTC_KRW |
| auto_trading_enabled | boolean | 자동매매 ON/OFF |
| max_total_exposure_krw | number | 최대 투입 금액 |
| daily_loss_limit_pct | number | 일 손실률 제한 |
| created_at | datetime | 생성일 |
| updated_at | datetime | 수정일 |

프론트에서 수정 가능한 필드는 아래 3개뿐이다.

- auto_trading_enabled
- max_total_exposure_krw
- daily_loss_limit_pct

Shadow Mode, 엔진 버전, 내부 리스크 파라미터, 전략 가중치 등은 사용자 설정 UI에 노출하지 않는다.

## 정책 API

운용정책 조회와 수정 API를 추가한다.

### GET /api/bot/policy

응답 항목:

| 필드명 | 설명 |
|---|---|
| auto_trading_enabled | 자동매매 ON/OFF |
| max_total_exposure_krw | 최대 투입 금액 |
| daily_loss_limit_pct | 일 손실률 제한 |
| daily_loss_limit_krw | 계산된 하루 손실 제한 금액 |
| current_bot_position_value_krw | 현재 봇 포지션 평가금액 |
| available_krw_balance | 거래소 KRW 잔고 |
| exposure_usage_pct | 최대 투입 금액 대비 현재 사용률 |
| updated_at | 마지막 수정 시간 |

### PATCH /api/bot/policy

수정 가능 항목:

- auto_trading_enabled
- max_total_exposure_krw
- daily_loss_limit_pct

검증 규칙:

- max_total_exposure_krw는 0보다 커야 한다.
- daily_loss_limit_pct는 0보다 크고 100보다 작거나 같아야 한다.
- auto_trading_enabled가 false면 신규 주문을 만들지 않는다.
- max_total_exposure_krw가 현재 포지션 평가금액보다 낮아질 경우 즉시 강제 매도하지 않는다.
- 단, 다음 판단 주기에서 목표 보유비중과 리스크 정책에 따라 축소 후보를 만들 수 있다.

## 기존 전략 API 처리

기존 v1 개발 중 전략 활성화 API 또는 전략관리 API가 이미 있다면 즉시 삭제하지 않는다.

대신 다음 방식으로 하위 호환을 유지한다.

- 기존 전략 목록 조회 API는 읽기 전용으로 유지 가능
- 전략 활성화, 비활성화, 선택 API는 프론트에서 제거
- 백엔드에서는 deprecated 처리
- 신규 스마트 엔진은 기존 전략 활성화 상태에 의존하지 않음
- 기존 단일 전략 결과는 legacy_signal 또는 internal_signals_json의 일부로만 저장

## 프론트 화면 변경

### 대시보드

대시보드에는 아래 항목을 표시한다.

- 자동매매 상태
- 최대 투입 금액
- 현재 봇 포지션 평가금액
- 최대 투입 금액 대비 사용률
- 일 손실률 제한
- 오늘 실현 손익
- 오늘 평가 손익
- 일 손실 제한 접근률
- 현재 봇 판단 한줄평

### 운용설정 탭

운용설정 탭에는 아래 3개만 표시한다.

- 자동매매 ON/OFF
- 최대 투입 금액
- 일 손실률 제한

보조 설명:

- 실제 자본금은 거래소 입금액 기준이다.
- 봇은 거래소 KRW 잔고를 조회하지만, 최대 투입 금액을 넘겨 주문하지 않는다.
- 전략은 사용자가 직접 선택하지 않고 봇이 내부 판단한다.

### 분석근거 탭

분석근거 탭은 기존 v1 개발 방향대로 유지한다.

추가로 아래 내용을 명확히 표시한다.

- 현재 봇이 사용한 내부 신호 목록
- 각 신호의 점수와 방향성
- 시장상태
- 외부요인
- 목표 보유비중
- 현재 보유비중
- 최대 투입 금액 기준 목표 주문 후보
- 주문하지 않은 경우 차단 사유

### 전략관리 탭

전략관리 탭은 최종적으로 제거하거나 읽기 전용 내부 신호 탭으로 전환한다.

읽기 전용으로 유지할 경우 이름은 아래 중 하나를 권장한다.

- 내부신호
- 판단모듈
- 분석모듈

이 화면에서는 사용자가 전략을 켜고 끄지 못한다.

## 리스크 필터 정정

기존 리스크 필터는 유지하되, 아래 조건을 추가한다.

### 최대 투입 금액 초과 차단

봇 포지션 평가금액이 max_total_exposure_krw 이상이면 추가매수를 차단한다.

추가매수 후 예상 포지션 평가금액이 max_total_exposure_krw를 초과하면 주문금액을 줄이거나 차단한다.

### 일 손실률 제한 차단

일 손실 제한 금액은 max_total_exposure_krw와 daily_loss_limit_pct로 계산한다.

오늘 손실 합계가 daily_loss_limit_krw 이상이면 아래를 적용한다.

- 신규 매수 차단
- 추가매수 차단
- 기존 포지션 축소 또는 청산 판단은 허용
- 자동매매 상태는 ON이어도 주문 방향을 제한

### 거래소 잔고 부족 차단

매수 후보가 있어도 실제 KRW 잔고가 부족하면 주문하지 않는다.

이 경우 blockers_json에 KRW 잔고 부족 사유를 저장한다.

## 기존 개발과의 연결 방식

코덱스가 이미 기존 v1 문서를 기준으로 개발 중이므로, 이번 변경은 전면 재작성으로 처리하지 않는다.

적용 순서:

1. 기존 v1 작업 내용 유지
2. 운용자본금 관련 필드, UI, API가 있으면 신규 로직에서 제외
3. 사용자 전략 선택 구조를 신규 스마트 엔진에서는 사용하지 않도록 분리
4. 기존 단일 전략 결과는 legacy_signal로 유지
5. 스마트 엔진은 내부 신호 조합 방식으로 확장
6. 프론트 설정 화면은 자동매매 ON/OFF, 최대 투입 금액, 일 손실률 제한만 노출
7. 기존 전략관리 화면은 제거하지 말고 우선 숨김 또는 읽기 전용 전환
8. 안정화 후 deprecated API와 UI 제거

## 완료 기준

이번 보완 패치의 완료 기준은 아래와 같다.

- 운용자본금 입력 UI가 없다.
- 운용자본금 필드가 신규 주문 계산에 사용되지 않는다.
- 사용자는 자동매매 ON/OFF, 최대 투입 금액, 일 손실률 제한만 변경할 수 있다.
- 전략 선택 UI가 실거래 판단에 영향을 주지 않는다.
- 봇은 내부 신호를 조합해서 목표 보유비중을 계산한다.
- 목표 보유비중은 max_total_exposure_krw 기준으로 금액화된다.
- 실제 매수 주문은 거래소 KRW 잔고와 최대 투입 금액을 모두 넘지 않는다.
- 일 손실 제한은 max_total_exposure_krw 기준으로 계산된다.
- 기존 v1의 Decision Snapshot, Feature Engine, Market Regime Engine, Target Exposure Engine 작업은 유지된다.
- 기존 실거래 안정성을 깨지 않는다.

## 최종 정의

이 봇은 사용자가 전략을 직접 고르는 자동매매 봇이 아니다.

사용자는 자동매매 여부, 최대 투입 금액, 일 손실률 제한만 설정한다.

봇은 거래소 잔고, 현재 포지션, 시장상태, 기술적 지표, 외부요인, 리스크 조건을 종합해서 목표 보유비중을 계산하고, 현재 보유비중과의 차이만큼 매수, 추가매수, 일부매도, 전량청산, 관망을 결정한다.

운용자본금은 사용자가 거래소에 직접 입금하는 영역이므로 봇 설정값으로 관리하지 않는다.
