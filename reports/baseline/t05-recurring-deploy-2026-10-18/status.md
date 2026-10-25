# T05 반복 자동화 운영 상태

**10/18 22:59:39 KST 시스템 timer 활성화 완료. 23:01:39 KST 원본 receipt·설치·권한·scope·실효 유닛·다음 예약의 읽기 전용 사후 대조를 통과했다.**

**다음 실제 예약은 2026-10-19 15:40:00 KST(06:40:00 UTC)**다. 설치·반복 승인·timer 활성화는 완료했으며, 첫 예약 평가와 첫 자연스러운 자동 삭제·보존 관찰은 남아 있다. 같은 apply나 이전 수동 정리 명령을 반복하지 않는다.

## 고정 operation과 실제 승인

| 항목 | 확인값 |
|---|---|
| operation | `2c03a81f309d0eca6f03e24a5d84fb838d75598fd1d38dbc6dd2a4204a57ae6e` |
| 설치 후보 | `t05-recurring-retirement-20261018-e57a7995f03fb301` |
| 고정 도구 | `t05-recurring-operation-20261018-447cddda543bc6ed` |
| 실제 scope | `311ab16f019b4473185c487b219430c1fab4e90c4257f813bacfa696b1ba29a5` |
| 최초 authority 수락 시각 | `2026-10-18T13:59:34.892351+00:00` / 22:59:34.892351 KST |
| 활성 recurring SHA-256 | `3b436c4f0803582c6a64414a009abf2ab212945584f6ba3211ec1efbaff8035d` |
| root attempt | `963f259af2fa4ed898c01b8e903b17f5` / `passed=true`, `phase=activated` |

사용자의 [정확한 반복 승인](approval.json)과 [고정 계획](operation-plan.json)을 사용했다. root의 operation-plan 바이트가 검토본과 같다.
root authority·active-grant 바이트/해시와 운영 recurring을 대조했고 승인 시각·scope를 새로 만들지 않았다.
[원본 attempt 사본](activation-evidence/attempts/963f259af2fa4ed898c01b8e903b17f5.json), [전환 state 사본](activation-evidence/state.json), [authority 사본](activation-evidence/authority.json), [사후 대조 원본](activation-audit-20261018.json).

root 원본은 `/var/lib/marketdata-clock/t05-recurring-operations/2c03a81f309d0eca6f03e24a5d84fb838d75598fd1d38dbc6dd2a4204a57ae6e/`에 보존된다.
사후 대조 파일은 복사한 원본 11개의 경로·SHA-256·소유권·모드를 연결한다. 이전 검토안·승인 대기 기록의 설명은 해당 준비 시점 기록이며 고정 도구/계획을 갱신하지 않았다.

## 실제 설치·활성화 대조

- 최종 `/opt/marketdata/releases/t05-recurring-retirement-20261018-e57a7995f03fb301`의 **1,798개 설치 객체**와 별도 설치 marker, 도구 6개+계획의 **7개 객체**와 marker를 해시·링크·모드·root 소유권으로 확인했다.
- 최종 경로의 UID 1000 Python·prefix·import·CLI help를 실제 실행해 재대조했다. root 전환 기록에도 같은 UID·경로와 성공한 fragment guard가 남아 있다.
- 비활성 grant 아래 설치한 뒤 얻은 **실제 proposal 전체가 예상 proposal과 일치**했다. 그 관측의 U는 **0.134919064초**였다. 이는 활성화 때의 관측이며 향후 삭제 시점의 시각 근거로 재사용하지 않는다.
- 변경 대상 8개는 정확한 after이며 일반 파일 root:root/0644, enable 링크의 대상도 일치한다. canonical/실제 자동 service·timer가 같은 바이트다. 공통 부모 `ReadWritePaths=/mnt/backup/marketdata-historical`을 유지했고 옛 renderer를 실행하지 않았다.
- 실효 service는 `User=Group=yoonhc`, 고정 후보의 `run` 진입점, `Restart=no`, `ProtectClock=yes`, `ProtectSystem=strict`, `ProtectHome=read-only`, `NoNewPrivileges=yes`다.
- timer는 **enabled·active/waiting**, `Persistent=false`, `AccuracySec=1s`, calendar 06:40 UTC이며 monotonic 활성화 조건이 없다. D-Bus의 실제 다음 예약 값 `1792392000000000`과 root timer receipt가 일치한다.
- operation은 `apply_complete`, `intent=null`이다. 실제 변경 명령은 daemon-reload 2회와 **timer start 1회**이고 결과는 모두 0이다. service 직접 시작은 없다. service는 inactive/dead·시작 timestamp 없음, timer의 LastTrigger는 비어 있다.
- 사용자 service/timer **두 mask 유지**, manual 비활성, recurring은 승인한 scope로 활성이다. 활성화 이후 새 T05 run은 없고 기존 6개 run·원래 완료 plan/journal·실패 이력은 보존됐다. 미완료 정리 차단 대상은 없다.
- 보호 입력 **50개**, 기존 근거·등록·plan/journal/run **24개**, 같은 boot와 Chrony/두 supervisor PID·invocation·active 상태가 모두 유지됐다. 정상적인 새 근거 추가를 금지하거나 baseline·완료 상한·생성 순서를 재등록하지 않았다.

추가 backup·전체 데이터 검사/복원·장애 주입·확인용 수동 삭제는 실행하지 않았다. 이번 사후 대조는 설치 파일과 작은 운영 기록의 읽기 대조이며, 전체 checkpoint 무결성 검사를 반복한 것이 아니다.

## 남은 운영 관찰

| 단계 | 현재 판정 | 다음 확인 |
|---|---|---|
| 설치·반복 권한·timer 활성화 | **완료** | root receipt·active grant·실제 다음 예약 확인 완료 |
| 첫 실제 예약 평가 | **대기** | 10/19 15:40 KST 이후 실제 system timer 출처·invocation·새 plan/run, 0건/보류/실패/완료 구분 |
| 첫 자연스러운 자동 삭제와 보존 | **대기** | 자연 발생한 후보의 정확한 삭제, 보호·제외 사본 보존, journal·근거 대조 |

예약 시각은 시작 조건이며 작업 완료 시각이 아니다. 0건이나 exit 75, systemd의 success만으로 삭제 완료로 표시하지 않는다. 첫 예약에 후보가 없거나 당일 보존·잠금 조건으로 보류되면 그 실제 이유를 남기고, 자동 삭제 관찰은 후속 자연 예약에 남긴다. 확인을 위해 사본을 추가 생성하거나 직접 service/apply를 시작하지 않는다.

기존 U≤5초·보수적 72시간+60초·후행 검증 사본 3개·당일 backup/restic·유지 사본 무결성·inventory·잠금 조건을 유지한다. 매 예약은 현재 사본으로 새 계획을 만들며 완료한 수동 plan과 EXDEV scope_transition을 재사용하지 않는다. 확정된 사전 잠금 보류만 다음 예약에서 재평가하고 미완료 삭제·불명확 기록을 새 계획으로 우회하지 않는다.

중지·원복은 [고정 절차](operation-review.md#중지·원복)를 따른다. timer를 먼저 막고 최대 600초 정상 종료를 기다리며, 초과하면 실행 중 grant·유닛을 건드리거나 업무를 죽이지 않는다. 정상 종료 뒤 반복 비활성·시스템 두 mask·enable 링크 부재로 닫고 새 기록은 보존한다. stop/rollback 뒤 같은 apply 승인으로 다시 활성화하지 않는다.

## 보존한 승인·인증 대기 이력

10/18 22:56 KST 최초 읽기 대조는 통과했지만 도구 세션의 `sudo -n`은 비밀번호 필요로 Python 실행기 전에 종료됐다. 당시 설치/authority/시작 요청 부재와 네 mask·비활성 grant 유지도 확인했다. 이후 사용자가 관리자 터미널에서 승인 명령을 실행해 위 활성화로 이어졌다.
[직전 check·인증 실패 원본](preapply-execution-20261018.json), [당시 후속 대조](post-sudo-blocked-readonly-20261018.json).
이 기록을 성공으로 덮어쓰거나 당시 승인 대기를 현재 상태로 표시하지 않는다.
