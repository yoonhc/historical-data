# 운영 전환 전 데이터 갱신·복구 지점 확인 — 2026-10-07

**기존 daily update의 미완료 4종을 해소했고, 기존 최신 checkpoint와 restic 사본의 연결·검증 근거를 확인했다.**
고정 후보 `system-bootstrap-20261007-f85eb8321a19c1ee`와 운영 설정은 유지했다.
실제 운영 전환·시각 변경·수집 중지·재부팅은 수행하지 않았다. 아래 시각은 별도 표시가 없으면 KST다.

## 데이터 갱신 결과

12:27:51 종료한 기존 `marketdata-daily.service`는 10/6 UTC 선물 자료 4종이 `waiting_source`였고,
오류 배열은 비어 있었다. 미완료 결과의 종료 코드 2와 systemd 시작 제한 상태를
[기존 보고서 사본](daily-latest.json), [실행 전 서비스 상태](before-update-services.json)에 보존했다.
이는 데이터 갱신 작업의 미완료이며, checkpoint 생성·restic 저장 실패와 구분한다.

23:27:45 공개 여부 확인에서 정확한 대상 ZIP 4개와 각 CHECKSUM 4개가 모두 HTTP 200으로 응답했다.
[원격 응답 근거](remote-availability.json)는 URL·크기·Last-Modified·상태를 기록한다.
Last-Modified를 최초 공개 시각으로 단정하지 않는다. 현재도 미제공인 상태가 아님을 확인한 뒤 기존 갱신 경로를 실행했다.

| 10/6 UTC 누락 대상 | 해소한 행 | 재실행 후 누락 격자 |
|---|---:|---:|
| BTCUSDT 선물 거래 봉 1m | 1,440 | 0 |
| mark price 봉 1m | 1,440 | 0 |
| index price 봉 1m | 1,440 | 0 |
| OI archive 5m | 288 | 0 |
| 합계 | **4,608** | **0** |

- 실행: 기존 `marketdata-daily.service`를 `start --no-block`으로 한 번 요청.
- 실제 처리: **23:31:47–23:31:54**, `Result=success`, `ExecMainStatus=0`, `NRestarts=0`.
- invocation: `d2f9726d44944831bb651708dbc3032b`.
- [재실행 보고서](daily-after-update.json)의 8개 대상 모두 `state=complete`, `complete=true`, `errors=[]`.
- [해당 invocation journal](update-journal.json)의 결과와 저장 보고서가 일치한다.
- `reset-failed`, 재시도 정책·타이머 변경은 하지 않았다.

4,608은 기존 누락을 해소한 행 수다. 기존 설정에 따른 최근 3일 재조회와 현재 metadata 관측도 함께 수행했다.
완료 판정은 이번 갱신 요청과 각 데이터의 기존 의미에 한정한다. 봉·OI의 조회 범위는
`[2026-09-14T00:00Z, 2026-10-07T00:00Z)`이며, OI는 source label grid,
funding은 source query coverage, metadata는 현재 관측이라는 구분을 유지한다.
모든 과거 데이터·실시간 호가의 품질 문제가 해소됐다는 뜻은 아니다.

## 확인한 기존 복구 지점

| 항목 | 확인된 값 |
|---|---|
| checkpoint 기준 시각 | **10/7 13:58:24.506151** (`checkpoint_finished_at`) |
| checkpoint 완료 | 10/7 14:03:06.062949 |
| 정기 maintenance 완료 | 10/7 14:03:08.041295, `ok=true` |
| restic 입력 검증 완료 | 10/7 14:08:52.303206 |
| restic 저장 완료 | 10/7 14:09:59.117960 |
| 기존 전체 읽기 검사 완료 | **10/7 14:10:53.479568**, `check --read-data`, exit 0 |
| 이번 live repository 대조 | 23:27, 통과 |

- backup ID: `1ac0768ac6f55ebbc5fc5daccae5e531b267210daef752683bb7601a690ecab2`
- snapshot ID: `12ee905237a76788c93a9963c2c0425739042c57e49effe73135b90d2eee377f`
- checkpoint 경로: `/mnt/backup/marketdata-historical/automatic/2026-10-07/358aedceb5a54aee9bea0bf5f59ca92d`
- manifest SHA-256: `c30105ac5130f4f35ec24a4e5e64b3e83fa6d103a4044da7e45d54596643bdcd`
- restic 저장소 ID: `b5a4434c95fcf4a9eb1339689cdea42c1da08bd34b845193dffce2aa8203e558`
- scope: `historical-data-original-v1`.

[backup-readiness.json](backup-readiness.json)에 다음 근거를 연결했다.

1. 기존 `Profile.guard()`의 마운트·UUID·암호 파일·용량 검사와 기존 repository/shared checkpoint 잠금을 사용했다.
2. 정기 maintenance의 source 논리 검사·백업 검증 성공 및 동일 backup ID를 확인했다.
3. 현재 checkpoint의 manifest와 catalog 파일 해시를 대조했다.
4. 실제 restic 저장소 설정 ID, snapshot 11개의 목록, 최신 지점의 태그·기준 시각·입력 경로를 확인했다.
5. 실제 snapshot의 `/backup.json`을 읽어 로컬 manifest와 일치함을 확인했다.
6. 기존 입력 검증 결과와 전체 읽기 검사 receipt·stdout·stderr를 대조했고, 검사 대상 11개가 현재 목록과 일치했다.
7. 확인 전후 archive 처리 상태 파일의 해시가 같았다. 확인 시 미처리 사본·미해결 오류·미검사 지점은 없었다.

이번 작업에서 전체 읽기 검사를 새로 수행한 것은 아니다. **14:10의 전체 검사 근거와 23:27의 현재 메타데이터 대조**를 구분한다.
새 백업·복원·forget/prune도 실행하지 않았다. `processed=0`인 정기 탐색은 새 복구 지점이나 새 전체 검사로 집계하지 않는다.

## 현재 백업의 시점 한계와 다음 순서

**이 사본은 13:58 기준이므로 23:31에 새로 채운 4종과 그 이후 수집 자료는 포함하지 않는다.**
백업 자체의 정상 여부와 최신 수집분의 편입 여부는 별개다. 현재 사본이 손상됐다는 뜻은 아니며,
이번 갱신 후의 저장소를 대상으로 새 checkpoint를 만든 것은 아니다.

23:40 실제 타이머 조회 결과 다음 정기 checkpoint 작업은 **10/8 13:10**이다.
그 실행이 완료된 뒤 동일 backup ID의 예약 restic 저장·전체 검사를 확인하면 갱신분을 포함한 근거가 생긴다.
다음 예약 시각만으로 완료를 가정하지 않는다. 그보다 먼저 전환할 경우에는 작업창 준비 단계에서
기존 백업 경로로 새 checkpoint를 생성·검증하고, 해당 지점의 restic 저장·검사를 확인한다.
30분 주기의 restic 탐색만 실행해도 새 checkpoint가 만들어지는 것은 아니다.

그다음 [고정된 운영 전환 범위](../system-runtime-root-preflight-2026-10-07/transition-review.md)에 따라
Linux 보류 완료 → 사용자 Windows 작업 → Linux 복귀·RTC 확인 → 패키지/고정 배포본 설치 →
최종 부팅·실제 수집 확인 순으로 진행한다. Windows 설정·시각/RTC 변경·수집 공백·재부팅은 별도 승인 대상이다.
Windows 작업이 끝났거나 다음 OS에서 작업 세션이 자동으로 이어진다고 가정하지 않는다.

## 운영 보존 확인

[followup-validation.json](followup-validation.json)의 **23:40:19** 최종 확인은 모두 통과했다.

- 고정 후보 관련 파일 198개, 운영 소스/설정 139개, 설치된 user unit 20개의 해시 유지.
- root 사전 점검 당시의 시스템 설정·마운트 관련 프로필·정리 grant·unit 활성화 상태 유지.
- depth·aggTrade의 PID, invocation, 시작 시각이 갱신 전후 동일. 수집 종료·재시작을 요청하지 않았다.
- 같은 부팅이며 timesyncd는 기존 실행 유지, Chrony 미설치. `NTPSynchronized=yes`, `LocalRTC=yes`, `Asia/Seoul` 유지.
- 운영 데이터와 daily 실행 상태/보고서는 의도대로 갱신됐고, 과거 실패 근거는 별도 사본에 보존했다.
- **자동 checkpoint 정리는 이번에 중지하지 않았다.** 기존 timer는 active/enabled이며 다음 예약은 10/8 15:40이다.
  실제 전환 창에서는 이를 보류하고, 상시 시각 검사 후보가 별도로 검증·승인될 때까지 재개하지 않는 기존 전환안을 유지한다.

수집·시각 관리 후보의 개발이나 root 접근 시험을 반복하지 않았다. 운영 전환 준비 상태 확인과 기존 갱신 작업의 누락 해소를 수행한 기록이다.
