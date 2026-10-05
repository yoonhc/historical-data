# 작업용 checkpoint 자동 정리 활성화

사용자의 **“오케이 승인할게”**를 바로 앞서 제시한 고정 배포본·반복 삭제 범위·15:40 KST 타이머의
승인으로 기록하고, **2026-10-05 19:38:43 KST에 활성화했다.**
첫 실제 예약은 **10/6 15:40:00 KST**다. 19:53 사후 조회까지 새 정리 서비스 실행·운영 삭제는 0건이다.

## 승인과 적용 범위

| 항목 | 결과 |
|---|---|
| 배포본 | `checkpoint-automation-79e29a3ee5152872` |
| 코드 ID | `283dfc4596ccc8ffe81aab9a2b6e756bd5b835ad011bb82a96d51d674838456b` |
| 반복 승인 scope ID | `8e1b3059c148f2ca235ec44faf317f8d680f5a846d4445327363534cfb297c5b` |
| 실행 사용자 | `yoonhc`, UID/GID 1000 |
| 보호 조건 | 검증된 최근 3개 + 완료 후 72시간 미만 사본의 합집합 |
| 반복 승인 파일 | `/home/yoonhc/.config/marketdata/checkpoint-retention-authorization.json`, 사용자 소유 0600 |
| 반복 권한 | `enabled=true`, 이번 사용자 승인 시각·근거 연결 |
| timer | `marketdata-checkpoint-retirement.timer`, enabled / active / waiting |
| service | `marketdata-checkpoint-retirement.service`, static / inactive / 실행 이력 없음 |
| 예약 | `OnCalendar=*-*-* 06:40:00 UTC`, `Persistent=false`, `Restart=no` |

[승인 기록](approval.json)은 원래 [검토 문서](../checkpoint-automation-2026-10-05/approval-review.md)와
[후보 보고서](../checkpoint-automation-2026-10-05/summary.md)의 경로·해시를 포함한다.
기록 시각은 승인을 파일로 남긴 시각이며 사용자 메시지의 도착 시각을 추정한 값이 아니다.
승인 전·후 내용은 [비활성 사본](authorization-before.json), [활성 사본](authorization-enabled.json)에 있다.
scope 내용은 변경하지 않았고 기존 수동 계획의 승인을 재사용하지 않았다.

원래 후보의 코드·정책·프로필·경로·scope를 유지했다. 사본은
`/mnt/backup/marketdata-historical/automatic`의 승인된 입력 범위만 대상으로 한다.
원본·편입 전 사본·restic 지점·시험 공간·복구 키트는 제외하며 `forget`·`prune`은 포함하지 않는다.
내일의 후보·보호 목록은 새로 계산한다. 오늘 18:44 미리보기 계획을 예약 적용에 넘기지 않았다.

## 설치 전 점검

[사전 점검](preflight.json)에서 배포본 138개 파일과 패치·묶음 해시, 설정 4개의 내용·소유자·권한을
확인했다. 새 유닛은 설치 전 없었고 기존 유닛을 덮어쓰지 않았다. 원래 수집·백업 실행 소스 94개는
변경하지 않았다. 승인 범위를 새로 계산한 결과도 기존 scope ID와 일치했다.

- 유닛 구문: 설치 전과 설치 후 `systemd-analyze --user verify` 통과.
- 사용자 서비스 지속: [loginctl 조회](login.json)의 `Linger=yes`. 재부팅·로그아웃 시험은 하지 않았다.
- 실행 환경: 승인된 service의 EnvironmentFile·작업 경로·환경·권한·우선순위·제한을 사용한
  **별도 임시 oneshot 시험 서비스**에서 읽기 전용 probe를 실행했다.
- Python: `/home/yoonhc/projects/algoTrade/historical_data/.venv/bin/python`에서 승인된
  `release/scripts/checkpoint_schedule.py`를 가져오는 것을 확인했다.
- 프로필·암호: 설정 경로·소유자·권한 검사와 실제 restic 저장소 해독·메타데이터 조회 성공.
  암호 값은 로그나 보고서에 남기지 않았다.
- 마운트: `/mnt/backup`, UUID `d90aaf81-2358-411f-8022-4e1a3f7b0973` 일치.
- 저장소 ID: `b5a4434c95fcf4a9eb1339689cdea42c1da08bd34b845193dffce2aa8203e558` 일치.

[시험 서비스 결과](service-preflight.json)는 실제 사용자 manager의 invocation ID와 환경을 남긴다.
이 시험은 정리 runner의 `run`·`apply`를 실행하지 않았다. 실제 삭제 서비스의 첫 예약 성공으로 세지 않는다.
오늘 daily → 같은 backup ID의 restic 저장·전체 검사 receipt도 다시 연결했다. 전체 읽기 검사나
유지 사본의 적용 직전 전체 파일 검사를 새로 실행한 것은 아니다.

사전 점검 보조 스크립트의 첫 시도는 `loginctl`에 쉼표로 묶은 속성 인자를 주어 빈 출력을 받았고
UID 확인에서 중단됐다. [최초 근거](preflight-attempt-1/failure.json)를 보존했다.
개별 `--property` 인자로 수정한 점검은 통과했으며, 첫 시도 당시에는 승인 파일·유닛을 변경하지 않았다.
이 수정은 배포본의 자동 정리 코드 변경이 아니다.

## 활성화와 사후 확인

승인 파일의 범위를 유지하면서 권한·시각·근거만 기록한 뒤, 검증된 service/timer 2개를
`/home/yoonhc/.config/systemd/user/`에 새로 설치했다. daemon reload 후 설치 파일과 배포본의
바이트 일치·drop-in 없음·유효 설정을 확인하고 **timer만 enable --now** 했다.

[활성화 결과](activation.json)와 [19:53 사후 대조](post-activation-verification.json)는 다음을 확인한다.

- 다음 예약: **2026-10-06 15:40 KST**, 아직 `LastTriggerUSec` 없음.
- 새 정리 service: `MainPID=0`, 시작 시각 없음. 표시되는 기본 `Result=success`는 실행 성공 증거가 아니다.
- 기존 유닛 파일 28개·운영 소스 94개·사본 manifest 7개·기존 정리 journal 유지.
- restic snapshot ID 9개 보존. 새 저장·삭제·복구·전체 읽기 검사를 실행하지 않음.
- 활성 승인 파일을 실제 코드로 다시 검사해 승인 범위 일치 확인.

수집 서비스를 재시작하지 않았고 기존 daily·restic·pack의 예약 설정을 바꾸지 않았다.
후보의 69개 격리 시험 근거는 유지하며, 동일 코드를 이유 없이 다시 전수 시험하지 않았다.
설치 보조 스크립트의 lint와 문서·배포본 대조 결과는 [최종 검증 기록](final-validation.json)에 남긴다.

## 상태 조회와 새 예약만 중지

```bash
systemctl --user status marketdata-checkpoint-retirement.timer
systemctl --user status marketdata-checkpoint-retirement.service
journalctl --user -u marketdata-checkpoint-retirement.service -n 80 --no-pager
```

실제 평가 기록은
`reports/restic-archive-runs/checkpoint-scheduled/<run_id>/run.json`에 남는다.
`no_candidates`·`deferred`·`failed`·`complete`와 정확한 계획·후보·보호 목록을 확인한다.
보류 종료 코드 75도 systemd에서는 success이므로 서비스 종료 코드만으로 정리 완료를 판단하지 않는다.
잠금 경합이나 당일 daily/restic 미완료이면 보류하고 다음 날 다시 평가한다.
미완료 정리가 있으면 점검 필요로 남기며 새 계획을 만들거나 자동 재개하지 않는다.

**새 정리 예약만 중지**하려면 다음을 실행한다.

```bash
systemctl --user disable --now marketdata-checkpoint-retirement.timer
```

이미 실행 중인 정리는 계속될 수 있다. 진행 중 작업 중단이 별도로 필요하면
`systemctl --user stop marketdata-checkpoint-retirement.service`를 사용한 뒤 미완료 journal을 점검한다.
이 명령은 기존 수집·daily·restic 예약을 중지하거나 이미 지운 사본을 복원하지 않는다.

## 아직 미완료인 운영 관찰

[관찰 대기 기록](observation.json)에 다음을 별도로 남겼다.

1. 10/6 15:40 이후 첫 실제 timer 출처·새 plan ID·당일 daily/restic 연결·정리 결과 확인.
2. 최초 예약 정리가 실제 완료되면 정확한 후보·보호 사본과 restic 보존, 미완료 journal 유무 확인.
3. 그 정리 뒤 **다음 새 daily → 동일 backup ID의 예약 restic 저장·전체 검사** 확인.

현재 예약 실행 수·운영 삭제 수는 모두 0이다. 사전 probe·과거 미리보기·처리 0개 탐색을
정리 완료로 세지 않으며, 기존 restic 초기 관찰 3/3과도 구분한다.
