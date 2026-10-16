# 예약 업무 시스템 manager 이전 후보

2026-10-12. **8개 업무 계약표 확정, 후보 구현·격리 검증 완료. 운영 이전은 미실시.**
이번 결과는 기존 업무 정책을 유지하는 1단계 후보에 한정한다.
업무별 시각 요구 분리·reference_stale 제거·polling 복원은 후속이다.

- 고정 후보: `scheduled-manager-20261012-255d64b78ffa8328`
- 후보 계획 ID: `70c1723f25b5eff078b483c1c2ff848a8eba7d6d6b40c0d930ca44d2c829217c`
- [업무별 판단·보류·재개·완료 계약표](../../../docs/scheduled-jobs-system-migration-contract.md)
- [정확한 변경·적용 전제·원복·미검증 범위](deployment-review.md)
- [시험 결과와 한계](validation.md), [소스·설정·환경 해시](seal.json)

## 후보에 구현한 것

8개 서비스와 timer를 시스템 manager용으로 구성했다. 업무 프로세스는 모두 `User=yoonhc`다.
기존 calendar·OnActive·Persistent·재시작/종료 조건·strict waiter/ExecStartPre를 유지했다.
T13 daily와 T05 backup/restic 설치본을 그대로 사용하고, OI 3종·pack·network의 workspace 파일
35개를 바이트 동일한 고정 실행본으로 옮겼다. 데이터·상태·잠금·암호 경로는 기존 것을 참조한다.

restic adapter는 기존 T05 archiver에 `manager=system`을 명시하고 새 실행 근거에 adapter 해시를 추가한다.
기존 실제 timer job·PID·invocation·trigger stamp 교차 확인은 유지한다. 과거 user receipt는 수정하지 않는다.

bootstrap adapter는 기존 사용자 유닛의 persistent mask 확인에 더해 새 시스템 업무·timer·waiter를 조사한다.
준비를 기다리는 waiter와 실제 업무를 구분한다. 기존 bootstrap의 시각 정책·초기 보정 프로토콜은 그대로다.
schema 2 프로필을 유지하여 이전 수집기 gate와 T05 reader가 새 연결 설정을 읽을 수 있게 했다.

설정 transaction은 동일 부팅의 정확한 계획·승인과 기존 coordinator 잠금을 요구한다.
중간에는 기존 reader도 거부하는 pending 상태를 기록하고, 설정·실효 유닛 확인 후 연결 해시와 이력을 확정한다.
boot ID·closed·보정 시도/결과·과거 관측을 보존하며 현재 시각 준비는 별도 결과로 남긴다.
원복은 유닛·설정·연결을 되돌리고 새 자료·원장·receipt를 남긴다.

**현재 transaction의 쓰기 실행은 명시한 시험 루트에서만 가능하다.** CLI는 계획 조회만 제공한다.
운영 root 쓰기와 두 manager의 정지·재개·timer 활성화를 묶는 인계 실행기는 아직 제공하지 않는다.
격리 host 모델을 실제 systemd 전환으로 보고하지 않는다.

## 검증 결과

| 범위 | 결과 |
|---|---|
| 전환·중단 재개·원복·기존 gate·새 inventory | **152개 통과** |
| 설치된 T05 reader·기존 archiver의 system manager 접점 | **8개 통과** |
| 그대로 동결한 OI/pack/maintenance/backup 코드의 기존 회귀 | **80개 통과** |
| native `systemd-analyze verify` | **18개 유닛 통과**, 임시 실행 경로로 치환한 구문/의존성 검사 |
| 별도 임시 경로에서 소스 41개·오프라인 non-editable 설치 재현 | **152개 재현 통과**; 추가 테스트 수로 합산하지 않음 |

서로 다른 검사 **240개**가 통과했다. 실제 root 유닛 기동, 운영 timer 출처, 실제 파일 권한,
관리자 간 인계/정상 종료는 아직 검증하지 않았다. 자연스러운 다음 운영 실행도 이번 시험 범위가 아니다.

## 운영 유지 확인

21:02:05 → 21:29:04 KST의 [읽기 전용 전후 대조](operating-comparison.json)에서 다음이 동일했다.

- 운영 파일/링크 44개, 사용자/시스템 유닛 목록, boot ledger 전체.
- Chrony와 두 수집 supervisor의 PID·invocation·active 상태.
- 사용자/시스템 자동 checkpoint 정리 service/timer **4개 mask**.
- 외부 설정/환경 6개 digest, 기존 T05/bootstrap Python 71개, 동결한 workspace 파일 35개.

공개 관측기는 기존 비활성 설정을 유지한다. 운영 DB에 Store/plan을 열거나 자료를 수정하지 않았고,
수집이 계속 쓰는 카탈로그 전체 해시의 불변을 요구하지 않았다. 추가 backup·restic 전체 검사·복원도 실행하지 않았다.
재현용 임시 경로는 정리했고 보고서 아래 후보 소스·환경·검증 근거는 보존했다.

## 다음 경계

다음은 별도 승인하는 관리자 사전 점검과 운영 인계 준비다. 실제 최종 경로의 일반 계정 실행,
system bus timer 출처, 보호 옵션, 양 manager의 실행/대기 job, 동일 잠금과 정상 종료를 확인해야 한다.
그 결과로 실제 설치·timer 링크·소유권을 포함한 인계 실행기와 같은 부팅의 운영 계획을 고정한다.

T05의 binding 해시 변경은 삭제 승인 scope에도 영향을 준다. 기존 baseline·완료/생성 순서 근거는
유지하고 재등록하지 않는다. 기존 비활성 grant를 새 scope의 승인으로 사용하지 않으며 정리는 계속 masked다.
실제 이전 뒤에도 **reference age 단독 strict 대기와 기존 상태 최신성 판단은 남는다**.
이번 후보를 업무별 시각 정책 변경 완료로 표시하지 않는다.
