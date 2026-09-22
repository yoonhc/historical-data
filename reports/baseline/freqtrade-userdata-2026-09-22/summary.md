# ft_userdata 연결 검증 — 2026-09-22

대상은 `/home/yoonhc/freqtrade/ft_userdata`의 기존 Compose와 `user_data`다.
공식 Freqtrade 2026.8 이미지 digest를 고정한 overlay로 실행했으며, 기존 Compose 파일과
`user_data/config.json`의 SHA-256이 작업 전후 동일함을 확인했다.

## 검증 결과

- 단위·회귀 테스트 **78개 통과**, Ruff 검사·형식 검사·diff 검사 통과.
- 고정 출력 **4개 / 파일 53개** 생성 및 해시 검사.
- 실제 Freqtrade loader로 native **29개 파일**의 전체 값·시각·행 수가 출력과 일치함을 확인.
- 각 5분봉 데이터의 전략 지표·진입·청산 함수도 실행.
- OI 추가 파일 **111,948행**의 모든 열이 부모 스냅샷의 해당 구간과 정확히 일치.
- 아래 네 백테스트는 요청 기간 전체와 실제 결과 ZIP·거래 기록까지 검사.
- 선물 lookahead 검사 **20개 신호**에서 편향 진입·청산 0개, 편향 지표 없음.
- 가격만 사용하는 전략의 거래 기록이 `futures-after-gap`과 `oi-longest` 출력에서 동일.
- 기존 7일 v2 출력의 22개 파일과 export ID가 그대로 유지됨을 확인.

| 출력 | 시장 | 백테스트 기간 UTC `[start, end)` | 거래 수 | 펀딩 반영 거래 |
|---|---|---|---:|---:|
| spot-3y | 현물 | 2025-09-01 ~ 2025-09-15 | 155 | 0 |
| futures-before-gap | 선물 | 2023-09-23 ~ 2023-10-07 | 189 | 24 |
| futures-after-gap | 선물 | 2025-09-01 ~ 2025-09-15 | 158 | 24 |
| oi-longest | 선물 | 2025-09-01 ~ 2025-09-15 | 158 | 24 |

전략은 연결 검증용 `DataSmokeStrategy`다. 수수료는 편도 현물 0.001, 선물 0.0005,
기본 백테스트 지갑 1,000 USDT, 주문당 100 USDT다. 실제 계정 조건이나 수익성의 검증이 아니다.
Lookahead 분석은 Freqtrade가 시장가·가상 지갑 등의 조건을 별도로 바꾸며,
검사한 20개 신호에 대한 결과다. 처음 두 분석 실행은 설정 오류로 CSV가 생성되지 않아
실패로 처리했고, 시장가 가격 방향을 보정한 분석 전용 설정에서 성공한 결과만 기준 기록에 넣었다.
실패 실행 로그는 일반 `reports/freqtrade-runs/`에 보존했다.

전체 데이터 로더 검증과 위 2주 백테스트를 구분한다. 3년 전체의 전략 성과를 평가한 것이 아니다.
OI 파일을 보존·전달했지만 이 전략은 OI를 사용하지 않으며 공개 시각·단위의 미확정 상태도 유지된다.

## 데이터와 근거

출력은 `data/exports/freqtrade/research-v3/<dataset>/`에 있다. 정확한 범위·부모 스냅샷·행 수·
생성 시간·최대 RSS는 [exports.json](exports.json)에 기록했다. 최대 RSS는 약 231 MiB였다.
현물은 3년 전체, 선물은 원천의 2분 누락을 피한 두 구간, OI는 가장 긴 연속 구간이다.
시간봉 경계 때문에 제외한 가장자리 범위는 [사용법](../../../docs/freqtrade-userdata.md)에 설명했다.

- [validation.json](validation.json): 검사 결과, 소스 해시, 실제 결과 ZIP/CSV 해시, 기존 설정 보존 확인.
- `export-*.json`: 출력별 전체 manifest와 원본·출력 범위.
- `runs/*.json`: 실행 당시 대상, 명령, 이미지, 설정·전략·출력 해시와 검사 결과.

각 run의 파일 해시는 그 실행 시점의 값이다. 최종 소스 해시는 `validation.json`에 별도로 고정했다.
설정 보정 전의 로더·백테스트 기록도 당시 명령과 해시를 유지한다.
시장 데이터와 결과 ZIP/CSV는 Git에 넣지 않으며 결과는 기존
`ft_userdata/user_data/backtest_results/marketdata/<run_id>/`에 보관한다.
