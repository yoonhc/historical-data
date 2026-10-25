# T05 반복 자동화 준비 검토

**첫 수동 정리와 사후 검증은 완료 상태를 유지한다.** 이번에는 기존 승인 기록 연결,
현재 자동 구성의 읽기 전용 대조, 잠금 보류 후 재평가 경계만 확인했다.
운영 삭제·전체 무결성 검사·새 backup·근거 등록·서비스 제어는 수행하지 않았다.
현재는 **반복 승인안 준비 중**이며 최종 활성화 계획 고정·운영 승인은 아직 아니다.

**10/18 후속:** 아래 잠금 보류 경계는 별도 후보 `t05-recurring-retirement-20261018-e57a7995f03fb301`로
보완했고 후보/관련 회귀 46개·관리자 시험 도구 15개를 통과했다.
첫 root 시험은 일회성 transient 유닛 회수 뒤 재시작을 시도한 도구 오류로 실패했다.
운영 전후 동일·시험 정리를 확인하고 도구만 수정했으며 **14:34 재점검을 통과했다.**
실제 timer/수동 출처 구분·UID 1000·fragment guard·Chrony 읽기·비활성 승인 보류와 정리를 확인했다.
이 문서의 이전 코드 대조는 당시 기록으로 유지한다.
[새 후보·검증·잔여 범위](../t05-recurring-candidate-2026-10-18/summary.md),
[관리자 점검](../t05-recurring-candidate-2026-10-18/system-preflight.md).

## 확인한 현재 구성

10/18 **13:46:31 KST** 실제 호스트에서 [구성 대조](configuration-inspection.json)를 통과했다.
[도구](inspect_configuration.py)는 설치 CLI의 읽기 전용 `propose`와 파일·manager 조회만 사용한다.
운영 `preview`·`run`·`apply-plan`을 호출하지 않는다.

| 항목 | 확인 결과 |
|---|---|
| 설치 T05 실행본 | `t05-completion-evidence-20261011-c9d413cfe11ec0e7` 유지 |
| 현재 scope | `84a0b92ae6f8bf0e38ac10ecb4a2615a32af72aebf1c8c28d591c02087cf4983` |
| recurring | 설치 코드의 새 계산 결과와 동일. `enabled=false`, `approved_at=null`, `approval_reference=null`, `scope_transition` 없음 |
| manual | 비활성 유지 |
| 자동 canonical service | UID 1000 계정, 고정 Python/진입점, `Restart=no`, `SuccessExitStatus=75` |
| 쓰기 경계 | `ReadWritePaths=/mnt/backup/marketdata-historical` 공통 부모 사용 |
| canonical timer | `OnCalendar=*-*-* 06:40:00 UTC` = 15:40 KST, `Persistent=false`, `AccuracySec=1s`, 정확한 자동 service 연결. 활성화 직후 실행 옵션 없음 |
| 실제 자동 service/timer | system·user 모두 `/dev/null` mask, inactive·job 없음. 실제 자동 service fragment의 실행 검증은 아직 아님 |
| 파일 권한 | 조회한 설정·canonical 유닛 모두 root:root/0644 |
| 운영 프로세스 | 기존 Chrony·depth/aggTrade supervisor의 PID/invocation 유지 |

검토한 원본의 사본은 [자동 service](canonical/marketdata-checkpoint-retirement.service),
[timer](canonical/marketdata-checkpoint-retirement.timer)다. 옛 renderer는 실행하지 않았다.

| 입력 | SHA-256 |
|---|---|
| canonical 자동 service | `97c0b5290eb5dc8b7bdda9811e7f629a0994dbd37661fc77b025bbe2109bd6a5` |
| canonical timer | `5cf13dceffa07a7d452dc5d0303dbb26fb77e9a1ebbf2fe7d1c26dd2735d5dfa` |
| 비활성 recurring | `9d9c544fe9fc7c4b3ec498fddf6feef7fb419b09b1f760a2bbe3c009f8422a5e` |

`installed_service()`는 실제 FragmentPath의 신뢰 가능한 일반 파일과 canonical 바이트 일치,
추가 drop-in 없음·reload 완료를 요구한다. 따라서 향후 실제 자동 service는 검증한 바이트의
root 소유 일반 파일로 설치해야 하며, mask를 링크로 교체했다는 사실만으로 guard 통과를 가정하지 않는다.
수정 후보로 실행 경로가 바뀐다면 위 사본은 현재 기준이고 최종 설치 파일은 다시 고정해야 한다.

`Persistent=false`는 비활성 기간에 놓친 calendar 예약의 따라잡기를 사용하지 않는 설정이다.
별도 시작 옵션이나 수동 시작을 막는 기능으로 확대하지 않는다. 이미 active인 service에 timer가
새 인스턴스를 추가하지 않는 동작도 업무 잠금·미완료 journal 검사를 대신하지 않는다.
[systemd v255 timer 문서](https://raw.githubusercontent.com/systemd/systemd/v255/man/systemd.timer.xml).

## 완료된 수동 작업의 승인 연결

`eced87a4…` 승인 → 18:47 네 구성 파일 after 적용/Busy 실패 →
`821367d2…` 재개 전용 별도 승인 → 원래 삭제 plan `31766af4…` → 성공 run `f57f334b…`다.
[완료 보고서에 링크와 의미](../t05-mount-recovery-2026-10-17/completion.md)를 추가했고,
[보존된 원본의 식별자·해시 대조](approval-chain.json)를 남겼다.
전체 첫 operation의 실패 결과와 성공한 구성 적용을 구분한다.
두 번째 operation은 설치 0개·daemon-reload 없이 이미 설치된 구성을 사용했다.

## 자동 경로에서 확인한 한 가지 차이

설치 코드의 `execute()`는 자동 `run`에서 매번 `agent.plan()`을 호출한다.
완료한 수동 plan을 재사용하지 않으며, 일반 recurring 승인에 수동 EXDEV `scope_transition`을 사용하지 않는다.
system timer의 실제 service provenance와 같은 KST 날짜 15:40 trigger도 검사한다.
반면 적용 진입 표시와 실제 삭제 시작이 구분되지 않는 경계가 있다.

1. 계획 생성 완료 뒤 `deletion_attempted=true`를 영속화한다.
2. `agent.apply()`가 maintenance/repository 잠금을 다시 얻는다.
3. 여기서 Busy이면 삭제와 journal 생성 전이어도 `deferred`/exit 75와 flag가 남는다.
4. 다음 `run`의 `locks()`는 이 flag를 가진 미완료 plan을 발견해 `unfinished_retirement`로 점검을 요구한다.

| 격리 입력 | 해당 실행 | 다음 실행의 미완료 guard |
|---|---|---|
| 계획 생성 중 Busy | deferred/75, deletion_attempted=false, 삭제 0개 | 후속 보존 조건 검사까지 진행 가능 |
| 적용 진입 뒤 Busy, journal 없음 | deferred/75, deletion_attempted=true, 삭제 0개 | unfinished_retirement, 점검 필요 |
| 미완료 journal 존재 | — | 점검 필요 유지 |
| 해당 plan의 완료 journal 존재 | — | 과거 시도가 후속 검사를 차단하지 않음 |

[설치 Python 3.12.3의 격리 결과](deferral-probe.json), [재현 코드](probe_deferral.py).
실제 설치 `execute()`와 다음 실행의 `ClockRetirement.locks()`를 사용했다.
provider·manager·업무·부모 잠금은 대역이며, 실제 프로세스 간 경합이나 운영 오류를 재현한 결과는 아니다.
실제 부모 `Retirement.apply()`가 journal 생성 전 `self.locks()`를 획득하는 순서도 코드로 대조했다.
이는 현재 동작을 확인한 시험이며 **수정 후보의 통과 근거가 아니다.**

따라서 “단순 잠금 보류는 다음 예약에서 재평가”를 모든 단계에서 이미 보장한다고 쓰면 안 된다.
현재 코드는 삭제를 진행시키지 않고 더 보수적으로 점검을 요구한다.
반복 승인 전에는 **변경 전에 안전하게 종료됐음이 확인된 잠금 보류와, 변경 가능성이 남은 중단을 구분**하는
최소 보완을 권한다. `deleted_this_run=0`이나 journal 부재 하나만으로 과거 flag를 지우면 안 된다.
원래 run·실패·journal은 유지하고, 실제 변경 시작 또는 결과 불명확 상태는 계속 자동 재개를 거부해야 한다.
이번에는 /opt 코드나 과거 상태를 수정하지 않았다.

## 다음 준비의 범위와 승인 경계

다음 작업은 위 경계의 작은 후보·중단 검사와, 자동 서비스의 달라지는 연결 검증이다.
U≤5초·72시간+60초·후행 검증 사본 3개·당일 backup/같은 backup ID의 restic 전체 검사·무결성·잠금은 유지한다.
이전 수동 엔진 시험과 실제 공통 mount 시험은 무변경 부분의 근거로 재사용한다.
실제 자동 진입점/guard·timer 출처·반복 승인에는 전용 시험 경로와 작은 입력을 사용한다.
원래 10개를 다시 정리하거나 운영 backup을 만들어 시험하지 않는다.

| 준비할 계약 | 범위 |
|---|---|
| 최종 설치 입력 | 검증한 새 실행본이 필요하면 별도 ID/해시 고정. 공통 부모 mount를 유지한 canonical/실제 service·timer 바이트와 root 권한 대조 |
| recurring 승인 | 최종 code/unit/policy/path로 scope 재계산. 현재와 같으면 유지, 변경됐으면 정확한 차이 설명. 매 실행의 새 plan 생성 권한이며 고정 목록 반복 승인이 아님 |
| 활성화 | 실제 system 서비스 파일·timer 파일·enable 링크·recurring의 before/after와 관리자 명령을 새 계획으로 고정. 직전 유휴/잠금·boot/구성·미완료 상태 확인. 사용자 두 mask는 유지 |
| 중지 | 새 timer 요청을 막고 진행 중 서비스와 job을 구분. 실행 중 승인 문서나 코드 변경으로 감시를 깨뜨리지 않도록 종료/안전한 중단과 권한 회수 순서를 검증. 무조건적인 강제 종료는 하지 않음 |
| 구성 원복 | 유휴 확인 후 비활성 grant·시스템 mask 복구. 새 plan/run/journal/자료/완료·순서 근거를 보존. 미완료가 있으면 정확한 상태를 검토하고 별도 수동 복구 |
| 첫 운영 결과 | no_candidates/보류/실패/완료를 구분. 첫 예약 후보 0개면 자동 기동·0건 처리만 완료로 기록. 실제 자동 삭제는 자연스러운 후속 후보로 확인 |

이 표는 준비할 순서를 정한 것이며 **실행 가능한 최종 활성화/중지/원복 도구가 완성됐다는 뜻이 아니다.**
현재 scope `84a0b92a…`는 이번 읽기 대조에서 일치한다. 실행 코드가 바뀌면 코드 해시도 scope의 입력이므로
현재 scope의 반복 승인을 새 후보에 자동 승계할 수 없다. 기존 baseline·완료 상한·생성 순서는 다시 등록하지 않는다.
최종 검증과 정확한 변경 목록을 제시한 뒤 시스템 자동화의 별도 승인을 받는다.

33분 51초와 조회 114회·파일 제거 기록은 [T14](../../../TODO.md)에 이미 연결돼 있다.
이번 준비에 전체 검사 축소·새 성능 정책·당일 조건 변경을 포함하지 않는다.

## 검증 한계와 도구 실행 기록

설정 대조의 첫 sandbox 실행은 호스트 소유권을 그대로 표시하지 않아 권한 확인에서 중단됐다.
native 첫 실행은 timer에도 service의 MainPID가 있다고 가정한 조회 도구 오류로 중단됐다.
service에만 PID를 요구하도록 도구를 고친 뒤 native chunk `44ea44`에서 통과했다.
두 실패 모두 읽기만 수행했고 운영 파일·service·grant를 변경하지 않았다.
실제 자동 유닛은 여전히 masked이므로 최종 보호 환경/실제 자동 trigger 검증은 남아 있다.
