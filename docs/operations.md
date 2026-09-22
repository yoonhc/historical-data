# 수집 운영과 복구

수집기는 Freqtrade와 독립적으로 실행됩니다. 자동 수집은 원본과 표준 데이터만 갱신하며,
연구용 스냅샷과 Freqtrade 출력은 명시적으로 새로 생성합니다.

## 운영 범위

| 설정 | 수집·검사 범위 | 반복 갱신 | 오래된 누락 |
|---|---|---|---|
| `configs/daily.toml` | 2026-09-14 UTC부터 완료된 UTC 날짜까지 | 최근 3일 재조회, 메타데이터 현재 관측 | 범위 내 누락을 데이터셋별 실행당 최대 32일에 포함해 복구 |
| `configs/oi-live.toml` | 최초 최근 28일의 시작점을 저장하고 계속 확장 | 최근 60분 재조회 | REST 복구 가능 구간의 중간 누락도 다시 조회 |

`operations.start`를 명시하면 해당 UTC 날짜를 검사 시작점으로 사용합니다. 시작점이 없으면
기존 등록값을 유지하고, 최초 등록 때만 `bootstrap_days`와 기존 자료의 가장 이른 날짜로 정합니다.
따라서 과거 OI 연구 자료를 가져온 일별 설정에는 시작점을 명시했습니다. 운영 범위가 정상이라는
결과는 3년 전체 자료가 연속이라는 뜻이 아닙니다.

최근 자료를 우선 처리한 뒤 오래된 누락부터 복구합니다. `refresh_days`는
`max_days_per_run`보다 작아야 하며, 기본 일수 제한은 32입니다. 한 번에 처리하지 못한 날짜는
보고서의 `deferred_days`에 남고 다음 실행에서 계속 처리합니다.

REST OI는 기간 종료 시각을 라벨로 사용합니다. 5분 경계 후 45초가 지나면 방금 종료된 라벨을
기대하며, 실제 HTTP 조회는 현재 시각까지만 요청합니다. 이 45초는 운영상 여유이며 거래소의
게시 지연 보장은 아닙니다. 공식 API가 최근 한 달의 이력만 제공하므로 복구 요청은 여유를 둔
최근 29일 내로 제한합니다. 그보다 오래된 누락은 `expired_missing_ranges`에 남습니다.
[Binance 공식 시장 데이터 문서](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)

## 상태 확인

```bash
.venv/bin/marketdata status --table
.venv/bin/marketdata status --config configs/oi-live.toml --report reports/oi-status.json
```

표에는 최신 원천 라벨, 누락 행 수, 차단된 날짜 수, 마지막 실행 결과가 표시됩니다.
`Blocked(scope/all)`의 앞 숫자는 운영 범위 내 날짜, 뒤 숫자는 전체 보관 이력에서 미해결 문제가
있는 날짜입니다. 같은 날짜에 문제가 여러 개 있어도 날짜 수는 중복 계산하지 않습니다.
펀딩의 `Missing` 단위 `d`는 조회하지 않은 구간이 있는 날짜 수입니다.

- `healthy`: 운영 범위의 완전성·최신성 및 마지막 실행이 정상입니다.
- `partial`: 누락·충돌·격리·미게시 자료가 있거나 마지막 갱신이 불완전합니다.
- `stale`: 최신 자료의 나이가 기준을 넘었습니다. 기본 일별 36시간, REST OI 15분입니다.
- `failed` / `running`: 마지막 갱신이 실패·중단됐거나 실행 중입니다.
- `empty` / `unmanaged`: 자료가 없거나 아직 운영 갱신을 실행하지 않았습니다.

여러 조건이 동시에 발생하면 표에는 하나만 표시하므로 JSON의 `coverage`, `last_run.errors`,
`latest_source_job`도 확인합니다. 갱신 시각 직전·실행 중에는 새로 기대되는 라벨이 아직 없어
일시적으로 `partial`일 수 있습니다. 일별 자료는 UTC 자정 이후 다음 예약 수집 전까지 새 날짜가
누락으로 표시될 수 있습니다. `status`는 현재 상태 조회이며 외부 알림은 아직 연결하지 않았습니다.

상태 조회는 정상일 때 종료 코드 0, 그 밖에는 2를 반환합니다. 상태의 완전성 캐시는 불변 revision별로
저장하며 새 revision과 격리 상태를 반영합니다. 파일 손상까지 검사하려면 별도로 실행합니다.

```bash
.venv/bin/marketdata validate
```

펀딩의 조회 완료는 고정된 8시간 간격이나 이벤트의 존재를 보증하지 않습니다. 메타데이터는
현재 관측만 검사합니다. OI의 격자 완전성도 archive 시각·수량 단위를 검증했다는 뜻은 아닙니다.

## 중단·실패·품질 문제

매 실행은 카탈로그의 실제 게시 상태로 누락을 다시 계산합니다. 각 날짜의 게시와 각 데이터셋의
실행 기록을 저장하므로 중단 후에도 이미 게시한 날짜를 활용합니다. 쓰기 잠금을 획득한 다음
이전 `running` 기록이 남아 있으면 `interrupted`로 변경합니다. 한 데이터셋의 원천 오류가 나도
나머지 데이터셋은 진행하고, 전체 실행은 불완전 상태로 종료합니다.

충돌·격리된 날짜는 자동 갱신에서 건너뛰며 상태에 계속 표시합니다. 문제 날짜의 원본과 수정본을
검토한 뒤 `repair`로 복구합니다. 변경값의 채택이 필요하다면 그때 `--accept-revisions`를
명시합니다. 최근 자료의 누락을 오래된 OI archive로 대체하거나 0값을 자동 승인하지 않습니다.

```bash
# 범위 내 누락 복구를 즉시 실행합니다. 자동 수집과 겹치면 잠금 때문에 실패할 수 있습니다.
.venv/bin/marketdata update --config configs/daily.toml --report reports/daily-update.json
.venv/bin/marketdata update --config configs/oi-live.toml --report reports/oi-update.json
```

장기 과거 수집에는 배치마다 쓰기 잠금을 반납하는 `backfill-set`을 사용합니다.
`update`/`watch`/`repair`는 다른 쓰기 작업을 최대 60초 기다린 뒤 진행합니다. 세부 수집·보고서·
스냅샷 절차는 [3년 연구 데이터셋 문서](research-dataset.md)를 봅니다.

타이머·로그·시작 제한 해제·중지 방법은 [systemd 운영 문서](../integrations/systemd/README.md)를 봅니다.
자동 갱신 전에 만든 SQLite 백업은 `state/backups/`에 있습니다. 이는 카탈로그 변경 전 복구 지점이며,
전체 데이터 백업을 대신하지 않습니다. 정식 백업은 원본·표준 데이터·스냅샷과 일관된 카탈로그를
함께 보존해야 합니다.
