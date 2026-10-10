# T04 — 운영에서 생성한 v2 시각 근거의 백업·복원

상태: **신규 v2 시각 근거의 운영 백업·복원 검증 완료**.
10/10 **15:24:19 KST**에 복원과 조회 대조를 완료했고, **16:02:29 KST**에
운영 설정·부팅 증거·수집 실행·자동 정리 mask 유지를 확인했다.
기존 v1 로그의 불변 보존·백업 편입은 완료하지 않았으며 TODO의 T04 잔여 항목으로 유지한다.

2026-10-10 13:52 KST에 일회 검증을 시작했다. 당시 `marketdata-backup.service`는
13:34:10부터 실행 중이며 원본 검증 단계였다. 13:35 restic 실행의 정상 종료와 전날
전체 검사 결과를 오늘 새 checkpoint의 저장·검사 근거로 사용하지 않는다.

Daily는 **14:42:05 KST 완료**했으며 13:34:10부터 약 67분 55초 걸렸다.
원본 검증 1,450,403건에서 실패 0건, checkpoint 57,423개 파일·39,645,876,117바이트이며,
backup ID는 `4381041be823394454ef9135162399d1540fa548025bfde05d791b250068a0de`다.
checkpoint 자체의 완료 시각은 **14:42:03 KST**, manifest의 checkpoint 기준 시각은 **14:36:03 KST**다.
14:35 restic 실행은 이 checkpoint가 완료되기 전이므로 새 사본 편입 근거로 사용하지 않았다.

## 실제 완료 흐름

아래 시각은 모두 2026-10-10 KST다. 예약 시각 대신 각 실행 receipt와 저장된 결과를 사용했다.

| 단계 | 실제 결과 |
|---|---|
| 예약 archiver | 15:05:01–15:12:54, `processed=1`, 성공 |
| 해당 checkpoint 업로드 | 15:10:06–15:11:34, 동일 backup ID의 snapshot 하나 확인 |
| 저장소 전체 읽기 검사 | 15:11:36–15:12:53, `check --read-data`, 종료 0, 대상 포함 15개 snapshot |
| T04 복원 시작 | 15:13:20, 예약 완료 근거와 입력 해시·잠금·공간 확인 |
| 명시적 snapshot 전체 추출 | 15:13:27–15:15:14, `restic restore <ID> --verify`, 종료 0 |
| 기존 프로그램 복원 | 15:24:15 완료, 57,423개 파일·39,645,876,117바이트, 검증 성공 |
| 두 collection 조회 대조 | 15:24:19 완료, checkpoint·복원본 모두 고정 기준과 일치 |
| 운영 상태 사후 확인 | 16:02:29, 배포 설정·boot·ledger·mask 동일, 두 supervisor 유지 |

정확한 restic snapshot ID는
`764f183c18d02a5c4d37ff13afa9fa08aed6d986cdd34c16158e7035497ea099`다.
예약 실행 ID는 `dde8d66427b041f08f3eba01021c75b8`, 연결된 업로드 attempt는
`88cd2c5478cf4dfc889080ec0d94b586`다. 대상 backup ID의 중복 snapshot은 없었다.
복원 중 archive state와 snapshot 목록이 유지됐으며 별도의 전체 검사나 새 백업을 추가 실행하지 않았다.

## 고정 대상과 완료 조건

수집 배포본은 `clock-evidence-policy-20261009-65296619a374c325`다.
[T11 고정 대조 목록](../t11-first-child-boundary-2026-10-09/backup-followup.json)의
두 종료 collection을 그대로 사용한다.

| 스트림 | collection | 조회 구간 | 보존할 과거 상태 |
|---|---|---:|---|
| Depth | `d2d18424592f4f549f5bda828e270759` | 3,553 | `partial`, reference 지연 1,950개 구간 |
| aggTrade | `c5b36646466344d5b1672275ffb2faf8` | 3,558 | `complete`, reference 지연 1,955개 구간 |

각 run의 근거 prefix 16개씩, 총 32개 artifact(600,444바이트)의 내용·출처·연결을 대조한다.
각 collection의 조회에 직접 사용되는 chunk는 14개씩이다.
`available`은 근거가 있다는 뜻이며 시각·시장 데이터의 정상 판정이 아니다.

두 저장소에서 32개 artifact의 바이트 해시·카탈로그 출처와 collection 보고서 해시가 고정 기준과 일치했다.
조회 결과 총 **7,111개 구간**과 boot/run/child·종료 경계가 일치했고 CLI는 모두 종료 0이었다.
Depth의 `partial`, 두 스트림의 과거 reference 지연 1,950/1,955개 구간도 그대로 복원됐다.
두 대상 조회의 unusable 구간은 각각 0개이며, 이를 다른 collection이나 모든 운영 시각의 품질로 확대하지 않는다.

| 스트림 | checkpoint·복원본에서 일치한 고정 조회 SHA-256 |
|---|---|
| Depth | `3a1a430fa28b71ffc72437fbfeaa95b4352ccfe84ebbeea2d36c86fcd2e60033` |
| aggTrade | `6a902e5ec7f4a2909178fe6ea551ac62505f484a23bcef4fbd54c98d425769a4` |

전체 파일 복원은 기존 프로그램의 검증 경로를 사용했고, 선택한 시각 근거 물리 파일 32개와 catalog 총 33개는
추출본과 복원본의 inode도 서로 다름을 확인했다. 전체 57,423개의 inode를 별도 전수 대조했다고 표현하지 않는다.

완료 조건은 오늘의 실제 완료 checkpoint, 동일 backup ID의 단일 restic snapshot,
그 snapshot을 포함하는 예약 `check --read-data` 성공, 명시적 snapshot 전체 추출,
기존 프로그램의 별도 저장소 복원, 고정 collection 조회의 의미 해시 일치다.
새 백업·추가 전체 검사·T03 보강분의 재검증은 하지 않는다.

## 실행과 보존 근거

- [고정 입력·기존 조회 해시 확인](preparation.json)
- [실제 복원·조회 완료 결과](execution.json), [프로그램 복원 receipt](application-restore.json)
- [예약 Daily](scheduled-daily.json), [예약 archiver](scheduled-archive.json), [전체 읽기 검사·원본 receipt 해시](scheduled-full-check.json)
- [snapshot 선택·목록](snapshot-inventory.json), [가장 최근 예약 작업 관측](latest-observation.json), [단계 관측 기록](observations.jsonl)
- [checkpoint의 선택 근거 대조](checkpoint-contents.json), [복원본의 선택 근거 대조](restored-contents.json)
- [운영 상태 사전 확인](host-before.json), [사후 확인·동일성 대조](host-after.json)
- [검증 스크립트](verify.py), [기존 읽기 전용 운영 점검 재사용](capture_host.py)

`verify.py await-execute`는 최장 3시간 동안 30초마다 완료 근거를 읽도록 실행했고 조건 충족 후
복원을 한 번 수행하고 종료했다. 새 타이머·서비스는 설치하지 않았다. 예약 작업 실패나 검증 불일치에서는
임의 재시도·수정 없이 중단한다. 원래 archive 잠금과 checkpoint 공유 잠금을 사용하며,
기존 source root를 덮어쓰지 않는 `/mnt/backup/marketdata-restic-restore/t04-clock-evidence-20261010-<id>/`
아래에 `extracted`와 `store`를 분리한다. 전체 snapshot 용량의 두 배와 기존 여유 공간 기준을 먼저 확인한다.

운영 수집·Chrony·RTC·정책·부팅 ledger와 자동 정리 mask를 변경하지 않는다.
기존 v1 로그 보존·백업 편입, 다른 업무의 시각 요구 분리, polling 변경, T05는 이번 완료 범위에 포함하지 않는다.

## 운영 유지와 남은 범위

사후 확인에서 두 supervisor는 10/9 19:33:46의 PID·invocation을 유지했고 child는 UID 1000의 고정 배포본으로 실행 중이었다.
Chrony도 기존 PID·invocation을 유지했다. boot ID·설정 파일 해시·ledger 전체·4개 mask는 사전 확인과 같았다.
수집·시각 서비스 재시작과 시각 제어는 수행하지 않았다. 자동 checkpoint 정리 service/timer는 계속 masked다.

16:02의 현재 시각 판정은 두 스트림 모두 `current_estimate_usable=true`, `raw_start_allowed=true`,
`hard_fault_seen=false`였다. reference age 약 620초와 조건부 오차 추정 약 125ms를 함께 기록했고,
과거 신선도 경고가 반영된 `clock_usable=false`도 그대로 보존했다. 이 값을 정상으로 덮어쓰지 않았다.

이번 시험 공간은
`/mnt/backup/marketdata-restic-restore/t04-clock-evidence-20261010-f71c5b20490248b4b229a66f3378ab10/`
아래 `extracted`와 `store`다. 두 사본의 파일 논리 크기는 합계 약 79.29GB이며 파일시스템 실제 점유량을 뜻하지 않는다.
삭제는 수행하지 않았고 기존 T06 삭제 승인을 재사용하지 않는다. 재현 근거 보존 후 이 정확한 경로의 정리 여부를 별도 검토한다.

신규 v2 근거의 운영 검증은 이 결과로 닫는다. 기존 v1 로그의 불변 보존·백업 편입은 미완료로
TODO에 유지하며, 과거 collection 연결을 추정해 만들거나 근거 없는 구간을 정상으로 처리하지 않는다.
