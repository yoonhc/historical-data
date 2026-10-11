# T05 배포 후보와 승인 경계

2026-10-11. 후보 `t05-clock-retirement-20261011-3618649f76cfd2f4`.
운영 설치·삭제·timer 활성화 승인서는 아니다.

## 고정 입력

- [manifest.json](manifest.json): 144개 소스 해시, 전체 목록 해시,
  [source.tar.gz](source.tar.gz)와 [candidate.patch](candidate.patch) 해시.
- 기준은 `checkpoint-automation-79e29a3ee5152872`의 138개 파일이다.
  운영 workspace 소스를 수정하지 않고 별도 작업 경로에서 구현했다.
- 변경 9개: `scripts/checkpoint_code.py`, `scripts/clock_retirement.py`,
  `scripts/retirement_clock.py`, `scripts/run_clock_retirement.py`,
  `scripts/render_retirement_units.py`, `scripts/restic_schedule.py`,
  `scripts/retire_checkpoints.py`, 신규 검사 두 파일.
- 기존 정리기의 계획 확장 hook과 실제 unlink/rmdir 직전 hook을 추가했다.
  기존 restic 예약 조회 함수는 명시적 system manager 선택을 추가하되 기본 user 동작을 유지한다.
  Collector·daily·backup·pack의 구현과 운영 실행본은 바꾸지 않는다.
- [deployment/](deployment/)의 설정·네 유닛은 렌더한 후보이며 설치하지 않았다.
  소스 해시와 별개로 실제 설정·유닛·실행 경로·UID를 새 승인 scope에 포함한다.

## 현재 승인된 관리자 시험

[system_preflight.py](system_preflight.py) `--check`는 입력 검증뿐이다.
`--run`은 `md-t05-preview-<nonce>`라는 `/opt`, `/var/lib` 시험 경로와
`/run/systemd/system`의 임시 서비스 하나만 만든다. 고정 소스와 검증한 비-editable
가상환경을 복사하고 권한을 확정한다. 기존 운영 유닛을 교체하지 않는다.

`User=yoonhc`, 빈 capability, `ProtectClock=yes`, `ProtectSystem=strict`,
`ProtectHome=read-only`, `PrivateDevices=yes` 등 후보와 같은 보호 조건 아래서
현재 system manager invocation/job, Chrony 읽기 전용 tracking, 기존 부팅 준비 공유 잠금,
시험 상태 파일 fsync, 실제 repository ID·snapshot 목록 조회, 삭제 없는 preview를 확인한다.
파일시스템 쓰기는 시험 상태·cache 및 기존 공통 lock/restic lock 경로로 제한한다.
checkpoint·원본·restic pack은 읽기 전용이다. 복사할 환경 파일은
[environment-files.json](environment-files.json)의 1,644개다.

당일 checkpoint/restic 전체 검사가 미완료이거나 잠금 경합이면 기존 조건대로 보류한다.
`access_passed`와 `preview_completed`를 분리한다. 전자가 성공해도 후자가 보류이면
전체 미리보기 완료로 표시하지 않는다. 보류를 해제하려고 예약 작업을 재시작하지 않는다.
정리 후보가 0개여도 실제 삭제 검증 완료로 세지 않는다.

시험 결과·관측·계획을 보고서에 보존한 뒤 시험 유닛·프로세스·파일만 정리한다.
133개 운영 파일과 Chrony·두 supervisor의 상태를 전후 대조한다.
시험의 `/opt`·`/var/lib` 경로는 최종 경로와 다르다. 실제 설치 이후에는 최종 경로의
소유권·해시·실효 유닛·mount 접근을 다시 확인해야 한다.

## 이후 운영 설치 범위 — 별도 승인 전 실행하지 않음

1. 위 후보만 `/opt/marketdata/releases/<release-id>`에 설치한다. 코드·환경은 root 소유,
   그룹/기타 쓰기 금지로 확정하고 실제 import를 대조한다.
2. `/etc/marketdata/checkpoint-clock-retirement/`에 settings, policy, clock binding,
   네 유닛의 검토본을 둔다. archive profile과 복구 receipt는 기존 경로를 읽는다.
   기존 승인 파일은 변경하지 않는다. 새 recurring/manual 승인 파일은 **enabled=false**로 만든다.
3. 새 상태는 `/var/lib/marketdata-checkpoint-retirement`다. 기존 과거 정리 원장·plan은
   읽기 전용으로 함께 대조한다. 승인된 `.automatic.retiring` staging 경로와 lock 접점을
   확인한다. 없으면 설치 범위에서 명시적으로 만들고 기록한다.
4. 시스템 유닛을 설치해도 자동 service/timer는 계속 persistent masked로 유지한다.
   기존 사용자 service/timer도 `/dev/null` mask를 유지한다. 첫 preview만 수동 요청한다.
5. 최종 설치 경로에서 생성한 새 scope·새 plan의 정확한 후보·보호 목록을 제출한다.
   임시 시험 경로에서 만든 plan이나 과거 수동·반복 승인은 이 단계의 권한이 아니다.

새 근거는 검증 완료 사본을 현재 정상 시각에 확인한 상한이다. 초기에는 **추가 72시간**
보호될 수 있다. 근거 없이 원래 `completed_at`의 정확도를 소급 인정하지 않는다.

## 첫 수동 삭제와 반복 자동화 — 각각 별도 승인

고정 실행본에서 `propose-manual --plan-id <64자리 ID>`로 disabled 요청을 출력한다.
관리자는 정확한 scope·plan·현재 boot를 대조한 뒤 별도 승인 내용을 root 소유
`manual.json`에 확정한다. 실행은 최종 시스템
`marketdata-checkpoint-retirement-apply@<PLAN_ID>.service`로만 한다.
수동 승인을 recurring 파일에 복사해도 거부한다.

첫 실제 삭제 후 keep 파일 검증·후보 소멸·journal·원본/restic 보존을 대조한다.
`deleted_this_run=0` 또는 `already_complete=true`를 새 삭제 성공으로 세지 않는다.
그 결과를 확인한 뒤 **새 recurring 승인과 15:40 KST 시스템 timer 활성화를 별도로 승인**한다.
`Persistent=false`, `Restart=no`이며 잠금/선행 작업/시각 보류는 다음 날 평가한다.
같은 부팅의 시각 제어 권한이나 Chrony/RTC 설정은 변경하지 않는다.

## 중단과 되돌리기

자동화 설치 후 새 자동 실행만 중지하는 명령은 다음과 같다. 현재 실행하라는 지시가 아니다.

```sh
sudo systemctl disable --now marketdata-checkpoint-retirement.timer
sudo systemctl mask marketdata-checkpoint-retirement.timer marketdata-checkpoint-retirement.service
```

timer 중지는 이미 실행 중인 서비스의 종료를 뜻하지 않는다. 실제 삭제 실행 중이면
새 기동부터 막고 상태를 확인한다. 즉시 중단이 필요한 경우에 한해 해당 retirement 서비스만
정상 중지하고 남은 journal을 점검한다. system manager 전체나 collector를 중지하지 않는다.
기존 사용자 유닛은 unmask하지 않는다.

중단된 계획은 자동 재개·새 자동 계획을 막는다. 원래 plan/journal·이미 지운 파일·staging을
확인하고 현재 부팅에서 다시 점검한 뒤, `propose-manual --plan-id <ID> --resume-interrupted`의
별도 승인을 받아 그 계획의 나머지만 진행할 수 있다. 과거 실패·시각 이력은 남는다.
ledger/journal 삭제, 새 plan으로 갈아타기, 승인 budget 초기화로 통과시키지 않는다.

코드 원복은 새 timer를 중지·masked로 둔 상태에서 한다. 구형 자동 정리를 재활성화하지 않는다.
새 완료 상한·계획·journal·승인·시각 기록은 삭제하지 않고 보존한다. 이미 지운 checkpoint는
코드 원복으로 돌아오지 않으며 필요 시 기존 restic 복원 절차를 사용한다.

새 control state는 아직 기존 checkpoint의 자동 백업에 편입됐다고 주장하지 않는다.
근거가 유실되면 72시간을 새 관측부터 다시 보호하고, 삭제 journal 유실로 로컬 누락을
설명할 수 없으면 기존 audit가 보류한다. 실제 설치·삭제 승인 기록과 함께 control state의
불변 사본을 보존해야 하며, 시장 원본/restic 자체의 보존 정책은 바꾸지 않는다.

## 범위 밖

다른 예약 업무의 시각 요구 분리·system manager 이전, v1 시각 로그 편입, T13 관찰,
Chrony 시간원/maxpoll 변경, 원본·편입 전 사본·시험 공간·복구 키트 삭제,
restic forget/prune은 포함하지 않는다.
