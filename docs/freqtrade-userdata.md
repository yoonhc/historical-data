# 기존 ft_userdata에서 고정 데이터 사용

실행 대상은 `/home/yoonhc/freqtrade/ft_userdata`다. 이 디렉터리의 기존
`docker-compose.yml`과 `user_data`를 사용하고, 이 저장소의 Compose overlay로
스냅샷 출력과 전략·백테스트 설정을 연결한다. 별도 설치나 Python 환경에 Freqtrade 설치가 필요 없다.

[실제 로더·백테스트·lookahead 검증 결과](../reports/baseline/freqtrade-userdata-2026-09-22/summary.md)

```text
Binance → 독립 수집기 → 고정 스냅샷 → Freqtrade 어댑터 출력
                                      ↓ read-only mount
                         ft_userdata의 공식 Docker 이미지
                                      ↓
                         user_data/backtest_results/marketdata/<run_id>
```

이미지는 검증한 Freqtrade 2026.8 digest로 고정한다. 기존 `user_data/config.json`은
실행에 합치지 않고, API 키가 빈 `integrations/freqtrade/config.futures.json` 또는
`config.spot.json`을 명시한다. 선물은 USDT 무기한·격리 마진, BTC 한 종목, 5분봉이다.
Compose의 기존 거래 명령 대신 일회성 `run --rm --no-deps ... backtesting`을 실행한다.
결과 디렉터리는 실행마다 새로 만들고 기존 봇·전략·거래 DB·설정은 수정하지 않는다.

## 데이터 출력

프로젝트 루트에서 실행한다. 아래 출력은 이미 생성한 고정 스냅샷을 사용한다.
원천 파일 전체 재수집이나 현재 활성 revision으로의 교체는 하지 않는다.

```bash
.venv/bin/python scripts/build_freqtrade_exports.py
```

| `--dataset` | 출력 기간 UTC `[start, end)` | 시장 |
|---|---|---|
| `spot-3y` | 2023-09-22 00:00 ~ 2026-09-22 00:00 | 현물 |
| `futures-before-gap` | 2023-09-22 00:00 ~ 2024-08-12 10:00 | 선물 |
| `futures-after-gap` | 2024-08-12 11:00 ~ 2026-09-22 00:00 | 선물 |
| `oi-longest` | 2025-08-29 07:00 ~ 2026-09-22 00:00 | 현물·선물 + archive OI |

정의는 [`datasets.json`](../integrations/freqtrade/datasets.json)에 있다.
출력은 `data/exports/freqtrade/research-v3/<dataset>/`에 생성한다.
현물·선물 OHLCV는 1m·5m·15m·1h, 마크·지수는 1h, 펀딩은 실제 정산 이벤트다.

선물 원천의 2024-08-12 10:02~10:04 누락을 피하면서 완전한 시간봉을 만들기 위해
앞 구간 끝에서 2분, 뒤 구간 시작에서 56분을 추가로 제외했다.
OI 구간은 원래 06:35부터이며 시간봉 경계까지 25분을 제외했다.
원래 스냅샷의 범위와 실제 출력 범위를 manifest에 모두 기록하며, 누락을 채우거나
선물 두 구간을 하나의 연속 백테스트로 이어 붙이지 않는다.

새 `export-window`는 UTC 하루씩 검증·변환하고 출력에 반영한다. 수집 writer 잠금을
잡지 않으므로 자동 수집과 함께 실행할 수 있다. 중간 실패는 임시 출력만 정리하며,
완료한 출력은 해시를 검사해 재사용한다. 기존 v2 내보내기와 7일 검증 출력도 유지한다.

## 검사와 백테스트

`--execute`가 없으면 대상과 명령만 확인한다. 기본 데이터는 `futures-after-gap`,
기본 시장은 `futures`다. 아래 명령은 모두 기존 `ft_userdata`를 대상으로 한다.

```bash
# 모든 native 파일을 실제 Freqtrade 로더로 읽고 값·시각·행 수 비교
.venv/bin/python scripts/freqtrade_run.py check --execute

# 연결 검증용 SMA 전략, 2025-09-01 이상 2025-09-15 미만
.venv/bin/python scripts/freqtrade_run.py backtest --execute

# 미래 데이터 참조 검사: 최소 10개, 목표 20개 거래의 신호 분석
.venv/bin/python scripts/freqtrade_run.py lookahead --execute

# 현물 / 누락 이전 선물 / OI 포함 출력도 선택 가능
.venv/bin/python scripts/freqtrade_run.py backtest --dataset spot-3y --mode spot --execute
.venv/bin/python scripts/freqtrade_run.py backtest --dataset futures-before-gap --execute
.venv/bin/python scripts/freqtrade_run.py check --dataset oi-longest --execute

# 기존 user_data에 둔 자신의 전략을 사용하기 전 명령 확인
.venv/bin/python scripts/freqtrade_run.py backtest \
  --strategy MyStrategy \
  --strategy-path /home/yoonhc/freqtrade/ft_userdata/user_data/strategies \
  --timerange 20250901-20260301
```

백테스트는 기본적으로 편도 수수료 선물 0.0005, 현물 0.001을 적용한다.
Freqtrade 2026.8은 종료 시각에 있는 봉도 포함하므로 실행 시 종료 경계를 1초 앞당긴
Unix timestamp 범위로 변환해 이 프로젝트의 `[start, end)` 계약을 유지한다.
이는 비교용 가정이며 실제 계정 수수료가 아니다. `--fee`로 지정할 수 있다.
지갑 1,000 USDT, 주문당 100 USDT, 동시 포지션 1개이며 기본 검증 전략은 롱만 사용한다.
기본 전략의 준비 봉 수는 20개다. 최소 하루의 시작 전 자료를 요구하며,
더 긴 준비 기간이 필요한 전략은 `--warmup-days`를 늘린다. 결과 ZIP의 실제 시작·종료가
요청 기간과 다르면 성공으로 보고하지 않는다.

일반 실행 기록은 이 저장소의 `reports/freqtrade-runs/<run_id>/`에 남는다.
명령, 수수료, 기간, 스냅샷·출력 ID, 이미지 digest, Compose·설정·전략 파일 해시,
실제 결과 ZIP 해시를 기록한다. Docker 로그와 명령의 종료 코드도 보관한다.
실행 전 Compose를 해석해 실제 `user_data` 경로와 데이터의 읽기 전용 연결을 확인한다.
백테스트는 공개 거래소 메타데이터를 조회할 수 있으므로 네트워크 연결이 필요하다.

전략 코드는 `/strategies`에서 읽으며 매매 정책은 전략에서 정의한다.
전략별 추가 Python 의존성은 공식 이미지에 자동 설치되지 않는다.
이미지를 바꾸거나 Freqtrade를 업그레이드하면 loader 검증부터 다시 수행해야 한다.

## 연구에서의 사용 범위

`DataSmokeStrategy`는 데이터 연결 확인용이다. 거래 수는 검증 근거이며 수익성 검증이 아니다.
Lookahead 검사는 선택한 구간과 검사한 신호의 범위에 한정된다.
이 검사는 Freqtrade가 시장가 주문과 큰 가상 지갑 등을 강제로 적용하므로 일반 백테스트와
조건이 다르다. `config.lookahead.json`을 추가 적용해 시장가와 호환되는 가격 설정을 사용한다.
종료 코드 0만으로 성공을 판단하지 않고 실제 CSV의 편향 여부와 검사 신호 수를 확인한다.

OHLCV 외 거래대금·taker 매수량·펀딩 원본·OI는 `features/*.parquet`에 있다.
Freqtrade가 이를 자동으로 전략 데이터프레임에 넣지는 않는다.
OI 결합은 [명시적 시각 가정과 지연을 사용하는 예제](../integrations/freqtrade/README.md#전략에서-추가-지표-읽기)를 따른다.
`oi-longest`는 archive OI이며, 공개 시각·시간 이동·단위가 검증되었다는 뜻이 아니다.
기본 검증 전략은 OI를 사용하지 않는다.

전략 연구를 시작할 때 개발·검증·최종 평가 기간을 먼저 고정하고, 같은 기간·수수료로
가격만 사용하는 전략과 OI를 추가한 전략을 비교한다. 초기 연결 검증에 사용한 구간을
아무도 보지 않은 최종 평가 구간으로 취급하지 않는다. 현재 출력은 과거 연구용 고정
데이터이며 dry-run/live에 지속적으로 새 피처를 공급하는 연결은 별도 작업이다.

참고: [공식 백테스트](https://www.freqtrade.io/en/stable/backtesting/),
[공식 lookahead 분석](https://www.freqtrade.io/en/stable/lookahead-analysis/),
[Feather V2와 Arrow IPC](https://arrow.apache.org/docs/python/feather.html).
