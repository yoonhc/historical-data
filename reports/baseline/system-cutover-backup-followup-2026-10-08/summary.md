# 정기 checkpoint → restic 후속 확인 — 2026-10-08

**어젯밤 보강한 10/6 UTC 자료 4종이 오늘 새 checkpoint와 restic snapshot에 포함됐음을 확인했다.**
기다리던 새 정기 사본의 생성·검증·예약 보관·전체 읽기 검사 확인은 완료다.
이번 실제 점검은 21:23–21:28 KST에 수행했다. 아래 시각은 모두 KST다.

| 단계 | 실제 결과 |
|---|---|
| 정기 백업 시작 | 10/8 13:10:00, `marketdata-backup.service` |
| checkpoint 기준 시각 | **14:03:03.975956**, `checkpoint_finished_at` |
| checkpoint 생성·검증 완료 | 14:08:09.637593 |
| maintenance 정상 종료 | **14:08:12**, exit 0, 재시도 0 |
| 해당 사본을 처리한 restic 예약 | **14:35**, `verified_timer`, `processed=1` |
| restic 저장 완료 | **14:40:27.537390** |
| 전체 읽기 검사 완료 | **14:41:27.091507**, `check --read-data`, exit 0 |

14:05 탐색 시에는 checkpoint가 아직 완료되지 않았다. 그다음 14:35 예약이 완료된 사본을 처리했다.
이후 `processed=0` 탐색은 새 백업이나 새 전체 검사로 세지 않는다.
[예약 journal](scheduled-journal.json)·[해석한 실행 기록](scheduled-events.json)과
[기존 처리 receipt 및 현재 저장소 대조](backup-readiness.json)를 연결했다.

## 정확한 복구 지점

- backup ID: `ccf70014d48549bd22e801b135680ef4b6e5040e236688ed78374aa987745935`
- snapshot ID: `c17a01a46e723b87f0c3c48403bf76e742c014b4682d02644416e50924324fd5`
- checkpoint: `/mnt/backup/marketdata-historical/automatic/2026-10-08/ce63ff231fb440b68972ea702382c1e5`
- manifest SHA-256: `70941188298499a536efe9a93541fcbadec682ddd6659e4082731b6269164d75`
- archive run: `8654157ac3724b7a9787831c020cf11b`
- 예약 invocation: `95db7e74b1b146a1ac1caacb4ae607c6`
- scope: `historical-data-original-v1`

기존 profile guard·repository lock·shared checkpoint lock을 사용했다.
마운트/UUID·저장소 ID·snapshot 태그/시각/경로·암호화 snapshot의 manifest·현재 catalog 해시를 대조하고,
기존 입력 검증과 전체 읽기 검사 receipt/stdout/stderr가 이 snapshot을 포함함을 확인했다.
현재 scope의 snapshot 12개와 검사 대상 목록이 일치했고, 미처리 사본·미해결 오류·미검사 지점은 없었다.
확인 전후 archive 상태 파일의 해시는 동일했다.

## 어젯밤 보강 자료의 실제 포함 확인

[10/7 갱신 근거](../system-cutover-readiness-2026-10-07/followup-validation.json)의 정확한 revision ID 4개를 사용했다.
checkpoint catalog는 immutable/read-only로 열고, 해당 revision과 active 연결을 확인했다.
명시적인 위 snapshot ID에서 네 Parquet 파일을 `restic dump`로 읽고 로컬 사본과 바이트·SHA-256을 대조한 뒤 행 수·시각 범위를 읽었다.

| 10/6 UTC 자료 | 행 수 | 첫 시각 → 마지막 시각(UTC) | 결과 |
|---|---:|---|---|
| 선물 trade bars 1m | 1,440 | 00:00 → 23:59 | 일치 |
| mark price bars 1m | 1,440 | 00:00 → 23:59 | 일치 |
| index price bars 1m | 1,440 | 00:00 → 23:59 | 일치 |
| OI archive 5m | 288 | 00:00 → 23:55 | 일치 |

**합계 4,608행**이 새 사본에 들어 있다. 구체적인 revision·파일 해시·dump receipt는
[backup-readiness.json](backup-readiness.json)의 `previous_update_inclusion`에 기록했다.
이것은 선택한 네 파일의 실제 내용 대조이며, 전체 프로그램 복원·재백업 시험을 다시 수행한 것은 아니다.
21시 점검에서 전체 읽기 검사를 새로 반복하지 않았고, 정기 작업의 14:41 검사 근거를 사용했다.

## 별도로 남은 오늘의 데이터 갱신

[오늘 daily 보고서](daily-latest.json)는 **10/7 UTC** 선물 trade/mark/index/OI 4종의 `waiting_source`를 남겼다.
12:25:53 실행은 미완료 계약에 따라 exit 2로 종료했고, 오류 배열은 비어 있었다.
이번에 복구 지점 포함을 확인한 **10/6 UTC** 자료와 다른 날짜다.

[현재 원격 확인](remote-availability.json)에서는 이 새 날짜의 ZIP 4개와 CHECKSUM 4개가 모두 HTTP 200이다.
아직 기존 updater가 다시 처리하지 않은 상태이며, 이번 점검에서는 갱신 서비스를 재실행하지 않았다.
오늘 checkpoint도 생성 당시의 이 미완료 상태를 포함한다. 새 날짜가 빠졌다는 사실을 백업 자체의 실패와 혼동하지 않는다.

전환 작업창에서는 기존 경로로 당시 공개된 누락 자료를 보강하고, 필요한 최신 복구 지점 확인을 마친 뒤 진행할 수 있다.
매일 새 날짜가 생길 때마다 다음 날 예약까지 기다리는 것을 새로운 전환 조건으로 삼지 않는다.
수집이 계속되는 동안 checkpoint 기준 시각 뒤의 자료가 생기는 것은 별도로 기록한다.

## 기존 운영과 자동 정리

[서비스 관측](service-observation.json)에서 depth·aggTrade는 실행 중이며,
고정 후보 198개·운영 소스/설정 139개·설치 user unit 20개의 해시는 그대로다.
Chrony 미설치·timesyncd 사용, `LocalRTC=yes`, `NTPSynchronized=yes`, KST 설정을 유지했다.
이번 작업에서 서비스 시작/중지·시각/RTC 변경·재부팅·운영 전환은 수행하지 않았다.

기존 승인에 따른 **15:40 자동 checkpoint 정리는 15:53:07에 정상 완료**됐다.
10/5 작업용 checkpoint 1개가 정리됐고, 해당 restic 지점을 포함한 전체 12개 snapshot은 현재 대조에서 존재했다.
정리 run은 `943e512a1a084ab5b4c99f6528a425f4`, plan ID는
`abbfbd17bbe5e68d764cfc9a7cc90620cc60d6f44faaa3430887db5c1f35534c`이며 `needs_attention=false`다.
이것은 기존 예약의 관측 결과이고 이번 점검에서 삭제를 요청한 것이 아니다. 현재 정리 timer는 계속 enabled 상태다.

다음 단계는 [고정된 운영 전환 범위](../system-runtime-root-preflight-2026-10-07/transition-review.md)의 작업창을 정하는 것이다.
Windows UTC 설정·Linux RTC 전환·패키지 교체·수집 공백·재부팅은 별도 승인 범위이며,
실제 전환 시 자동 정리를 보류하고 상시 시각 검사 검증·승인 전에는 재개하지 않는 방침을 유지한다.
