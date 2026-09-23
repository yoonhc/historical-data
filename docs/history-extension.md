# 2019년 이후 과거 자료 확장

기존 3년 연구 스냅샷을 유지하면서 공개 원천이 제공하는 과거 자료를 추가한다.
수집·표준화·검증은 Freqtrade와 독립적이며 원문과 표준 자료에 보관 기한을 두지 않는다.
aggTrade의 대규모 과거 아카이브와 과거 전체 호가 복원은 이번 범위에 포함하지 않는다.

## 실제 확인한 원천 시작점

2026-09-23 공개 파일 목록과 REST 응답을 확인했다. 모든 시각은 UTC다.
아래 날짜는 이번에 확인한 가용 범위이며, 그 이후 자료의 무누락을 뜻하지 않는다.

| 자료 | 이번 수집 시작 | 경로 |
|---|---|---|
| 현물 거래 1분봉 | 2019-01-01 | 월별 아카이브 |
| USD-M 선물 거래 1분봉 | 2019-09-08 17:57 | 2019년 REST, 2020년부터 월별 아카이브 |
| 마크·지수 1분봉 | 2019-12-23 11:58 | 2019년 REST, 2020년부터 월별 아카이브 |
| 확정 펀딩 | 2019-09-10 08:00 | REST |
| archive OI 5분 통계 | 2020-09-01 | 일별 아카이브 |

현물 월별 파일 목록 자체는 2017-08부터다. 선물 거래봉 일별 목록은 2019-12-31부터,
선물 거래봉·마크·지수·펀딩 월별 목록은 2020-01부터 확인했다.
시작일의 상장 전 시간은 제공 범위 밖으로 구분하고 값을 채우지 않는다.
현재 상품 메타데이터를 과거 상품 규칙으로 간주하지 않는다.

OI는 archive와 REST를 별도 시계열로 유지한다. 과거 archive의 단위·라벨 시각·공개 지연은
확정하지 않았으며 0값 격리와 원천 누락을 그대로 남긴다. 파일을 확보했다고 연구 적합성을
승인하는 것은 아니다. [기존 OI 검토](legacy-oi-review.md)를 함께 읽는다.

## 수집 순서와 잠금

실시간 depth의 제한된 스냅샷 범위 소진을 먼저 점검하고, 2020-01 한 달치 시범 수집과
실시간 감시를 병행한 다음 나머지 기간을 확장한다. `backfill-set`은 다운로드·재시도·정규화를
쓰기 잠금 밖에서 수행한다. 원문·작업 기록 저장과 UTC 하루 단위 표준 데이터 게시에만
잠금을 잡고, 일별 게시 사이에는 잠시 양보한다. 중단 후 재실행하면 완전한 날짜는 건너뛴다.

보고서의 `writer_seconds`는 해당 Collector의 누적 잠금 보유 시간,
`writer_max_seconds`는 가장 긴 단일 보유 시간이다. 잠금 대기 시간·HTTP 시간과 다르다.
다른 CLI 명령의 잠금 방식이나 모든 실행의 지연 상한을 보장하는 수치는 아니다.

```bash
.venv/bin/marketdata backfill-set --config configs/history-core.toml \
  --start 2020-01-01 --end 2023-09-22 --report reports/history-core.json
.venv/bin/marketdata backfill-set --config configs/history-spot.toml \
  --start 2019-01-01 --end 2020-01-01 --source archive --report reports/history-spot.json
.venv/bin/marketdata backfill-set --config configs/history-futures.toml \
  --start 2019-09-08 --end 2020-01-01 --source rest --report reports/history-futures.json
.venv/bin/marketdata backfill-set --config configs/history-reference-prices.toml \
  --start 2019-12-23 --end 2020-01-01 --source rest --report reports/history-reference.json
.venv/bin/marketdata backfill-set --config configs/history-funding.toml \
  --start 2019-09-10 --end 2020-01-01 --source rest --report reports/history-funding.json
.venv/bin/marketdata backfill-set --config configs/history-oi.toml \
  --start 2020-09-01 --end 2023-01-01 --report reports/history-oi.json
```

`--source auto`는 기존 원천 선택을 유지한다. `rest`는 월별 아카이브가 없는 기간을
하루 단위로 조회한다. `archive`는 공개 ZIP을 사용한다. HTTP 오류·격리·원천 누락을
보고서에 남기고 부분 완료는 종료 코드 2로 구분한다. 수정본 충돌을 자동 승인하지 않는다.

## 과거 캔들 형식

일부 과거 현물 캔들의 원문 종료 시각은 명목 1분보다 짧다. 예를 들어 2020-02-19
11:35 캔들은 아카이브와 REST 모두 종료 시각이 11:35:32.286이었다.
파서 버전 2부터 구간 내부의 종료 시각을 허용하고 실제 배타적 종료 시각
`bar_end=11:35:32.287`을 보존하며 `short_source_bar` 품질 표시를 추가한다.
구간 밖 종료 시각·잘못된 OHLC·시각 격자·중복 검사는 유지한다.

종료 시각을 1분 끝으로 늘리거나 누락된 거래를 합성하지 않는다. 리샘플링에도 품질 표시가
전파된다. **시작 시각 격자의 완전성은 매 캔들이 60초 전체를 담는다는 보장이 아니다.**
기존 저장 자료와 고정 스냅샷을 이 변경 때문에 다시 쓰지 않는다.

초기 OI 파일에는 모든 CSV 필드가 동일한 중복 행이 있었다. 파서 버전 3은 archive OI에
한해 같은 시각의 **모든 필드 문자열이 동일한 경우** 첫 행을 표준 자료에 유지한다.
`exact_source_duplicate` 표시와 보존 행·중복 행의 원문 번호를 품질 이력에 기록한다.
원본 ZIP의 중복은 제거하지 않는다. OI 값이 같더라도 다른 CSV 필드가 다르면 거절하고,
0값의 동일 중복도 격리한다. 다른 데이터셋의 중복 거절 정책은 유지한다.

저장된 ZIP과 CHECKSUM을 다시 검증해 새 파서로 재처리할 수 있다. 원문을 변경하거나
재다운로드할 필요는 없다. 아래 검사는 자료별 실제 시작 이후의 누락·격리와 품질 표시를
집계하며 기존 스냅샷을 만들거나 변경하지 않는다.

```bash
.venv/bin/python scripts/build_history_ledger.py --end 2026-09-22
```

## depth의 복구 범위

배포 서비스는 10초마다 독립 REST top 20 대조를 시도하고, 알려진 가격 단계가 줄면
더 이르게 확인한다. 제한된 스냅샷 가격 범위가 소진되면 같은 소켓에서 새 epoch와
새 기준 스냅샷을 시작한다. 이미 실패한 epoch와 `sync_losses`는 보존한다.
이 방식도 급격한 가격 이동이나 원천 스냅샷 지연으로 인한 호가 공백을 복원하지 못한다.
REST 기준 ID와 WebSocket 증분이 연결되지 않으면 동기화 성공으로 처리하지 않는다.
새 기준 스냅샷이 첫 증분보다 오래된 경우에는 연결을 유지하며 복원하지 못한 증분도
원문·표준 journal에 저장하고, 10초 간격으로 새 epoch와 기준 스냅샷을 시도한다.
`lagged_snapshots`와 `unreconstructed_messages`로 이 상태를 따로 기록한다.
이후 원천이 회복되어도 실패 이력은 남고 전체 세션을 완전하다고 표시하지 않는다.
새 정책을 기록한 수집에만 실패 epoch의 미복원 증분 보존을 허용하며, 이전 journal의
엄격한 재생 규칙은 유지한다.

실시간 감시의 수신 나이는 애플리케이션이 받은 메시지의 **저장된 최신 시각** 기준이며,
네트워크 도착 지연 측정이 아니다. 과거 확장 전후의 오류, 원천 누락, 무결성, 기존
revision·snapshot 보존, 새 백업을 각각 검증한다.
