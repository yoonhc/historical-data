# T05 첫 자연 예약 관찰 계약 — 2026-10-18 사용자 수락

사용자는 [설치·반복 승인·시스템 timer 활성화와 사후 대조](status.md)를 수락했다. 이 단계는 완료다.
다음 관찰 대상은 **2026-10-19 15:40:00 KST(06:40:00 UTC)의 자연스러운 시스템 예약**이다.
이 문서는 관찰 범위를 고정한 것이며 아직 실행되지 않은 예약의 성공 근거가 아니다.

## 완료 단계와 반복 권한

| 단계 | 현재 상태 | 완료 근거 |
|---|---|---|
| 설치·반복 승인·시스템 timer 활성화 | **완료·사용자 수락** | operation `2c03a81f…`, root attempt `963f259a…`, [사후 대조](activation-audit-20261018.json) |
| 첫 실제 예약 평가 | **대기** | 실제 timer 출처·invocation·run, 생성된 plan과 선행 조건·최종 결과 연결 |
| 첫 자연스러운 자동 삭제·보존 확인 | **대기** | 실제 삭제가 발생한 예약의 정확한 대상·보호/제외 사본·staging·journal·미완료 여부 대조 |

승인한 후보 `t05-recurring-retirement-20261018-e57a7995f03fb301`, operation
`2c03a81f309d0eca6f03e24a5d84fb838d75598fd1d38dbc6dd2a4204a57ae6e`, scope
`311ab16f019b4473185c487b219430c1fab4e90c4257f813bacfa696b1ba29a5`의 반복 범위 안에서는 **새 plan마다 수동 승인을 다시 요구하지 않는다.**
코드·정책·경로·scope 변경이나 미완료/불명확 상태를 기존 승인으로 우회하지 않는다.
완료된 설치 apply, 이전 수동 plan/apply/resume, EXDEV scope_transition을 재사용하지 않는다.

## 첫 실행에서 연결할 작은 기록

| 연결 | 확인할 근거 |
|---|---|
| 실제 예약 → 실행 | 시스템 timer의 실제 trigger와 같은 service의 invocation·실행 시각·PID/종료 기록. 예약표·현재 timer active·시작 시각만으로 출처나 완료를 추정하지 않는다. |
| 실행 → 새 작업 | 해당 invocation의 새 run ID·action·후보/보호/제외 집합·plan ID와 해시. 계획 생성 전 보류라면 plan 부재와 실제 보류 단계·사유를 그대로 남긴다. |
| 당일 보존 | 해당 실행이 채택한 당일 maintenance 보고서·backup ID·완료 marker, **같은 backup ID**의 restic snapshot ID·archive receipt·그 snapshot이 포함된 전체 읽기 검사 완료 근거. 검사 종료 시각과 자료 기준 시각을 구분한다. |
| 시각·순서·보호 | 그 실행의 유효 관측/U와 기간 판단, 각 후보의 기존 완료 상한 및 확실한 후행 검증 사본 3개, 기존 baseline·생성 순서 연결. 활성화 때나 관찰 때의 새 시각으로 과거 삭제 판단을 대신하지 않는다. |
| 무결성·inventory·잠금 | 실행기가 실제 수행한 유지 사본 무결성·inventory 검사 결과와 잠금 진입/종료·보류 근거. 나중의 유휴 상태를 당시 잠금 획득 근거로 대체하지 않는다. |
| 최종 결과 | run의 phase/state/exit·최종 오류/보류 사유·처리 수와 journal, 해당 invocation의 systemd 종료 결과를 함께 대조한다. |

우선 `/var/lib/marketdata-checkpoint-retirement/runs/<run-id>/run.json`, 해당 실행의 시각/판정 기록,
`retention/plans/<plan-id>.json`·`retention/journals/<plan-id>.json`, maintenance/archive receipt와 제한된 invocation journal을 읽는다.
원본 경로·ID·해시를 연결하고 필요한 작은 기록만 보존한다. 이미 실행기가 수행한 전체 무결성 검사를 확인하려고 다시 실행하지 않는다.
확인되지 않은 연결은 미확인으로 남기고, 전체 복원·추가 backup·장애 주입으로 관찰 범위를 늘리지 않는다.

## 결과별 판정

| 실제 결과 | 첫 예약 평가 기록 | 자동 삭제·보존 판정과 다음 행동 |
|---|---|---|
| `no_candidates` 등 정상 0건 종결 | 출처·run·최종 근거가 맞으면 **첫 평가 완료, 처리 0건** | 실제 자동 삭제는 아직 미확인. 후속 자연 예약에서 후보가 생길 때 관찰한다. |
| 당일 보존 미완료 또는 확정된 사전 잠금 보류 | 실제 단계·사유·삭제 진입 여부와 정상 최종 기록을 확인해 **보류 관찰**로 구분 | 설치된 정책이 허용하는 다음 예약 재평가를 기다린다. 확인용 백업·수동 service 시작은 하지 않는다. |
| 업무 실패·미완료 삭제·기록 불명확 | **실패 또는 점검 필요**. 정상 평가/삭제 완료로 표시하지 않는다. | 기존 run/journal을 보존하며 새 plan으로 우회하지 않는다. 기존 반복 권한을 복구 예외로 사용하지 않는다. |
| 실제 삭제 완료 | timer→invocation→새 plan/run→선행 근거→최종 완료를 연결 | 아래 사후 대조까지 통과하면 **첫 자연스러운 자동 삭제·보존 확인 완료**로 기록한다. |
| 아직 실행 중·종료 근거 부족 | 진행 중 또는 미확인 | 시작 시각·systemd success만으로 업무 완료를 표시하지 않는다. |

exit 75는 업무의 보류 사유·최종 기록과 함께 해석한다. systemd가 success로 표시해도 실제 삭제 완료를 뜻하지 않는다.
정상 0건/보류의 첫 평가 관찰과 실제 자동 삭제 확인은 서로 다른 단계이며, 실패를 정상 보류로 바꾸지 않는다.

실제 삭제가 있었으면 plan/journal의 **정확한 경로·backup ID·manifest·항목별 상태**를 기준으로 원본 및 해당 staging 대상의 부재,
보호/제외 사본의 존재·신원, 삭제 수, journal/attempt 종결과 미완료 intent 여부를 대조한다.
작은 marker·manifest·실행기가 저장한 검증 receipt를 우선 사용하며, 전체 자료를 다시 읽어 검증하지 않는다.
공간 증가나 삭제 수 하나만으로 보호/보존 결과를 대신하지 않는다.

## 유지할 운영 상태

현재 승인한 recurring·시스템 timer 활성, 사용자 자동화 두 mask·manual 비활성을 유지한다.
15:40 KST·Persistent=false·Restart=no, U≤5초·보수적 72시간+60초·후행 검증 사본 3개와 당일 보존·무결성·inventory·잠금 조건은 그대로다.
Chrony·수집기·T16·RTC/polling과 T05 baseline·완료 상한·생성 순서·기존 근거를 변경하지 않는다.
운영 결과를 만들기 위한 수동 서비스 시작·삭제·추가 백업이나 중복 apply는 하지 않는다.
이 관찰 계약을 반영하는 이번 작업은 문서 변경이며 운영 제어를 수행하지 않았다.
