# Historical Market Data

BTCUSDT 현물·USDT 무기한 선물의 공개 시장 데이터를 수집·검증·보관하는 독립 데이터 프로젝트입니다.
Freqtrade와 연구 노트북은 이 프로젝트가 제공하는 데이터를 읽는 소비자로 둡니다.

첫 구현과 실제 데이터 검증을 마쳤습니다. 수집 코어에는 Freqtrade 의존성이 없고,
별도 어댑터가 고정 스냅샷을 Feather/Parquet으로 내보냅니다. Freqtrade는 공식 Docker 이미지에서 실행합니다.

## 현재 확보한 데이터

고정 검증 구간은 **2026-09-14 00:00 ≤ UTC < 2026-09-21 00:00**입니다.

| 데이터 | 시장 | 검증 구간 행 수 |
|---|---|---:|
| 거래 1분봉: OHLC, base/quote 거래량, 체결 수, taker 매수량 | 현물·선물 각각 | 각각 10,080 |
| 마크가격·지수가격 1분봉 | 선물 각각 | 각각 10,080 |
| 확정 펀딩 정산 이벤트 | 선물 | 21 |
| OI 5분 통계 | 아카이브·REST 각각 | 각각 2,016 |
| 상품 메타데이터 | 현물·선물 | 최초 수집 당시 각 1회 관측 |

기존 OI 실험의 916일 파일도 체크섬을 검증해 가져왔습니다. 20일은 0값으로 격리했고,
나머지에도 누락이 있을 수 있으므로 `coverage`를 확인해야 합니다. 날짜 전체가 연속이라는 뜻은 아닙니다.
아카이브 OI와 REST OI는 별도 시계열이며 시각·단위를 검증한 것처럼 합치지 않습니다.

3년 전체 신규 다운로드, aggTrades, 호가 스트림, 주문 실행은 이번 구현에 포함하지 않았습니다.
아래 명령으로 기간을 넓혀 수집할 수 있습니다. 정기 실행용 타이머 파일은 준비했으며 아직 활성화하지 않았습니다.

## 설치와 실행

Python 3.12 이상과 uv를 사용합니다. 작업 디렉터리는 이 프로젝트 루트입니다.

```bash
uv --cache-dir .cache/uv sync --all-packages --frozen
.venv/bin/pytest -q
```

`marketdata`와 `marketdata-freqtrade`는 별도 배포 가능한 패키지입니다.
수집기만 설치할 때는 `pip install ./packages/marketdata`를 사용하면 됩니다.
어댑터도 Freqtrade 자체를 설치하지 않습니다.

모든 기간은 UTC의 `[start, end)`입니다. 타임존 없는 날짜·시각도 UTC로 해석합니다.
아카이브 수집은 완료된 UTC 날짜 단위이며, 캔들 누락을 보간하지 않습니다.

```bash
# 다운로드 단위와 URL 확인
.venv/bin/marketdata plan --dataset trade_bars --market spot \
  --start 2026-09-14 --end 2026-09-21

# 기본 7개 데이터셋 수집. 완전한 아카이브 구간은 건너뜁니다.
.venv/bin/marketdata backfill-set --config configs/bootstrap.toml \
  --start 2026-09-14 --end 2026-09-21 --report reports/acquisition.json

# 개별 자료 수집: 긴 과거는 월별 ZIP 우선, OI archive는 일별 ZIP
.venv/bin/marketdata backfill --dataset trade_bars --market spot \
  --start 2023-09-21 --end 2026-09-21

# 완전성 및 파일 해시 검사
.venv/bin/marketdata coverage --dataset open_interest --series archive \
  --start 2026-09-14 --end 2026-09-21
.venv/bin/marketdata validate
```

정상 완료는 종료 코드 0, 실행 오류는 1, 누락·격리·미게시 자료 등 불완전한 결과는 2입니다.
펀딩의 `query_complete`는 해당 범위의 원천 조회 완료이며, 이벤트가 모두 존재한다는 증명은 아닙니다.
기존 값과 다른 수정본은 원본을 보존하고 충돌로 처리합니다. 검토 후 같은 `repair` 명령에
`--accept-revisions`를 지정하면 새 revision을 채택하며 이전 스냅샷은 유지됩니다.
OI의 0값을 포함한 날짜를 해제하려면 완전하고 유효한 하루 원본이 필요합니다.

## Freqtrade에서 사용

```bash
# 저장된 자료로 고정 스냅샷 생성; 출력의 snapshot_id를 사용합니다.
.venv/bin/marketdata snapshot --config configs/bootstrap.toml \
  --start 2026-09-14 --end 2026-09-21

.venv/bin/marketdata-freqtrade export --snapshot SNAPSHOT_ID \
  --output data/exports/freqtrade/my-research --timeframes 1m 5m 15m 1h

# 이 저장소에서 검증한 7일 스냅샷·출력을 재현
.venv/bin/python scripts/build_smoke_export.py
```

대상은 **Freqtrade 2026.8**이며 공식 이미지 digest를 Compose에 고정했습니다.
현재 검증 출력은 `data/exports/freqtrade/smoke-v2/`입니다.
일반 캔들은 `date/open/high/low/close/volume`, 펀딩은 `date/funding_rate`입니다.
마크·지수는 호환 출력에만 volume=0을 사용합니다. 공통 원본에는 허구의 거래량을 넣지 않습니다.
거래대금·taker 매수량·OI는 `features/*.parquet`에 보존되며 전략에서 명시적으로 읽어야 합니다.

- [Docker 실행·백테스트 및 OI 결합 예제](integrations/freqtrade/README.md)
- [설계와 구현 범위](docs/data-platform-design.md)
- [기존 OI 실험 검토 및 재검증](docs/legacy-oi-review.md)
- [7일 검증·OI 시각 비교 결과](reports/baseline/v0.1.0/smoke.json)
- [실제 Freqtrade 로더 검증](reports/baseline/v0.1.0/freqtrade-loader.json)
- [현물·선물 백테스트 검증](reports/baseline/v0.1.0/freqtrade-backtests.json)
- [검증 기록과 데이터 백업의 범위](reports/README.md)

## 갱신과 운영

```bash
# 일별 갱신: 완료된 최근 3일을 재확인하고 현재 메타데이터를 관측합니다.
.venv/bin/marketdata update --config configs/daily.toml

# REST OI: 최초 실행은 최근 하루, 이후 마지막 기록보다 한 시간 전부터 겹쳐 수집
.venv/bin/marketdata update --config configs/oi-live.toml

# 터미널에서 5분마다 수집. 종료는 Ctrl-C.
.venv/bin/marketdata watch --config configs/oi-live.toml --sleep-seconds 300
```

장기 중단 후 REST OI 재개는 보수적으로 최근 29일 안에서만 복구합니다. 더 오래된 자료는
별도 archive 시계열에서 확보합니다. daily 설정을 5분마다 실행할 필요는 없습니다.
단일 저장소는 쓰기 프로세스 하나만 허용하므로 동시 작업이 겹치면 한 작업이 재실행을 기다립니다.

[`integrations/systemd/`](integrations/systemd/README.md)에는 이 머신 경로에 맞춘 사용자 서비스·타이머 예제를 두었습니다.
다른 위치에서 사용할 때는 서비스 파일의 절대 경로를 먼저 수정하세요.
설치하려면 사용자 systemd에 다음과 같이 연결합니다. 사용자 서비스의 로그아웃 후 실행 여부는
머신의 user-manager/linger 설정에 따릅니다.

```bash
systemctl --user link "$PWD"/integrations/systemd/marketdata-*.service \
  "$PWD"/integrations/systemd/marketdata-*.timer
systemctl --user daemon-reload
systemctl --user enable --now marketdata-daily.timer marketdata-oi.timer
systemctl --user list-timers 'marketdata-*'
```

저장소는 `data/raw` 원본, `data/canonical` 불변 Parquet, `data/snapshots` manifest와
`state/catalog.sqlite`로 구성됩니다. 원본과 수정 이력은 자동 삭제하지 않습니다.
백업은 수집기를 멈춘 상태에서 `data/`와 `state/`를 함께 복사하거나 SQLite 백업 API를 사용합니다.
활성 revision을 무시하고 모든 Parquet을 glob으로 읽으면 수정 전후 데이터가 중복됩니다.

## Python 조회

```python
from marketdata.domain import Key
from marketdata.store import Store

store = Store(".")
try:
    key = Key("trade_bars", "spot")
    table = store.read(key, "2026-09-14", "2026-09-21", snapshot_id="SNAPSHOT_ID")
    # pyarrow.Table. Decimal과 UTC 시각을 유지합니다.
    import duckdb
    with duckdb.connect() as db:
        db.register("bars", table)
        print(db.sql("select count(*), sum(base_volume) from bars").fetchall())
finally:
    store.close()
```

쓰기 API를 직접 사용할 때는 `with store.writer():`로 작업 전체를 보호해야 합니다.
`Store.read()`의 최신 버전 조회는 품질 문제를 자동 숨기지 않습니다.
연구·내보내기에는 `coverage` 검사를 통과한 고정 스냅샷을 사용하세요.
