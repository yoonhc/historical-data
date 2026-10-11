# T05 근거 생성 기능 적용·원복 절차

2026-10-11. 고정 후보 `t05-completion-evidence-20261011-c9d413cfe11ec0e7`.
[사용자 승인 범위](authorization.md)를 설치와 활성화 두 단계로 수행한다.
본 문서는 실제 성공 결과를 대신하지 않는다. 현재 단계별 상태는 [status.md](status.md)를 따른다.

## 1. 관리자 설치·등록

`install_evidence.py --check`는 입력·기존 운영 파일/유닛을 읽기 전용으로 대조한다.
소유권은 도구의 user namespace가 아닌 **실제 호스트**에서 수집한 기준을 사용한다.

`install_evidence.py --run`의 정확한 쓰기 대상:

| 경로 | 내용 |
|---|---|
| `/opt/marketdata/releases/t05-completion-evidence-20261011-c9d413cfe11ec0e7` | 검증한 소스 151개와 비-editable 환경 1,646개를 내용 해시로 복사. 모든 소스/일반 환경 파일 0644, bin 파일 0755, root 소유 |
| `/etc/marketdata/checkpoint-clock-retirement` | 고정 settings·policy·clock binding·유닛·두 user drop-in 원본·disabled manual 승인. recurring은 설치된 scope로 생성하며 enabled=false |
| `/var/lib/marketdata-checkpoint-retirement` | UID/GID 1000, 0700. baseline·사후 관측·불변 객체·등록 receipt |
| `/var/lib/marketdata-t05-evidence-deploy-20261011` | root 소유 설치 코드·입력·명시적 등록 승인·전후/명령/결과 기록 |
| `/etc/systemd/system/marketdata-checkpoint-retirement.service`, `.timer` | `/dev/null` 링크. 자동 실행 계속 masked |
| `/etc/systemd/system/marketdata-checkpoint-retirement-preview.service`, `-apply@.service` | `/etc/marketdata/checkpoint-clock-retirement/units/`의 고정 유닛 링크. 시작·enable하지 않음. apply 승인도 disabled |

공개키/패키지 다운로드·uv 재설치·시각 서비스 변경은 없다. 실제 실행하는 두 entrypoint는
`python -B -I`로 고정 소스 경로를 사용한다. 복사한 환경의 console script를 다른 경로의
패키지 관리 명령으로 사용하지 않는다. installed import와 source entrypoint import를 각각
일반 계정으로 대조한다. 개발 cache의 쓰기 권한을 복사하지 않는다.

최종 경로에서 읽기 전용 inventory를 만들고, 승인한 7개 ID/manifest와 일치할 때만 root 소유의
새 `registration-approval.json`을 만든다. 승인 purpose는 `checkpoint-evidence-registration-v1`,
action은 `register`, deletion_authority=false다. 등록은 일반 계정으로 기존 maintenance/repository/
checkpoint/registry 잠금 아래 수행한다. 새 baseline과 각 최초 사후 상한을 다시 읽어 검증한다.
조회 실패 항목은 missing으로 남으며 완료 시각을 소급 생성하거나 자동 재등록하지 않는다.

이 단계는 운영 상태 쓰기이며, 삭제 없는 이전 미리보기와 구분한다. 새 근거는 아직 다음
checkpoint/restic으로 보존되기 전이다. 등록 완료만으로 정리 후보로 취급하지 않는다.

관리자 단계가 끝나도 기존 사용자 backup/restic 실행 명령은 그대로다. 실행 중인 업무를
종료하지 않는다. root 결과의 `installed_pending_user_entrypoint_activation`을 확인한다.

## 2. 기존 예약의 실행 명령 전환

`activate_evidence.py --run`은 yoonhc 계정으로 다음 두 새 파일만 게시한다.

- `~/.config/systemd/user/marketdata-backup.service.d/80-checkpoint-evidence.conf`
- `~/.config/systemd/user/marketdata-restic-archive.service.d/80-checkpoint-evidence.conf`

둘 다 기존 ExecStart를 비운 후 고정 배포본의 해당 entrypoint로 지정한다. 기존 base 링크를
교체하거나 `systemctl disable`을 사용하지 않는다. root 시작 검사 drop-in의 전체 ExecStartPre,
예약 시각·timer 활성/enable 상태·Restart·종료 제한·보호 옵션은 그대로 대조한다.
user daemon-reload는 두 파일을 완성해 게시한 뒤 한 번 수행한다. 새 backup/run을 요청하지 않는다.

실제 backup/archiver가 실행 중이거나 ExecStartPre 프로세스가 있으면 변경 없이 보류한다.
inactive·PID 0인 채 기존 time gate의 선행 작업을 기다리는 start job은 업무 실행과 구분해 기록한다.
그 대기를 취소하거나 강제로 통과시키지 않는다. 전환 후 실제 시작 시에도 기존 검사가 적용된다.
전환 도중 자연스럽게 예약 업무가 기동하면 그 invocation은 기록하고 강제 종료하지 않는다.

두 파일 중 일부만 게시됐거나 유닛 확인이 실패하면, 도구가 만든 내용이 정확히 같은 파일만
제거해 기존 명령을 복원한다. 예상하지 못한 변경 파일은 자동 제거하지 않고 오류로 남긴다.
설치 코드·근거·실패 기록은 유지한다.

## 중단과 원복

| 지점 | 처리 |
|---|---|
| 설치 입력 불일치 | 운영 쓰기 전 중단. 실제 차이 확인 후 새 기준의 근거 기록 |
| 파일 설치 중 실패 | 최초 result/commands와 부분 경로 보존. installer 전체를 무작정 재실행하지 않음 |
| 최종 inventory가 승인한 7개와 다름 | 등록 전 중단. 설치 자체와 등록 미완료를 구분하고 새 목록 검토 |
| baseline/일부 상한만 생성 | 불변 객체 보존. 아직 없는 근거만 별도 observe 검토; baseline 삭제·덮어쓰기 금지 |
| 설치 완료·활성화 전 | 기존 명령으로 운영 지속. 설치된 새 근거가 옛 코드에서 자동 갱신된다고 보지 않음 |
| 활성화 실패 | 정확히 이번에 게시한 두 drop-in만 원복하고 user manager 재조회 |
| 활성화 이후 원복 필요 | 유휴 상태에서 `rollback_evidence.py`로 검토 후 `--run`. 두 명령만 원복, 자료·registry·sidecar·승인/실패 기록·고정 release 유지 |

원복 도구는 새 코드로 진행 중인 backup/archiver를 중단하지 않는다. 구형 archiver에는 새 T05
근거 보존 receipt의 추가 기능이 없으므로 원복 뒤에는 새 근거가 계속 축적된다고 표시하지 않는다.
정리 mask는 유지하고 과거 반복 grant로 우회하지 않는다.

## 다음 정기 실행에서 확인할 것

실제 backup.json 완료 확정 후의 관측·생성 선후관계 → 동일 backup ID의 sidecar → restic snapshot
bytes/receipt·전체 검사 → 별도 경로 복원 조회를 연결한다. 설치 결과로 이를 완료 처리하지 않는다.
기존 사본의 최초 유효 사후 상한부터 72시간+60초 및 후행 검증 사본 3개가 필요하다.
근거 보존/복원까지 실제 확인한 뒤 새 삭제 미리보기와 별도 수동/반복 승인을 진행한다.
