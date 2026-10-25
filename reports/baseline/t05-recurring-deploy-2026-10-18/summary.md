# T05 반복 자동화 — 최종 전환 도구 준비

2026-10-18. **설치·활성화·중지·부분 원복 도구와 격리 검증을 완료했다. 운영 적용·반복 권한 부여는 아직 하지 않았다.**
현재 recurring/manual 비활성, 시스템·사용자 정리 service/timer 네 mask를 유지한다.
첫 수동 정리와 관리자 사전 점검을 반복하지 않았다.

| 식별 | 고정 값 |
|---|---|
| 후보 | `t05-recurring-retirement-20261018-e57a7995f03fb301` |
| 전환 도구 | `t05-recurring-operation-20261018-447cddda543bc6ed` |
| operation plan | `2c03a81f309d0eca6f03e24a5d84fb838d75598fd1d38dbc6dd2a4204a57ae6e` |
| 실행 진입점 SHA-256 | `eeb317e4dfb6b89471c3270527e1f213875002d3a97faeaf8a3b1ed8611ef949` |
| 기존 scope | `84a0b92ae6f8bf0e38ac10ecb4a2615a32af72aebf1c8c28d591c02087cf4983` |
| 검토할 설치 후 scope | `311ab16f019b4473185c487b219430c1fab4e90c4257f813bacfa696b1ba29a5` |

[정확한 계획·도구 6개 해시·before/after](operation-plan.json), [운영 검토와 명령](operation-review.md).
후보 소스와 이전 61개/실제 관리자 시험은 변경하지 않았다. 이번 도구는 그 후보를 배포하는 별도 코드다.

## 이번에 확인한 범위

- **전환 관련 73개 검사:** 설치 중단·재개, 구성/승인/enable 링크/timer 시작 경계,
  반복 적용의 중복 기동 방지, scope 불일치·잠금 경합·미완료 journal 거부,
  새 기록을 남긴 중지·부분 원복, 실행 중 oneshot과 대기 job 구분을 확인했다.
- 그중 **실제 별도 프로세스 SIGKILL 10개**는 설치 디렉터리·intent·payload 확정과 구성/승인/중지 경계를 다뤘다.
  systemd는 새 전환 시험에서 대역이다. 이를 새 운영 manager 시험으로 표시하지 않는다.
- 새 설치기로 **1,798개 객체(소스 152 + 환경 1,646)**를 별도 임시 경로에 복사했다.
  모든 내용/모드·link 대조와 UID 1000의 Python prefix/import·실제 CLI `--help`를 확인하고 시험 경로를 정리했다.
- **22:38 KST 실제 호스트 읽기 대조:** before 8개, 보호 입력 50개, 기존 근거·종료 기록 24개,
  네 mask·비활성 권한·boot/ledger·Chrony/두 supervisor를 고정 계획과 대조했다. 미완료 정리 0개다.

[검증 원본·도구/검사 해시·한계](validation.json), [검사 출력](transition-tests.txt),
[호스트 읽기 결과](preactivation-readonly.json).
기존 [61개 후보/도구 검사](../t05-recurring-candidate-2026-10-18/validation.json)와
[14:34 실제 관리자 결과](../t05-recurring-candidate-2026-10-18/system-preflight-review.json)는 재사용했다.
숫자를 더해 동일한 수준의 운영 검증으로 표현하지 않는다.

## 전환의 경계

최종 `/opt` 설치와 UID 1000 import → 비활성 grant 아래 유닛 교체 → 실제 fragment guard와
`propose`의 **예상 scope 완전 일치** → 승인 문서 확정 → 시스템 timer만 시작 순서다.
다른 scope를 자동으로 수용하지 않는다. retirement service를 직접 시작하지 않는다.

중지·원복은 timer의 새 요청부터 막고, 진행 중인 정리를 강제 종료하지 않는다.
종료 대기 중 coordinator/runner 잠금을 잡지 않는다. 설정 교체 구간에는 coordinator **SH**,
설정 디렉터리 EX와 기존 runner EX를 사용하며 경합하면 보류한다.
원복은 자동 정리를 비활성·mask로 끝낸다. 새 `/opt`와 run/plan/journal·완료/순서 객체는 보존한다.
구버전이 새 안전 보류 기록을 이해하지 못하는 제한은 그대로다.

`approved_at`을 준비 시각으로 미리 작성하지 않는다. 명시적 승인 명령을 처음 받은 시각과
operation별 감사 기록 경로만 고정된 비활성 제안에 채운다. 실제 활성 grant 바이트/해시는
운영 쓰기 전에 보존하고 재개 때 동일하게 사용한다. 나머지 필드와 scope는 변경할 수 없다.

## 남은 단계

**정확한 operation과 반복 승인 범위 검토·별도 승인 → 실제 설치/활성화 → 첫 예약 확인 →
자연스럽게 발생한 첫 자동 삭제/보존 대조**가 남았다. 최종 `/opt`의 root 소유권·실제 scope/guard는
적용 시 확인할 필수 조건이며, 임시 경로 복사 시험으로 완료를 주장하지 않는다.

15:40 KST·Persistent=false·Restart=no, U≤5초·72시간+60초·후행 검증 사본 3개,
당일 backup/restic·무결성·잠금·중단 조건은 유지한다.
첫 예약의 정상 0건/선행 조건 보류와 첫 실제 자동 삭제는 별도로 기록한다.
완료된 `31766af4…` 수동 plan·EXDEV `scope_transition`은 자동 경로에서 재사용하지 않는다.

기존 수동 계획·구성 수정·재개 승인의 연결은
[10/17 완료 보고서](../t05-mount-recovery-2026-10-17/completion.md)에 보존돼 있다.
renderer 보완, 수동/자동 당일 조건 분리, T14 성능 계측, T16의 남은 관찰은 TODO의 별도 후속이다.
