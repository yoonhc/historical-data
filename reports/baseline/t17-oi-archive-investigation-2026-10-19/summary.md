# T17 · 10/15 UTC BTCUSDT OI 아카이브 원인 조사

**2026-10-19 00:14 KST, 저장된 원문과 당시/현재 설치본으로 원인 조사를 완료했다.** 두 시도에서 받은 동일한 ZIP에는 **금액이 0인 행 14개, 그중 수량도 0인 행 8개**가 있다. 기존 정책이 해당 날짜를 `quarantined_zero_oi`로 격리해 정상 revision·query가 생성되지 않았고, scheduler가 완료 근거를 거부했다. 단순한 완료 근거 연결 누락으로 복구할 수 있는 상태가 아니다.

**T17 전체는 미완료다.** 현재 공개 원본이 수정됐는지와 실제 자료 복구는 아직 확인하지 않았다. `needs_review`·실패 이력·refresh 대기를 유지했다. 조사에서는 운영 DB를 읽기 전용으로 열고 보고서 파일만 작성했다. 네트워크 요청·재수집·review 적용·서비스 제어·백업·전체 검사·삭제는 실행하지 않았다. T05의 승인된 반복 자동화와 10/19 15:40 KST 관찰 계약, T16 완료 범위도 변경하지 않았다.

## 대상과 원래 두 시도

| 항목 | 값 |
|---|---|
| 자료 | Binance / linear_perpetual / BTCUSDT / open_interest / 5m / archive |
| UTC 범위 | `[2026-10-15 00:00, 2026-10-16 00:00)` |
| acquisition task | `c24159c0b9f2893037ac67c41e52d5bbdfd0a9ed2c3f62ecdae43d6c7b32b6e9` |
| 첫 attempt | `0af32466cb6e44efbee0fe5ba85c5282`, 10/16 18:00:24 KST, `validation_retry` |
| 두 번째 attempt | `39f32b6665984af28a7e3b84e73b13da`, 10/16 20:00:12 KST, `needs_review` |
| 공통 config hash | `fb9a36a40e5b54460bb5f5b9cd7f142eafe0f99e223e20c965299b8def39f2d2` |
| 원래 최종 오류 | `DataError: Task has no complete verified publication proof` |

각 시도는 ZIP과 CHECKSUM을 한 번씩 받아 모두 HTTP 200으로 종료했다. 총 4개 HTTP 응답과 fetch 행이 남아 있다. 두 시도는 같은 ZIP·CHECKSUM을 사용했고, validation 실패 2회 후 점검 필요로 전환됐다. 404 미공개·429 냉각·통신 오류로 인한 대기가 아니다. 원래 두 attempt는 [10/18 보존본](../scheduled-clock-operating-review-2026-10-18/preexisting-oi-attempts.json)과 내용이 동일하다.

## 원문·내용 대조

| 확인 | 결과 |
|---|---|
| ZIP SHA-256 | `5f1c31bdf645489221d120079529bd311168258ef524a669e88908db22139d90` |
| CHECKSUM SHA-256 | `e83db5cde9085b39e8545c7c620f4de5044fae82648ad61967e6113c828524e5` |
| 크기 | ZIP 11,218바이트 / CHECKSUM 97바이트 |
| CHECKSUM 내용 | ZIP 파일명과 실제 SHA-256 모두 일치 |
| ZIP member | `BTCUSDT-metrics-2026-10-15.csv` 한 개 |
| 날짜·격자·중복 | 288행·288개 고유 시각, 해당 UTC 날짜의 00:00–23:55 5분 격자와 정확히 일치 |
| 값 | `sum_open_interest_value`가 0인 행 14개. 그중 `sum_open_interest`도 0인 행 8개 |
| 원문 순서 | 인접 역순 20곳. parser가 정렬하고 경고를 남기며, 이것 자체는 격리 사유가 아님 |
| 경제값 fingerprint | `4490cc2440a746d7edee837c64c8b702bc2ad0a95caaa58b13e915b534d3a3b0`, 두 `publish_intent`와 일치 |

[ZIP 사본](BTCUSDT-metrics-2026-10-15.zip), [CHECKSUM 사본](BTCUSDT-metrics-2026-10-15.zip.CHECKSUM), [추출 CSV](BTCUSDT-metrics-2026-10-15.csv)는 기존 로컬 원문에서 복사했다. 새 공개 URL을 요청한 자료가 아니다. 전체 원본 경로·artifact·fetch·응답 헤더는 [현재 대조 원본](current-inspection.json)에 있다.

0값은 UTC **13:40–14:45**의 연속된 14개 source label에 있다. 해당 날짜 KST 22:40–23:45다.

| UTC label | 수량 0 | 금액 0 |
|---|---|---|
| 13:40 | 예 | 예 |
| 13:45 | 아니오 | 예 |
| 13:50 | 아니오 | 예 |
| 13:55 | 예 | 예 |
| 14:00 | 아니오 | 예 |
| 14:05 | 예 | 예 |
| 14:10 | 예 | 예 |
| 14:15 | 아니오 | 예 |
| 14:20 | 예 | 예 |
| 14:25 | 예 | 예 |
| 14:30 | 아니오 | 예 |
| 14:35 | 예 | 예 |
| 14:40 | 예 | 예 |
| 14:45 | 아니오 | 예 |

정확한 원문 값·CSV 행 번호는 각 대조 JSON의 `parser_replay[].zero_rows`에 보존했다. 이는 **수신한 원문에 0이 있다는 확인**이다. 실제 시장 OI가 0이었다거나 특정 시간 동안 거래소 장애가 있었다고 판정한 것은 아니다. 기존 `archive_label_unverified`·수량/금액 단위 미확인 의미도 유지한다.

## 완료 근거가 끊긴 위치

1. `normalize_archive()`가 수량 또는 금액이 0인 행을 `zero_oi`로 기록하고 quarantine을 표시한다.
2. `RecordingCollector._publish()`는 정규화된 288행의 `publish_intent`를 먼저 저장한다. 이 intent는 이후 정상 게시가 확정됐다는 증거가 아니다.
3. `Store.publish()`는 그 날짜에 `zero_oi`가 있으면 `quarantined_zero_oi` issue를 기록하고 정상 revision/query 게시를 건너뛴다. 이번에는 기존 정상 revision도 없어 active·revision·query가 모두 0개다. 0이 아닌 나머지 행을 임의로 완성된 날짜로 인정하지 않는다.
4. 실제 저장 issue **26**은 `blocking=1`, `resolved=0`이며 원문 replay의 14개 `zero_oi`와 순서 경고가 정확히 일치한다. 내부 작업 행 2개도 `quarantined`다. 두 작업 행을 두 번의 추가 다운로드로 세지 않는다. 같은 issue는 중복 등록하지 않는 기존 동작으로 한 개만 남았다.
5. `inventory()`는 정상 자료의 빈 범위 288개와 blocked day `2026-10-15`, `complete=false`를 반환한다. 여기의 **missing 288은 정상 게시된 시계열의 부재**이며 원문 CSV 행 288개가 없다는 뜻은 아니다.
6. `Scheduler.proof()`는 이 inventory 검사에서 바로 `None`을 반환한다. 이후 query·revision·원문 연결 검사에 도달하기 전이며, 최종 일반 오류가 `Task has no complete verified publication proof`로 기록된다.

현재 설치된 `reconcile_proof()`도 `No complete, unblocked verified acquisition evidence`로 거부했다. 이미 저장된 정상 결과에 포인터만 다시 연결하는 복구를 적용할 수 없다. 같은 ZIP을 다시 처리하면 같은 격리 조건을 만나므로, 근거 없이 재시도 예산만 추가하지 않는다.

## 실제 설치본으로 확인한 범위

| 구분 | 실행본 |
|---|---|
| 당시 | `t13-daily-scheduler-20261011-703fb0d455523c1b` |
| 현재 | `scheduled-clock-policy-20261016-686bb70c9ee4ef11/daily` |

각 설치본의 `.venv/bin/python -B -I`로 [읽기 전용 대조기](inspect_readonly.py)를 실행했다. 실제 import 경로·Python 버전·모듈 해시는 [당시 대조](original-inspection.json), [현재 대조](current-inspection.json)에 있다. `Store`·`Collector`·`Scheduler` 생성자를 실행하지 않고, `mode=ro`·`PRAGMA query_only=ON`·단일 읽기 transaction의 최소 facade로 **설치된 inventory/proof/reconcile 함수**를 호출했다. canonical active가 없는 경로만 실행했으며, DB 연결별 변경 수는 모두 0이다.

두 실제 parser는 원문과 각 attempt의 수신 시각으로 같은 288행·14개 zero issue·fingerprint를 산출했다. `normalize_archive`, `Store.publish`, `inventory`, `Scheduler.proof`, `RecordingCollector._publish`, `reconcile_proof`의 함수 AST도 이전/현재 동일하다. **T16 시각 정책 전환이 이 판정을 새로 만든 근거는 없다.** 전체 CLI 재수집·systemd 운영 재현이나 모든 실패 경로의 회귀 검사를 실행했다는 뜻은 아니다.

두 읽기 transaction의 선택 task/attempt/issue/job/fetch/revision/query 기록도 동일했다. [통합 비교 결과](comparison.json)와 [보고서 해시 목록](artifacts.json)에 연결했다. 운영 전체 DB는 정기 업무가 계속 쓰므로 전체 파일 불변을 주장하지 않는다.

## 남은 범위와 다음 행동

00:14 KST 기준 acquisition은 같은 두 attempt와 `needs_review`를 유지한다. refresh는 **10/16·17·18·19 확인 주기 4개**가 `planned`다. 10/19 주기는 이전 10/18 보고서 이후 추가됐으며, 기존 완료 작업을 취소한 것이 아니다. 원래 acquisition/점검 필요를 우회하지 않는 scheduler 조건으로 후속 refresh가 대기한다.

다음은 **이 날짜·이 URL의 공개 CHECKSUM 수정 여부를 한정해 확인하는 것**이다. 이번 조사에서는 네트워크 요청을 하지 않았으므로 현재 공개본이 여전히 같다고 단정하지 않는다.

- CHECKSUM이 같으면 같은 자료의 무조건 재시도·`needs_review` 해제 없이 점검 필요를 유지한다. 확인 시각과 응답을 남기고 수정 원본 또는 별도 품질 정책 판단이 필요한 상태로 관리한다.
- CHECKSUM이 달라졌다면 해당 ZIP만 별도 경로에 받아 CHECKSUM·날짜·격자·중복·0값과 내용 차이를 먼저 확인한다. 변경됐다는 사실만으로 품질 통과로 인정하지 않는다.
- 품질을 통과한 수정 원본이 있을 때 기존 review 절차의 **정확한 한 acquisition 복구/재처리 범위**를 고정한다. 운영 적용은 별도 확인 후 진행하며 원래 원문·두 실패·issue 이력은 보존한다. 이어서 기존 refresh 재개와 최종 proof를 확인해야 T17 완료를 판단할 수 있다.

REST series를 archive에 합치거나 0값을 보간·삭제해 통과시키는 변경, generic proof 오류 표시 개선, 새 자동 복구 시스템은 이번 조사에 추가하지 않았다. 현재 확인한 내용으로는 기존 품질 조건이나 시각 정책을 바꿀 이유가 확인되지 않았다.
