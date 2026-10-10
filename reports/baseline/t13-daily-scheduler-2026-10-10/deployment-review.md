# T13 적용·원복 검토안

10/11 후속: 아래 보완 요구를 구현·검증한 `703fb0d455523c1b`를 별도로 고정했다.
이 문서는 이전 후보의 당시 검토 기록이다. 현재 적용 대상·재개 절차는
[새 적용·재개·원복안](../t13-daily-review-2026-10-10/deployment-review.md)을 따른다.
기존 소스·패치·해시와 178개 검사 기록은 변경하지 않았다.

상태: **기존 후보 178개 검증 완료 / 배포 전 needs_review 재개·사본 미리보기 보완 미완료 / 실제 설치·timer 변경 미승인·미실행**.
보존할 기존 후보는 `t13-daily-scheduler-20261010-89912177f7b3adad`다.
기준 commit과 전체 파일/소스 묶음/패치 SHA-256은 [manifest.json](manifest.json)을 따른다.

10/10 검토 반영: **스케줄러 적용과 공개 시각 관측 활성화는 별도 결정**이다.
첫 적용안은 관측기 `enabled=false`를 유지한다. 관측 날짜 선정이나 조사 완료를 스케줄러 배포의 선행 조건으로 두지 않는다.
이 보완은 적용 절차에 대한 검토이며 운영 적용 승인·실행 기록은 아니다. 고정 후보 코드·해시는 바꾸지 않았다.

10/10 추가 검토로 이전의 “needs_review 재개 절차는 필요가 생길 때 준비” 결정을 수정한다.
**작업별 안전한 재개 경로를 구현·검증한 뒤 배포한다.** 아래 보완은 아직 구현하거나 시험하지 않았으며,
기존 178건을 새 재개 기능의 검증으로 해석하지 않는다. 보완 코드는 새 후보 ID·manifest·시험 결과로 고정한다.
기존 후보는 비교·재현용으로 보존하고 적용 검토·승인을 새 코드에 자동 승계하지 않는다.

## 기존 후보의 예정 변경 범위

다음은 89912177f7b3adad의 변경 목록이다. 보완 후보가 준비되면 새 배포 경로·추가 원장 기록·최종 해시를 다시 확정해야 하며, 현재 목록으로 바로 설치하지 않는다.

| 경로 | 예정 작업 |
|---|---|
| `/opt/marketdata/releases/t13-daily-scheduler-20261010-89912177f7b3adad/` | 고정 소스와 lock 기반 비-editable 가상환경 신규 설치. 기존 수집 release와 분리 |
| `/etc/marketdata/daily-scheduler.toml` | 기존 8개 대상·범위와 T13 시각/재시도 설정 설치 |
| `/etc/marketdata/daily-publication-probe.toml` | 기본 비활성. 실제 관측을 승인할 경우 대상 날짜·기간을 별도 고정 |
| `~/.config/systemd/user/marketdata-daily.service` | ExecStart를 고정 release의 `python -I -m marketdata.daily_schedule run`으로 변경 |
| `~/.config/systemd/user/marketdata-daily.timer` | 5분 평가·30초 jitter·Persistent 유지. due 대상만 네트워크 처리 |
| 운영 `state/catalog.sqlite` | 최초 승인된 실행부터 `daily_tasks`, `daily_attempts`, `daily_cooldowns`, `daily_runs`, `daily_probe_targets` 테이블을 추가. 기존 schema/자료 삭제 없음 |
| 운영 `state/daily-scheduler.lock`, `state/daily-scheduler-latest.json` | 실행 직렬화와 현재 결과. JSON은 보조 사본, 작업 이력은 카탈로그에 저장 |

원문·canonical·queries 및 기존 `sync_runs`에는 실제 승인된 작업의 수집 결과/집계가 추가된다. 기존 원장 밖의 완료 플래그만으로 성공을 선언하지 않는다. 이전 일괄 갱신기의 `daily-latest.json`과 과거 sync_runs는 덮어쓰지 않는다.

새 production Python 모듈은 [daily_schedule.py](candidate/packages/marketdata/src/marketdata/daily_schedule.py), [daily_probe.py](candidate/packages/marketdata/src/marketdata/daily_probe.py) 두 개다. 공통 HTTP/파서/저장/검증/백업 모듈 변경은 없다. 나머지 추가 파일은 신규 테스트·설정·유닛 템플릿이다. 현재 workspace의 import 경로에는 이 모듈들을 설치하지 않았다.

## 유지해야 할 운영 조건

- `/etc/systemd/user/marketdata-daily.service.d/90-marketdata-clock.conf`의 기존 `Requires`, `After`, 엄격한 `ExecStartPre`를 그대로 유지한다.
- `/etc/marketdata/clock-bootstrap.json`은 이 drop-in을 해시로 보호하고, 기존 daily service/timer 이름을 inventory로 관리한다. 확인한 guard_files에는 사용자 base daily service/timer 본문이 없었다. 이번 안은 새 marketdata 유닛 이름을 만들지 않는다.
- 실제 적용 직전에 guard_files·유닛 inventory·타이머 대상·실효 ExecStartPre 전체를 다시 대조한다. 불일치가 있으면 gate나 ledger를 수정해 통과시키지 않는다.
- Chrony, RTC, Windows, boot/closed ledger, runtime 프로필, collector 두 서비스, backup/pack/restic 예약·설정, 자동 정리 mask는 변경 범위 밖이다.
- 기존 writer/layout 잠금과 추가 scheduler 단일 실행 잠금을 유지한다. 원문 수집기는 정상 운영 중인 채로 두고 기존 유지보수 작업을 강제 종료하지 않는다.

## 승인 후 적용 순서

선행 조건: 아래 재개·미리보기 보완 검증과 새 후보 고정을 마친 뒤, 해당 배포본의 적용 범위를 별도로 승인받는다.

1. 현재 daily base 유닛·timer 내용/권한/enable 상태·drop-in과 guard 해시, 실행 중 작업·관련 저장소 상태를 전환 기록에 보존한다. 관측기는 `enabled=false`를 확인한다. 아래의 실제 저장소 기준 대상 미리보기를 먼저 수행한다.
2. 신규 `/opt` release를 별도 설치·해시 검증한다. root 소유로 고정하고 yoonhc의 실행/읽기, 실제 Python import, 설정 읽기와 기존 데이터 경로 접근을 확인한다. 여기까지 기존 실행 경로와 분리한다.
3. **daily timer만** 잠시 중지해 새 실행 유입을 막고, 이미 실행 중인 daily는 자연 종료를 기다린다. 강제 종료로 원본 다운로드/검증을 끊지 않는다. 운영 데이터의 과거 카탈로그 사본을 복원하지 않는다.
4. 후보 base unit/timer와 설정을 설치한다. `@RELEASE@`를 고정 ID로 치환한다. root 소유 clock drop-in을 수정하거나 제거하지 않는다. 사용자 manager의 daemon-reload 후 실효 명령·기존 gate·타이머 대상을 확인한다.
5. 현재 엄격한 준비가 통과할 때 기존 이름의 daily를 한 번 실행해 현재 due 작업·이월 상태·새 조회/기존 status의 연결을 확인한다. 예정 전/404 대기는 실패를 숨긴 전체 완료가 아니며, 상태 필드를 확인한다.
6. 실제 결과가 맞으면 승인한 timer를 재개한다. 운영 적용 시점이 예정 시각을 지났다면 과거 슬롯을 전부 재생하지 않고 현재 due 대상만 한 번씩 처리한다.
7. 첫 실행 및 처리 0건 재평가에서 요청 대상 제외, 실행 시간·CPU·잠금 경합·수집 저장 지연을 가능한 기존 지표로 기록한다. 첫 정오/저녁/날짜 이월과 다음 실제 checkpoint/restic의 새 원장·자료 편입 및 별도 복원 후 조회 일치를 확인한다. 공개 관측은 별도 활성화 시에만 고정 창 종료와 실제 결과를 확인하며 스케줄러의 완료 조건에 포함하지 않는다. 예약 시각 경과·처리 0건을 미래 경계 관찰 완료로 세지 않는다.

이 문서는 적용 범위와 절차를 제시한 것이며, 위 명령을 이번 작업에서 실행했다는 기록이 아니다. 관리자 인증이 필요한 설치·최종 실효 유닛 접근 확인은 승인된 적용 단계에서 수행한다. `systemd-analyze verify`는 실제 서비스 기동 시험을 대신하지 않는다.

## 적용 직전 대상 미리보기

현재 후보 CLI에는 독립적인 `preview` 명령이 없다. `Scheduler.plan()`도 원장·scope·coverage 기록을 변경하므로,
운영 root에 `run` 또는 `plan()`을 호출해 미리보기라고 표현하지 않는다.
일관된 카탈로그 사본과 격리된 쓰기 경로에서 원문/표준 파일 근거를 읽도록 준비하고,
HTTP 전송과 운영 카탈로그/원장 쓰기가 없는 방법을 확인한 후 미리보기를 수행한다.
이 보조 절차는 아직 구현·검증하지 않았다. 원문 전체 복제나 범용 관리 기능은 요구하지 않는다.

- 기존 backup의 SQLite Online Backup API 사용을 참고해 운영 카탈로그의 일관된 사본을 만든다. WAL 모드의 본체 파일만 복사하지 않는다. 근거: [SQLite Backup API](https://www.sqlite.org/backup.html), [WAL](https://www.sqlite.org/wal.html).
- DB·JSON·scheduler/writer 잠금·임시 파일·출력의 쓰기 대상은 모두 시험 경로로 제한한다. 운영 Store를 초기화하는 경로가 스키마 등을 쓰지 않는지도 확인한다.
- 사본 DB만으로 원문의 물리 배치가 고정되지는 않는다. 원본 파일을 참조하면 기존 shared layout 잠금과 packed/loose 읽기 계약을 유지하고, 사본 생성부터 필요한 읽기 완료까지 pack 이동과의 정합성을 확인한다. 시험 경로의 별도 잠금을 운영 파일 보호의 대용으로 쓰지 않는다.
- 운영 collector의 쓰기가 계속되므로 운영 DB 전체의 전후 해시 일치를 요구하지 않는다. 연결 권한·실제 쓰기 대상·HTTP 호출 없음을 검증한다. 사본 DB를 운영 DB에 되돌리지 않고 첫 실제 실행에서 최신 상태를 다시 평가한다.

미리보기는 기준 시각·카탈로그 사본 식별자와 다음 분류를 남긴다.

- 저장 근거가 확인돼 acquire를 생략할 대상.
- 확인 주기가 도래한 최근 3일 refresh와 현재 metadata 관측.
- 날짜가 고정된 미완료 이월.
- 최초 또는 재시도 예정 시각 전인 대상.
- 보존된 냉각·점검 필요 상태. 첫 전환에서 새 원장이 없다면 **새 원장에 기록된 이력이 없음**으로 표시한다. 다른 수집기·기존 CLI의 HTTP 제한 이력까지 없다는 뜻으로 확대하지 않는다.

이미 받은 날짜도 이번 주기의 refresh가 도래하면 요청 대상이다. 기존 CLI의 과거 성공을 새 refresh 주기의 완료로 임의 변환하지 않는다.
미리보기와 실제 실행 사이의 경과·자료 변화를 고려해 적용 때 다시 확인하며, 미리보기 자체를 다운로드·검증 완료로 세지 않는다.

## needs_review 재개: 배포 전 필요한 최소 보완

현재 고정 CLI의 action은 `run`, `status`, `probe`이며 **작업별 retry/requeue/resolve 명령은 없다**.
`needs_review`는 terminal 상태다. 자동 완료 대조나 같은 날짜 성공의 공유 대상에서도 제외된다.
기존 CLI의 보강 결과를 자동 인정하는 시험은 terminal이 아닌 pending acquisition에 해당한다.
그 결과를 needs_review 해제까지 검증한 것으로 확대하지 않는다.

작은 CLI 또는 관리 스크립트로 정확한 작업 하나를 처리하되, 다음 두 동작을 구분한다. 명령 이름은 아직 확정하지 않았다.

| 동작 | 필요한 근거와 결과 |
|---|---|
| 저장 근거로 완료 연결 | 작업의 key·UTC 날짜/범위·목적·확인 주기를 충족하는 검증된 저장 결과를 대조한 후 완료 연결한다. HTTP 요청이나 자료 덮어쓰기는 하지 않는다. 근거 부족 또는 미해결 충돌이면 needs_review를 유지한다. |
| 추가 재시도 승인 | 원인 조치 사유와 명시적인 추가 시도 한도를 기록하고 해당 작업만 재시도 대기로 돌린다. 승인 자체로 HTTP를 보내거나 완료 처리하지 않는다. scheduler가 실행 가능 시각·현재 호스트 냉각·기존 검증 기준을 다시 확인한다. |

공통 계약:

- 요청 ID, 대상 작업 ID, 동작, 사유, 추가 예산 또는 사용한 저장 근거, 처리 전후 상태와 결과를 남긴다. 과거 시도·실패·기존 예산 소진 사실을 삭제하거나 0으로 초기화하지 않는다. 추가 예산은 운영자의 명시적인 허용분으로 구분한다.
- 같은 요청 ID와 같은 내용의 반복은 이미 확정한 결과를 반환하고 예산을 다시 더하지 않는다. 같은 ID로 대상·동작·예산 등을 바꾸면 거부한다. 과거 요청의 재실행으로 이미 진행한 작업 상태를 되돌리지 않는다.
- 기존 scheduler 배타 잠금과 Store writer/SQLite transaction을 사용해 상태·추가 예산·처리 기록을 함께 확정한다. scheduler 경합이나 중간 종료로 부분 승인·중복 처리가 생기지 않는지 시험한다. 이 설계가 실제로 구현됐다는 뜻은 아니다.
- acquire의 저장 범위 충족과 특정 refresh 주기의 원격 재확인은 별도 근거다. 현재 `proof()`의 `checked_at`은 로컬 검증 시각이므로 새 원격 확인 시각으로 쓰지 않는다. 기존 fetch 이력·대상 URL·실제 관측 시각·검증된 source/revision 연결 등을 대조하고, 해당 주기의 성공을 입증하지 못하면 완료 연결을 거부한다.
- 재시도 승인은 충돌 revision 채택이나 검증 예외 승인으로 해석하지 않는다. 과거 정상 revision을 보존하며 동일 자료의 검증 실패를 제한 없이 반복하지 않는다.
- DB 직접 편집·예산 자동 초기화·이전 실행기로의 원복을 냉각/점검 제한의 우회 방법으로 쓰지 않는다. 이번 설계 검토는 특정 운영 작업의 재개 승인이 아니다.

추가 검증은 기존 CLI 보강 후 HTTP 없는 완료 연결, 특정 작업의 제한된 재시도와 냉각 유지,
동일 요청 반복/내용 불일치/transaction 중단/scheduler 경합, 사본 미리보기의 운영 쓰기·HTTP 없음으로 한정한다.
변경이 영향을 주는 기존 회귀 검사는 재사용하되 새 기능의 결과와 이전 178건을 구분한다.

재시도 수치의 의미도 구분한다. 같은 하위 요청에서 HTTP 4회 × 작업 3회이면 최대 12회이고,
ZIP/CHECKSUM 등 한 작업의 전체 요청 수와 같지 않다. 초/HTTP-date 형태의 Retry-After는
`cooldown()`이 최소 900초와 비교해 더 긴 대기를 반영하며, 기존 냉각 만료보다 앞당기지 않는다.
냉각 공유 범위는 이 scheduler/관측기의 동일 호스트 요청이며, 두 실시간 수집기나 다른 프로그램 전체를 통제하지 않는다.

## 원복

1. 새 daily timer를 중지하고 진행 중 새 실행의 정상 종료를 기다린다. 종료를 기다릴 수 없는 상황은 별도 장애 대응으로 기록한다. 다음 기동은 running 작업을 저장 근거로 복구하도록 구현했다.
2. 보존한 **daily base service/timer만** 되돌린 뒤 daemon-reload한다. 기존 root clock drop-in과 strict gate를 확인하고 이전 enable 상태를 복원한다.
3. 새 raw/canonical/revision, 원장 테이블, checksum/오류 증거, sync_runs·JSON 보고서는 보존한다. DB를 전환 전 사본으로 되돌리거나 새 테이블을 삭제하지 않는다. 이전 Collector·validate·backup가 추가 테이블을 포함한 저장소에서 동작함을 격리 확인했다.
4. 새 원장 상세 조회가 필요하면 고정 release의 `python -I -m marketdata.daily_schedule status --root ...`를 읽기 전용으로 유지한다. 이전 CLI의 update 결과와 새 원장 집계의 출처를 구분한다.
5. 원복하면 **이전의 정오 일괄 갱신·전체 재시도 정책도 돌아온다**. 새 기능의 미완료 목록/냉각 기록이 이전 실행기를 자동 통제하지는 않는다. 냉각이 진행 중이거나 점검 필요가 있으면 이전 일괄 갱신을 즉시 강행하지 말고 현재 HTTP 제한·자료 상태를 먼저 확인한다.

새 후보를 다시 적용할 때 pending acquisition은 기존 CLI가 확보한 검증 저장 근거로 완료될 수 있다. 미래 수정 확인까지 소급 완료하지 않는다. `needs_review`의 원인 해결·재시도 결정은 자동 예산 초기화나 DB 직접 편집으로 대체하지 않는다.

## 조회 및 재현

운영 승인 후 권장 조회:

```text
/opt/marketdata/releases/t13-daily-scheduler-20261010-89912177f7b3adad/.venv/bin/python -I -m marketdata.daily_schedule status --root /home/yoonhc/projects/algoTrade/historical_data
```

`runner_ok=true`/종료 코드 0은 이번 실행기의 정상 종료다. `data_complete`, `review_required`, pending별 상태·다음 시각을 함께 본다. 구 CLI의 시장 자료 coverage와 최신 집계도 유지되지만 자세한 재시도/관측 상태는 새 조회를 사용한다. 실행 전에 strict gate가 막혔다면 새 보고서가 생성되지 않을 수 있으므로 보고서 as_of와 unit/journal을 함께 확인한다. DB/JSON 쓰기 자체가 실패한 상태를 최신 성공으로 해석하지 않는다.

재현은 [prepare_reproduction.py](prepare_reproduction.py)에 **존재하지 않는 별도 경로**를 지정한다. 이 스크립트는 고정 아카이브/패치 해시를 확인하고 기준 파일 복원 → git apply check/apply → 146개 파일 대조까지만 수행한다. 의존성 설치와 시험은 [validation.md](validation.md)의 명령으로 진행한다. 운영 root를 시험 입력으로 지정하지 않는다.
