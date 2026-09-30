# Historical Market Data

BTCUSDT 현물·USDT 무기한 선물의 공개 시장 데이터를 수집·검증·보관하는 독립 데이터 프로젝트입니다.
Freqtrade와 연구 노트북은 이 프로젝트가 제공하는 데이터를 읽는 소비자로 둡니다.

첫 구현과 실제 데이터 검증을 마쳤습니다. 수집 코어에는 Freqtrade 의존성이 없고,
별도 어댑터가 고정 스냅샷을 Feather/Parquet으로 내보냅니다. Freqtrade는 공식 Docker 이미지에서 실행합니다.

[2026-09-22 진행 현황 정리](docs/milestones/2026-09-22-data-platform.md)에 주요 결정,
완료 범위·검증 근거·남은 한계와 다음 우선순위를 기록했습니다. 해당 날짜의 기준 기록이며
현재 운영 상태는 `marketdata status --table`로 확인합니다.

이후 백업·복원, aggTrade·depth, 상시 운영·무기한 보관, 과거 데이터 확장을 묶은
[2026-09-23 후속 작업 정리](docs/milestones/2026-09-23-streams-and-history.md)도 제공합니다.

그 뒤의 두 번째 SSD 증분 백업, 호가 복구 보강, 유지보수 잠금 대기와 실제 예약 검증은
[2026-09-27 수집·백업 운영 보강 기록](docs/milestones/2026-09-27-collection-and-backup-stabilization.md)에
정리했습니다. restic 시험 전의 구현 상태·검증 범위·남은 한계와 후속 순서를 기록합니다.

## 현재 확보한 데이터

2019년 이후 원천이 제공하는 범위까지 확장했습니다. 아래 표는 UTC **2026-09-22 00:00 미만**의
고정 검증 결과입니다. 종료일 뒤의 자동 수집은 별도이며 현재 상태는 `status`로 확인합니다.

| 데이터 | 시작 시각 (UTC) | 확보 행 수 | 남은 누락·격리 |
|---|---|---:|---|
| 현물 거래 1분봉 | 2019-01-01 00:00 | 4,058,129 | 4,111분; 짧은 원천 캔들 9개 표시 |
| 선물 거래 1분봉 | 2019-09-08 17:57 | 3,701,162 | 1분 |
| 마크가격 1분봉 | 2019-12-23 11:58 | 3,548,827 | 55분 |
| 지수가격 1분봉 | 2019-12-23 11:58 | 3,548,822 | 60분 |
| 확정 펀딩 이벤트 | 2019-09-10 08:00 | 7,706 | 원천 조회 범위 완료 |
| OI 5분 통계 (archive) | 2020-09-01 00:00 | 629,510 | 7,546개 라벨 누락; 격리 24일 포함 |

누락을 합성하지 않았고 아카이브·REST 보완 조회와 원문 형식의 예외를 기록했습니다.
시작 시각 격자가 채워져도 모든 캔들의 길이가 60초라는 보장은 아닙니다.
[수집·재개·품질 규칙](docs/history-extension.md)과
[확장 검증 결과](reports/baseline/history-extension-2026-09-23/summary.md)를 참고하세요.
REST OI는 최초 최근 28일을 확보한 뒤 5분마다 수집하고, 상품 메타데이터는 현재 관측을 기록합니다.

기존 **2023-09-22 ≤ UTC < 2026-09-22** 연구 구간의 연속 범위는 **19개 스냅샷**으로
보존했습니다. 이 구간의 거래봉·펀딩 통계 연구는 3년 전체,
마크·지수가 필요한 연구는 남은 2분을 제외한 두 구간을 사용합니다.
[데이터별 품질·연구 구간·스냅샷 보고서](reports/baseline/research-2023_2026/summary.md)

기존 OI 실험의 916일 파일도 체크섬을 검증해 재사용했습니다. 전체 보관 이력과 위 연구 구간의
격리 날짜 수는 다를 수 있으므로 `status`와 해당 기간의 `coverage`를 확인하세요.
아카이브 OI와 REST OI는 별도 시계열이며 시각·단위를 검증한 것처럼 합치지 않습니다.

현재 머신에서는 일별 갱신과 5분 OI 수집 타이머를 활성화했습니다. 현재 수집 상태는 `status`로
확인합니다. BTCUSDT 선물 aggTrade는 짧은 REST 구간과 WebSocket 표본 수집·재접속 복구를
검증했습니다. [aggTrade 사용법·범위·검증](docs/aggtrades.md)을 확인하세요.
선물 호가 depth의 스냅샷·증분 수집, 재동기화와 저장 자료 재생도 추가했습니다.
[호가 depth 사용법·검증 범위](docs/orderbook-depth.md)를 확인하세요.
두 스트림을 함께 실행해 OI 수집과의 공존·재접속·자원 사용을 확인하는
[최대 1시간 검증 절차](docs/stream-soak.md)도 제공합니다.
[1시간 동시 수집 결과](reports/baseline/stream-soak-2026-09-23/summary.md)에는 호가 범위 소진
문제의 수정, 90,009건의 체결 감사와 35,349행의 호가 재생·대조 결과를 기록했습니다.
두 스트림은 [1시간 세션을 반복하는 상시 서비스](docs/stream-services.md)로 운영합니다.
2026-09-23 과거 확장 중 REST 호가 스냅샷 지연이 관측됐습니다. 지연 중에도 증분을 보존하도록
보완했으며, 미복원 구간은 불완전으로 표시합니다. 증분 보존과 호가 복원 상태는 구분합니다.
현물 스트림과 장기 aggTrades 아카이브는 후속 확장 항목입니다.
종료된 스트림 파일을 원문 바이트와 ID를 보존한 채 묶는 기능도 제공합니다.
[파일 묶음 저장·무기한 보관 정책](docs/file-packing-and-retention.md)을 확인하세요.

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

# 첫 연동 검증에 사용한 7일 수집 명령. REST OI는 최근 제공 기간에만 재조회 가능합니다.
.venv/bin/marketdata backfill-set --config configs/bootstrap.toml \
  --start 2026-09-14 --end 2026-09-21 --report reports/acquisition.json

# 3년 과거 수집·재개: 월별 ZIP 우선, 다운로드 밖에서 하루 단위로 게시합니다.
.venv/bin/marketdata backfill-set --config configs/research-archive.toml \
  --start 2023-09-22 --end 2026-09-22 --report reports/research-backfill-2023_2026.json

# 월별 품질·연속 연구 구간 보고서와 완전한 구간의 고정 스냅샷
.venv/bin/python scripts/build_research_dataset.py --snapshots

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

현재 대상은 기존 **`/home/yoonhc/freqtrade/ft_userdata`** Docker 설치입니다.
3년 스냅샷 출력과 실제 실행은 다음 명령을 사용합니다.

```bash
.venv/bin/python scripts/build_freqtrade_exports.py
.venv/bin/python scripts/freqtrade_run.py check --execute
.venv/bin/python scripts/freqtrade_run.py backtest --execute
```

기본값은 선물 누락 이후 구간이며, 설정·전략은 읽기 전용으로 연결하고 결과는
기존 `ft_userdata/user_data/backtest_results/marketdata/`에 저장합니다.
[데이터 구간·자신의 전략 실행·검증 기록 안내](docs/freqtrade-userdata.md)

개별 스냅샷의 기존 v2 출력과 최초 7일 검증을 재현하려면 아래 명령을 사용합니다.

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
3년 출력은 `data/exports/freqtrade/research-v3/`, 최초 7일 출력은 `smoke-v2/`입니다.
일반 캔들은 `date/open/high/low/close/volume`, 펀딩은 `date/funding_rate`입니다.
마크·지수는 호환 출력에만 volume=0을 사용합니다. 공통 원본에는 허구의 거래량을 넣지 않습니다.
거래대금·taker 매수량·OI는 `features/*.parquet`에 보존되며 전략에서 명시적으로 읽어야 합니다.

- [Docker 실행·백테스트 및 OI 결합 예제](integrations/freqtrade/README.md)
- [3년 과거 수집·연구 구간·스냅샷 사용법](docs/research-dataset.md)
- [설계와 구현 범위](docs/data-platform-design.md)
- [기존 OI 실험 검토 및 재검증](docs/legacy-oi-review.md)
- [7일 검증·OI 시각 비교 결과](reports/baseline/v0.1.0/smoke.json)
- [실제 Freqtrade 로더 검증](reports/baseline/v0.1.0/freqtrade-loader.json)
- [현물·선물 백테스트 검증](reports/baseline/v0.1.0/freqtrade-backtests.json)
- [검증 기록과 데이터 백업의 범위](reports/README.md)

## 갱신과 운영

OI는 15초 간격의 별도 관측기로 HTTP 응답과 수신 시각을 기록하고, 매일 최근 7일의
archive/REST 값 비교와 응답 변화 보고서를 생성합니다.
[OI 품질·조회 시각 검증과 한계](docs/oi-quality.md)

```bash
.venv/bin/marketdata oi-quality --days 7
```

```bash
# 수집 범위의 누락·격리·최신 시각·마지막 실행 결과
.venv/bin/marketdata status --table
.venv/bin/marketdata status --report reports/status.json

# 저장 용량과 파일 수 점검
.venv/bin/marketdata storage --report reports/storage.json

# 일별 갱신: 등록된 범위의 누락을 복구하고, 최근 3일을 재확인합니다.
.venv/bin/marketdata update --config configs/daily.toml

# REST OI: 최초 28일 확보, 이후 중간 누락 복구와 최근 한 시간 재확인
.venv/bin/marketdata update --config configs/oi-live.toml

# 터미널에서 5분마다 수집. 종료는 Ctrl-C.
.venv/bin/marketdata watch --config configs/oi-live.toml --sleep-seconds 300
```

일별 운영 범위는 현재 `2026-09-14`부터이며, REST OI의 최초 시작점도 카탈로그에 보존합니다.
새 데이터가 도착해도 이전 누락을 잊지 않습니다. 오래 중단했다면 최근 자료를 먼저 유지하면서
데이터셋별 실행당 최대 32일씩 누락을 복구합니다. [운영 정책·장애 복구](docs/operations.md)

REST OI는 보수적으로 최근 29일 안에서만 복구하며, 그보다 오래된 누락은 만료 구간으로 표시합니다.
archive OI는 별도 시계열로 유지합니다. 단일 저장소는 쓰기 프로세스 하나만 허용하며,
타이머 작업이 겹쳐 잠금을 얻지 못하면 systemd가 재시도합니다.

[`integrations/systemd/`](integrations/systemd/README.md)에는 이 머신에 연결한 사용자 서비스·타이머를 두었습니다.
다른 위치에서 사용할 때는 서비스 파일의 절대 경로를 먼저 수정하세요.
현재는 여섯 타이머·두 스트림 서비스와 `yoonhc`의 linger가 활성화되어 로그아웃 후에도 실행됩니다.
일별 작업은 한국 시각 12:20 이후 약 5분 이내, REST OI는 매 5분 경계의 45초 이후에 실행합니다.
새 머신에 설치하려면 사용자 systemd에 다음과 같이 연결합니다.

```bash
systemctl --user link "$PWD"/integrations/systemd/marketdata-*.service \
  "$PWD"/integrations/systemd/marketdata-*.timer
systemctl --user daemon-reload
systemctl --user enable --now marketdata-daily.timer marketdata-oi.timer \
  marketdata-oi-probe.timer marketdata-oi-quality.timer \
  marketdata-pack.timer marketdata-backup.timer \
  marketdata-aggtrade.service marketdata-depth.service
systemctl --user list-timers 'marketdata-*'
loginctl enable-linger "$USER"
```

저장소는 `data/raw` 원본, `data/canonical` 불변 Parquet, `data/snapshots` manifest와
`state/catalog.sqlite`로 구성됩니다. 원본과 수정 이력은 자동 삭제하지 않습니다.
백업은 수집기를 멈춘 상태에서 `data/`와 `state/`를 함께 복사하거나 SQLite 백업 API를 사용합니다.
수집 중에도 일관된 복구 지점을 만드는 `marketdata backup`, `backup-verify`, `restore` 명령을
제공합니다. 자동 백업은 두 번째 SSD에 보관하고, 이전 사본의 동일 파일을 재사용해 반복 저장을
줄입니다. 원본과 복원본은 독립 사본입니다. 현재 사본 삭제·기간 제한을 적용하지 않으며,
원본·revision 보존과 날짜별 백업 복구 지점의 최종 보관 정책은 별도로 다룹니다.
[백업 범위·증분 저장·별도 경로 복원·용량 점검](docs/backup-and-storage.md)을 확인하세요.
하드링크 백업과 restic의 용량·복구 비교, VM 이전을 위한 호스트 설정 분리는
[restic 독립 시험 절차](docs/restic-trial.md)에 정리했습니다.
후속 [restic 일일 백업 연결 설계](docs/restic-operations-plan.md)는 완료된 사본의 별도 보관,
중단 후 재개·성공 판정·예약 관찰 기준을 정의합니다.
[병행 보관 도구 실행 안내](docs/restic-archive.md)에 구현 명령과 설정·시각·복원 계약을 정리했습니다.
9/27 별도 타이머를 적용했습니다. [예약 운영·날짜별 관찰·새 예약만 중지하는 절차](docs/restic-scheduled-operations.md)를
확인하세요. 새 날짜 3개와 새 사본 복원은 실제 예약 기록으로 별도 관찰합니다.
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
