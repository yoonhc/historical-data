# Freqtrade Docker 연동

Compose는 공식 Freqtrade 2026.8 이미지의 digest를 고정한다.
수집 코어·어댑터에는 Freqtrade를 설치하지 않는다. 출력 데이터는 컨테이너에 읽기 전용으로 연결한다.
아래 명령은 **프로젝트 루트**에서 실행한다.

## 출력과 실제 로더 검사

```bash
mkdir -p integrations/freqtrade/user_data/backtest_results
.venv/bin/python scripts/build_smoke_export.py
.venv/bin/marketdata-freqtrade verify data/exports/freqtrade/smoke-v2

docker compose -f integrations/freqtrade/compose.yaml run --rm \
  --entrypoint python freqtrade /checks/check_export.py /marketdata
```

로더 검사 자체는 네트워크가 필요 없다. 현재 검증한 원본 구간은
2026-09-14 이상, 2026-09-21 미만 UTC다. 파일별 행 수·가격·거래량·시각을 실제 로드 결과와 비교하고
5분봉에서 Freqtrade 전략 지표/진입/청산 함수까지 실행한다.

다른 출력은 `MARKETDATA_EXPORT`에 절대 경로를 설정해 연결할 수 있다.
그 출력 manifest의 대상 Freqtrade 버전과 컨테이너 버전은 일치해야 한다.

## 백테스트

9월 14일 자료는 지표 준비에 쓰고 9월 15일부터 20일까지 검사한다. 전략 준비 봉 수는 20개다.
`DataSmokeStrategy`는 연결 검증용 이동평균 전략이며 연구 결과로 추천하는 전략이 아니다.
설정의 API 키는 비어 있다. 백테스트는 공개 거래소 메타데이터를 조회할 수 있다.

```bash
docker compose -f integrations/freqtrade/compose.yaml run --rm freqtrade backtesting \
  --config /configs/spot.json --datadir /marketdata/data \
  --strategy-path /strategies --strategy DataSmokeStrategy \
  --timerange 20260915-20260921 --cache none --fee 0.001 \
  --export trades --backtest-directory /freqtrade/user_data/backtest_results \
  --notes marketdata-spot-smoke

docker compose -f integrations/freqtrade/compose.yaml run --rm freqtrade backtesting \
  --config /configs/futures.json --datadir /marketdata/data \
  --strategy-path /strategies --strategy DataSmokeStrategy \
  --timerange 20260915-20260921 --cache none --fee 0.0005 \
  --export trades --backtest-directory /freqtrade/user_data/backtest_results \
  --notes marketdata-futures-smoke
```

결과는 `integrations/freqtrade/user_data/backtest_results/`에 저장한다.
마지막 5분봉의 시작 시각은 20일 23:55이므로 Freqtrade가 종료 시각 안내를 낼 수 있다.
원천의 전체 `[14일 00:00, 21일 00:00)` 분봉 격자는 별도 coverage 검사에서 확인한다.
실행 종료 코드만으로 성공을 판정하지 않고 실제 결과 ZIP과 거래 기록도 확인한다.

선물 펀딩 파일의 `1h`는 Freqtrade 로더 규약이다. 펀딩을 매시간 생성하지 않는다.
정산 시각이 시간 경계보다 1초 미만 늦은 경우에만 native 출력에서 시간 경계로 내린다.
시간 중간의 이벤트나 경계 보정으로 충돌하는 이벤트는 표현 불가능하므로 export를 거부한다.
정확한 원천 시각은 core와 `features/funding_events-linear_perpetual-primary.parquet`에 보존한다.

## 전략에서 추가 지표 읽기

기본 Freqtrade 캔들에는 OI·quote volume·taker volume이 자동 추가되지 않는다.
추가 데이터는 export의 `features/`에 있고, Compose가 제공하는 Python 경로로 어댑터 함수를 import할 수 있다.

다음은 `populate_indicators()`에서 REST OI를 결합하는 예다. `date`가 봉 시작이므로
5분봉의 의사결정 시각은 `date + 5분`으로 둔다. 실제 연구에서는 데이터 로딩을 캐시하면 된다.

```python
import pandas as pd
import pyarrow.parquet as pq
from marketdata_freqtrade.features import align_open_interest

oi_rows = pq.read_table(
    "/marketdata/features/open_interest-linear_perpetual-rest.parquet"
).to_pylist()
decision_times = (dataframe["date"] + pd.Timedelta(minutes=5)).dt.to_pydatetime()
matched = align_open_interest(
    oi_rows,
    decision_times,
    mode="historical_assumption",
    publication_lag_seconds=60,  # 연구 가정. 원천 공개 지연을 측정한 값은 아님.
    max_age_seconds=600,
)
dataframe["oi_quantity"] = [float(r["quantity"]) if r else float("nan") for r in matched]
```

archive 시계열에는 `archive_shift_seconds`도 명시해야 한다.
0초 또는 300초 등 선택한 값은 검증된 보편 규칙이 아니라 해당 연구의 가정이다.
향후 값으로 결측을 채우지 않으며 오래된 값은 null로 남긴다. 수량·가치 단위는 아직 미확정이다.

거래 추가 필드는 `features/trade_bars-<market>-<timeframe>.parquet`에서 읽을 수 있다.
그 파일의 `timestamp`는 봉 시작, `decision_time`은 봉 종료다.
live/dry-run 전략에 쓰려면 해당 시점까지 수집된 최신 피처를 공급하는 별도 연결이 필요하다.
