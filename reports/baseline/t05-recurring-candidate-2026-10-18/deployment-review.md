# T05 반복 자동화 적용 검토안

**설치 후보 바이트와 scope 계산, 활성화·중지·원복 순서를 정리한 준비 문서다.**
실제 관리자 시험은 [10/18 14:34 완료](system-preflight-review.json)했다.
최종 변경 도구의 중단/원복 검증은 남아 있으며 운영 활성화 승인안으로 확정하지 않았다.
이 문서나 이전 수동 승인을 사용해 운영 파일·mask·timer를 변경하지 않는다.

## 고정 입력

- 후보: `t05-recurring-retirement-20261018-e57a7995f03fb301`.
- 새 최종 경로: `/opt/marketdata/releases/t05-recurring-retirement-20261018-e57a7995f03fb301`.
- 현재 scope: `84a0b92ae6f8bf0e38ac10ecb4a2615a32af72aebf1c8c28d591c02087cf4983`.
- 후보 scope: `311ab16f019b4473185c487b219430c1fab4e90c4257f813bacfa696b1ba29a5`.
- 후보 비활성 grant SHA-256: `6d731e2e9e43aba3f249c5349531b1155948e4ecdeb4546ae0c4ef7463d62c66`.

[실행 소스](manifest.json), [최종 네 유닛](deployment-files.json),
[변경 scope 입력](scope-projection.json)을 함께 고정한다.
새 scope는 코드·유닛 경로 변경 때문에 필요하다. 기존 보호 시작점·등록·완료/순서 객체를 바꾸는 작업이 아니다.
활성 grant의 승인 시각·참조와 after 해시는 운영 승인을 받을 최종 operation에서 별도로 고정한다.

## 예정 변경 목록

| 경로/대상 | 예정 변경 |
|---|---|
| 새 `/opt` 후보 경로 | 추가 설치·root 소유/쓰기 제한·UID 1000 실행 대조. 구버전 보존 |
| `/etc/marketdata/checkpoint-clock-retirement/units/`의 service·preview·apply template | 검증한 공통 부모 mount를 유지하고 새 실행본 경로만 반영 |
| 같은 canonical timer | 바이트 유지. 15:40 KST·Persistent=false·즉시 실행 조건 없음 |
| `/etc/systemd/system/marketdata-checkpoint-retirement-apply@.service` | canonical과 같은 새 후보 바이트의 일반 파일 |
| `/etc/systemd/system/marketdata-checkpoint-retirement.service`·`.timer` | 현재 mask를 승인한 root 소유 일반 파일로 교체 |
| 시스템 timer enable 링크 | 정확한 대상 링크만 생성·보존·원복 목록에 포함 |
| `recurring.json` | 최종 설치와 scope 대조 후 별도 승인에 따라 활성 grant 설치 |
| 기존 사용자 service/timer | 두 mask 유지 |

preview의 기존 canonical 연결, settings·policy·clock binding·manual 비활성 문서,
T16 backup/restic 실행본·Chrony·수집기·bootstrap ledger는 그대로 유지하는 범위다.
각 파일의 실제 before 형태/해시/uid/gid/mode·enable 링크와 실효 유닛은
최종 operation 생성 및 적용 직전에 다시 확인한다. 현재 표를 완성된 실행 manifest로 대신하지 않는다.
옛 renderer 출력으로 공통 부모 mount를 두 개의 별도 쓰기 경계로 되돌리지 않는다.

## 활성화 순서

1. 현재 boot·구성·ledger·보호 입력·네 mask·비활성 grant와 프로세스 신원을 확인한다.
   정리 서비스의 PID/job 및 미완료 plan/journal을 대조한다. 예상 밖 상태에서는 보류한다.
2. 새 `/opt`를 먼저 설치하고 최종 경로에서 UID 1000의 실제 Python·CLI/import와 권한을 확인한다.
   이 확인에 실패한 상태로 운영 자동 유닛을 바꾸지 않는다.
3. timer가 비활성인 동안 canonical/실제 유닛을 정확한 바이트로 설치하고 manager 조회를 대조한다.
   daemon-reload가 다른 예약 업무에 줄 수 있는 기동도 확인하며 보존 업무를 강제 중지하지 않는다.
4. 설치된 `propose`로 scope를 다시 계산해 위 후보 값과 완전히 일치하는지 확인한다.
   기존 baseline·사후 상한을 다시 등록하거나 수동 `scope_transition`을 넣지 않는다.
5. 별도 승인된 활성 recurring을 설치한 뒤 시스템 timer만 enable/start하고 다음 예약·trigger·guard를 확인한다.
   자동 service를 수동으로 시작해 추가 삭제를 시험하지 않는다.

반복 승인은 특정 `31766af4…` 목록의 반복 실행이 아니라
고정 코드·정책·경로·scope 안에서 매 실행 새 계획과 검증을 수행하는 권한이다.
15:40 KST·Persistent=false·Restart=no, U≤5초·72시간+60초·후행 검증 사본 3개,
당일 backup/동일 backup ID의 restic 저장·전체 검사·무결성·잠금 조건을 유지한다.

## 중지와 원복

새 예약 요청을 먼저 막고 service의 PID·job·실제 run 단계를 확인한다.
oneshot의 `activating`을 유휴로 보지 않는다. 실행 중이면 기존 정책 아래 정상 종료를 기다리고,
종료 대기 시간을 강제 종료 허용 시간으로 사용하지 않는다.
실행 중 canonical·코드·승인 문서를 바꿔 감시/승인 대조를 깨뜨리지 않는다.

유휴가 확인된 뒤 recurring을 비활성으로 돌리고 시스템 자동 service/timer mask와 enable 링크를 정리한다.
기존 user mask는 계속 유지한다. 이 순서에서 사용하는 정확한 manager 명령·대기·중단 상태는
최종 실행기와 시험에 고정해야 한다.

우선적인 복구 상태는 **새 실행본과 모든 기록을 보존한 비활성·mask 상태**다.
구성 원복이 필요하면 저장한 before 바이트·형태·권한을 되돌리되,
새 plan/run/journal·자료·T05 완료/순서 근거는 과거 사본으로 덮어쓰지 않는다.
미완료 journal은 새 자동 계획으로 우회하지 않고 정확한 상태를 별도 검토한다.

구버전은 새 잠금 보류 근거를 해석하지 못해 `unfinished_retirement`로 막는 것을
[확인](old-guard-compatibility.json)했다. 따라서 구버전으로 파일을 돌려놓는 것과
자동 실행 재개는 별개다. 기록을 지우거나 `deletion_attempted`를 false로 바꾸지 않는다.
검토 후 새 후보를 다시 사용하거나 정확한 상태에 대한 별도 복구 절차를 정해야 한다.
현재 이 검사는 helper 호환성만 확인했으며 전체 실제 구성 원복을 완료했다고 표시하지 않는다.

## 운영 완료 판정

| 결과 | 판정 |
|---|---|
| 첫 timer 기동·no_candidates | 자동 기동·0건 평가 확인. 실제 자동 삭제 완료와 구분 |
| 선행 작업/시각/잠금 보류 | 사유와 변경 미시작 근거를 보존. 확정된 안전 보류만 다음 예약 재평가 |
| 기록 불명확·삭제 중단·미완료 journal | 점검 필요, 새 계획 자동 진행 금지 |
| 자연스럽게 생긴 후보의 자동 정리 | 그 실행의 새 계획·실제 삭제·유지·journal·시각/보존 근거 대조 |

원래 10개 수동 정리나 전체 무결성 검사를 운영 시험 목적으로 반복하지 않는다.
형식적인 checkpoint 생성·시각 변경·보호 조건 축소도 하지 않는다.
33분 51초의 기존 수동 실행 자료는 T14에서 다루며 자동화 승인 조건에 성능 변경을 추가하지 않는다.

## 남은 준비

완료한 관리자 임시 시험을 근거로 실제 설치/활성화·중지·부분 실패/원복 실행기 및 격리 검증 →
정확한 최종 operation ID·before/after·감사 범위 제출 → **별도 운영 승인** 순서다.
실제 최종 경로 설치와 반복 timer 활성화, 첫 예약 결과·첫 자연스러운 자동 삭제는 아직 미실시다.
