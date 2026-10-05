# 작업용 checkpoint 정리 자동화 후보

이 문서는 15:40 KST 자동 정리의 구현 계약이다. 후보 구현·격리 시험·삭제 없는 운영 미리보기까지만
진행하며, 타이머 활성화와 반복 삭제 승인은 별도다. 기존 개별 수동 계획의 승인을 재사용하지 않는다.

## 범위와 선행 조건

새 `scripts.checkpoint_schedule`은 기존 `Retirement.plan()`·`apply()`를 호출한다. 원래의 UUID·
저장소 ID·manifest/source_root·복구 증거·전체 검사 범위·파일 목록 대조·유지 사본 전체 해시 검사와
잠금 순서는 유지한다. 원래 정리기에 추가한 기능은 선택적 자동 실행 ID·승인 범위 ID를 계획의
해시 대상에 포함하는 것뿐이다. 이 두 ID로 시계가 같아도 실행마다 다른 계획을 만든다.

자동 실행은 다음을 추가 확인한다.

- 당일의 기준은 **Asia/Seoul 달력 날짜**다. 해당 날짜에 시작해 완료한 daily의 latest 보고서와
  같은 ID의 history 보고서가 같아야 한다. `complete`, `ok`, 논리 검사 결과와 backup 성공을 확인한다.
- daily의 정확한 backup ID·경로·원래 source_root를 archiver 기록과 연결한다. 기준 시각도 당일이어야 한다.
- 해당 사본의 입력 검증, 성공한 저장 명령의 시각·snapshot ID·stdout summary가 일치해야 한다.
  원래 저장 시각이 미상인 기록을 새 성공으로 추정하지 않는다.
- 저장 이후의 `check --read-data` 성공과 해당 snapshot 포함을 확인하고 명령 receipt·출력도 대조한다.
  기존 정리기는 이어서 후보와 유지 사본 모두의 실제 restic snapshot 및 manifest를 검사한다.
- 선행 작업 미완료나 잠금 경합이면 보류한다. 미해결 실패는 점검 필요로 표시한다.
- 기존 정리 journal이 미완료이면 새 계획을 만들지 않는다. 자동 재개·기록 삭제는 하지 않는다.

이 확인은 plan과 apply가 각각 maintenance·repository 잠금을 획득한 안쪽에서 반복한다.
plan 이후 daily·설정·승인 범위가 바뀌면 예전 판단으로 apply하지 않는다. 코드와 승인 파일도
기존 정리기의 코드 검사 지점에서 다시 확인한다. 당일 날짜를 넘어가면 다음 예약에서 재평가한다.

기존 최근 검증 사본 **3개**와 **완료 후 72시간 미만**의 보호 규칙을 합집합으로 적용한다.
편입 전 사본·원본·live catalog·restic 지점·시험 공간·복구 키트는 대상이 아니다.
직접 외부 복사 등 공통 flock에 참여하지 않는 프로그램까지 강제 보호할 수는 없으므로,
운영 checkpoint 읽기는 기존 잠금 참여 진입점을 사용해야 한다.

## 반복 승인과 고정 배포본

자동화 전용 정책과 설정을 사용한다. 정책의 `apply_enabled=true`는 기존 정리기 호출을 허용하는
최종 정책의 일부이며, **자동 실행의 승인을 뜻하지 않는다**. `preview`는 이 최종 정책으로도
`apply`를 호출하지 않는다. 미리보기 후 정책 플래그를 바꿔 계획을 재사용하는 방식은 쓰지 않는다.

`propose`는 아래 범위를 묶은 JSON을 출력하며 `enabled=false`, `approved_at=null`,
`approval_reference=null`로 만든다. 이 명령에는 승인을 활성화하는 옵션이 없다.

- 전용 승인 파일·정책 파일 경로, 실행 사용자 UID
- 전체 실행 소스·의존성 선언·새 service/timer의 해시와 코드 ID, Python 경로·버전
- 정책·archive 프로필의 해시, source root·저장소 ID·archive scope, 프로필의 모든 경로
- restic 실행 파일 해시, 최신 3개·72시간, 15:40 KST 예약과 재시도·자동 재개 금지

승인 파일은 사용자 소유 0600 일반 파일이어야 한다. 검토 후 별도 반복 승인을 받으면 범위를
바꾸지 않고 `enabled=true`, 실제 승인 시각·승인 근거를 기록한다. 조건 중 하나라도 달라지면
새 승인이 필요하다. 수동 계획 JSON을 이 파일로 지정해도 승인으로 인정하지 않는다.
`run`은 승인과 함께 지정된 timer의 현재 systemd start Job/PID/invocation 근거를 요구한다.
수동 CLI·수동 service 시작은 자동 삭제 권한을 사용하지 못한다.

배포본은 별도 고정 디렉터리에 둔다. `run_checkpoint_schedule.py`가 그 디렉터리의 코드와
marketdata 패키지를 먼저 import하므로 기존 editable 수집 설치와 분리된다. 다른 트리에서
가져온 관련 모듈이 섞이면 거부한다. 코드 변경은 새 배포본과 새 승인으로 처리한다.
Git 없는 배포본은 기존 코드 식별자의 `git_commit=null`을 그대로 쓰고 파일별·전체 해시를
승인한다. 없는 commit을 만들어 기록하지 않는다. 재현용 후보 Git 기준은 별도 release 메타데이터다.

## 실행 결과와 기록

| state | 의미 | 종료 코드 |
|---|---|---:|
| `preview_ready` | 전체 plan 검사 통과, 후보 존재, 삭제하지 않음 | 0 |
| `no_candidates` | plan 검사 통과, 후보 0개 | 0 |
| `deferred` | 잠금·선행 조건·승인 부족 또는 미완료 정리 때문에 보류 | 75 |
| `failed` | 무결성 대조·읽기/쓰기 오류·중단 등 실제 실패 | 1 |
| `complete` | 새 계획의 정리가 완료됨 | 0 |

매 평가에 고유 run ID와 `reports_root/checkpoint-scheduled/<run_id>/run.json`을 남긴다.
plan 검사 통과 시 새 plan ID와 정확한 후보·보호 사유·편입 전 제외 목록을 기록한다.
plan 전 보류는 `plan_id=null`, `selection_assessed=false`다. 확인하지 않은 목록을 후보 0개로
꾸미거나 미완료 정리가 있는데 새 계획을 만들지 않는다.

실제 unlink 전 진행 상태를 먼저 저장한다. 강제 종료로 run receipt가 `running`에 남더라도
기존 정리 journal이 작업 진행의 기준이며, 다음 자동 실행은 미완료 journal을 감지해 보류한다.
계획을 기록한 뒤 정리 journal 생성 전에 강제 종료된 자동 실행도 모호한 중단으로 보류한다.
완료 journal로 해소됐음을 확인할 수 없다면 별도 수동 점검이 필요하고, 보고서를 지워 넘기지 않는다.
단순히 생성해 둔 미리보기 계획은 실제 자동 적용을 시작했다가 중단한 실행과 구분한다.
수동 점검·명시적 재개는 원래 계획/코드로 수행하고 그 근거를 따로 남긴다.
이미 완료한 개수와 미완료 여부를 구분하며, 부분 정리 후 오류는 성공으로 덮지 않는다.
보고서 저장 자체가 실패하면 CLI 오류와 systemd 실패가 남을 수 있고 파일 receipt를 보장하지 않는다.

`preview`는 기존 plan 검사다. 유지 사본 파일 전체 해시·카탈로그·pack 검사는 원래대로 실제
apply 직전에 수행한다. 운영 미리보기에서 이 적용 전 전체 검사를 실행했다고 주장하지 않는다.
54분 복구 시험이나 전체 source 논리 검사·restic read-data를 매 정리에서 다시 실행하지 않는다.

## 예약과 중지

후보 timer는 `OnCalendar=*-*-* 06:40:00 UTC`로 15:40 KST에 실행한다.
`Persistent=false`, 부팅 직후 실행 없음, `Restart=no`다. 보류는 다음 날 다시 평가한다.
서비스는 `SuccessExitStatus=75`로 예상된 보류를 재시도 대상으로 만들지 않는다.
따라서 systemd의 success만으로 정리 완료라 판단하지 말고 receipt의 state와 attention을 확인한다.

승인·설치 후 **새 정리 예약만 중지**하는 명령은 다음과 같다.

```bash
systemctl --user disable --now marketdata-checkpoint-retirement.timer
```

이 명령은 기존 daily·restic·수집 예약을 변경하지 않는다. 이미 실행 중인 정리는 계속될 수 있다.
진행 중 작업까지 중단할 별도 필요가 있을 때만 다음을 사용한다.

```bash
systemctl --user stop marketdata-checkpoint-retirement.service
```

서비스 중단은 SIGINT와 기존 중단 기록을 사용한다. 삭제한 작업용 사본을 되돌리는 명령은 아니다.
부분 정리 기록을 확인하고, 필요한 사본은 명시적 restic ID로 별도 경로에 복원한다.
`forget`·`prune`, 시험 산출물 삭제, 자동 unlock·원래 manifest 수정은 이 기능에 없다.
