# 2026-10-17 예약 업무 이전·시각 정책·요청 복구

**T13의 고정 범위 운영 검증과 T15 시스템 manager 이전을 완료했다. T16은 업무별 시각 정책을 운영에 적용하고,
실제 OI 품질 결과의 Decimal 직렬화 결함을 보완해 원래 요청을 복구했다. 실제 daily 자료 처리와 다음 정기 backup/T05/restic 연결은 아직 남아 있다.**

범위는 [이전 마일스톤](2026-10-11-checkpoint-evidence-and-daily-processing.md)의 마지막 운영 대조 기준
**10/11 18:20 KST 이후 이번 정리까지 수행한 작업 전체**다. 마지막 커밋 `51a997c` 이후 Git 변경 목록과 구분한다.
최신 운영 근거는 10/17 00:17:06 수정본 적용 및 00:26까지의 후속 대조다. 이번 문서 정리는 새로운 운영 관측이 아니다.

## 완료와 남은 범위

| 작업 | 확인한 결과 | 남은 범위 |
|---|---|---|
| T13 | 다음 KST/UTC 경계·완료 보존, 고정 26개 시도의 checkpoint/restic 편입·선택 복원 일치 | 합의한 운영 검증 완료. 공개 시각 관측기는 비활성 |
| T13 시험 공간 | 미리보기 4개·선택 복원 1개를 부모 포함 정리 | 운영 원본·백업·근거 보존 |
| T15 | 8개 업무 system manager 이전, 실제 처리·정기 backup/T05/system restic 연결 | 합의한 1단계 완료. 새 구성의 실제 재부팅은 미실시 |
| T05 | 첫 정기 완료/순서 근거의 보존·독립 복원, 10/14까지 후행 3개 확인 | 기간·당일 보존·현재 구성의 새 미리보기·한 계획 삭제·반복 자동화 승인 |
| T16 | 시각 정책·요청 복구 구현, 관리자 환경 검증·운영 적용·Decimal 보완 | 실제 daily 자료 처리, 다음 정기 backup/T05/restic 연결 |
| 별도 후속 | 보조 기록 보존/회전/독립 복구와 남은 시각 정책 범위를 TODO에 분리 | T16 완료와 함께 삭제하지 않음 |

## T13의 고정 범위 완료와 시험 정리

[10/12 17:16 완료](../../reports/baseline/t13-operating-completion-2026-10-12/summary.md)는
10/11 정오 10건·저녁 16건의 시도, 관련 작업 32개와 자료 24,206행을 대상으로 했다.
다음 KST 확인 주기의 refresh/metadata 및 UTC 날짜 신규 작업을 기존 완료 acquisition과 구분했다.
전체 작업 수를 과거의 188개로 고정하거나 10/12 저녁 자료까지 완료 범위를 늘리지 않았다.

checkpoint `fd4bf8417a5203f4dfbd467118740ecc2441d642096471737727fcbcdad00fdf`와
restic `616ed4d6dbd3636b1970c5e623e3a770d42d31ae9ee3b9b26102e9675110a470`의
기존 전체 읽기 검사·명시적 지점의 선택 복원·원장 6개 테이블과 자료를 대조했다.
같은 복원에서 T05의 baseline·기존 사본 7개·신규 완료 1개의 근거도 원래 registry 없이 확인했다.
이는 T05의 삭제 승인과는 별개다.

[18:45 시험 정리](../../reports/baseline/t13-trial-cleanup-2026-10-12/summary.md)는 승인한 부모 폴더까지 포함한다.
파일 104개·폴더 158개를 삭제하고 여유 공간 약 22.13GB 증가를 관측했다.
운영 checkpoint/restic·보고서·T05 근거·자동 정리 mask는 보존했다.

## T15: 업무 정책을 유지한 시스템 manager 이전

[설치본 계약표](../scheduled-jobs-system-migration-contract.md)에 8개 업무의 판단 지점·보류·재개·완료 근거를 정리했다.
T13/T05는 기존 설치본을 유지하고, 나머지 workspace 업무는 동일 내용의 고정 실행본으로 분리했다.
명시적 경로·UID 1000·기존 공통 잠금, restic 실행 출처와 bootstrap inventory/T05 binding을 연결했다.
자료 대상·예약·strict gate·검증·재시도 의미는 이 단계에서 바꾸지 않았다.

[후보](../../reports/baseline/scheduled-system-migration-2026-10-12/summary.md)의 관련 240개 검사와
그중 152개의 별도 경로 재현을 구분했다. [관리자 시험·인계 도구](../../reports/baseline/scheduled-system-handoff-2026-10-12/summary.md)에서
실제 8개 환경·timer 출처·양 manager의 정상 종료/대기 취소·잠금·mask/링크 원복을 확인했다.
시험 의존성·사용자 시험 유닛 계정 지정 오류와 실제 CLI 진입점 보완을 원래 실패 기록과 함께 보존했다.
종료 대기 중 T05 후처리의 coordinator 잠금을 막지 않는 경계도 고정 계획에 반영했다.

운영 release는 `scheduled-manager-20261012-255d64b78ffa8328`, 승인 계획은
`6a71f295ec760f442edf0f88b04c6cf9378d9383fb9c45c0dc7c8c140eac3f2b`다.
[10/13 00:32:58 적용](../../reports/baseline/scheduled-system-deploy-2026-10-13/status.md) 후
기존 user service/timer 16개는 masked, system timer 8개는 활성화됐다.
같은 supervisor의 다음 child·실제 저장을 확인했고 Chrony·수집기를 재시작하지 않았다.

[22:12 운영 완료](../../reports/baseline/scheduled-system-operating-completion-2026-10-13/summary.md)에서는
정오 실제 시도 10건·관련 작업 12개, 새 checkpoint `10dd2182…`와 system restic `a2dbcdd4…`의
저장·전체 읽기 검사·T05 근거 bundle 조회를 연결했다. 완료 상한·baseline·선행 관계를 유지했다.
약 29.8KB bundle의 조회이며 시장 데이터 전체 복원을 다시 수행한 결과는 아니다.
정기 backup 75분 38초는 실측 자료로 남겼고 성능 최적화 완료로 표시하지 않았다.

## T16: 업무 판단과 실패 후 복구

[첫 후보](../../reports/baseline/scheduled-clock-policy-2026-10-15/summary.md)는 현재 관측이 유효한데
reference age만 600초를 넘는 경우의 일괄 차단을 8개 업무의 판단으로 대체했다.
날짜·기한·냉각·격자·pack 선정·backup 날짜·최신성을 관측 구간으로 판단하고 reference 원값은 보존한다.
수집기 최초 시작/회복·부팅 준비의 기존 정책, T05 삭제 정책과 Chrony polling 설정은 범위 밖이다.

후속 검토에서 확인한 실행·복구 경계를 두 후보로 보완했다.

- [감시·요청 복구](../../reports/baseline/scheduled-clock-policy-recovery-2026-10-15/summary.md): 죽은 감시 thread의 오류를 지우고 재개하지 않음,
  기록/프로그램 오류와 시각 보류 분리, 종료 실패에도 업무 결과·오류·최종 상태 저장 시도,
  설정 내용과 코드 identity, 업무별 저장 근거에 따른 종료 판정.
- [저장 근거·transaction](../../reports/baseline/scheduled-clock-policy-storage-2026-10-16/summary.md): SQLite 명시적 종료,
  원래 실패 분류·예산의 한 번 반영, OI의 정확한 fetch 행·network JSONL byte 구간을 완료 근거로 사용.
  이전 `/opt` 실행본이 latest를 갱신한 뒤에도 원래 요청을 중복 관측 없이 복구했다.

정책/복구 119개는 기존 90개와 추가 29개다. 관련 기존 회귀 97개와 preserve 연결 13개,
과거 무변경 preserve 146개의 재사용을 구분한다. 실제 중단 시험·업무 실행본 간 원복과
구성 전환·실제 systemd 보호 환경 검증도 각각 별도 근거다.
[10/16 관리자 시험](../../reports/baseline/scheduled-clock-handoff-2026-10-16/summary.md)은
8개 보호 환경·새 waiter·서로 다른 invocation의 동일 요청 복구·timer/수동 출처 구분을 통과했다.

## 실제 Decimal 결함과 한 요청 복구

[첫 T16 적용](../../reports/baseline/scheduled-clock-deploy-2026-10-16/status.md)은 10/16 22:23:51 KST 완료됐다.
여러 업무가 자연스러운 reference age 600초 초과에서도 진행했고 다음 child의 저장·시각 근거도 이어졌다.
다만 실제 Decimal 행을 포함한 OI 품질 보고서 저장 뒤 공통 encoder가 실패해 request는 pending으로 남았다.
보고서 생성 성공과 wrapper 종료 실패를 분리하고 T16을 미완료로 유지했다.

[보완](../../reports/baseline/scheduled-clock-result-fix-2026-10-16/summary.md)은 `run_job.py`와
`request_contract.py` 두 encoder에서 날짜/시각·Decimal·지원하지 않는 타입을 명시적으로 처리했다.
123개(기존 116+추가 7)와 실제 비어 있지 않은 OI 결과 경로를 확인했다.
수정본 재시작만으로 결과 포인터 없는 pending이 복구되는 것은 아니므로,
원래 한 요청의 정확한 scope·pins·보고서·실패 근거를 연결하는 별도 복구 계획을 만들었다.

[승인한 복구·수정본 적용](../../reports/baseline/scheduled-clock-repair-deploy-2026-10-16/status.md)은
10/17 00:17:06 KST 완료됐다. 현재 release는 `scheduled-clock-policy-20261016-686bb70c9ee4ef11`,
계획은 `8ce6ba51659556e5901e85c81694a9d585c0817dbc9a0c34c990547467217f31`이다.
113개 인계/복구/연결 검사와 실제 별도 프로세스 SIGKILL 6개 경계를 연결했다.
운영에서 설치 객체 5,523개·구성 13개·실효 유닛 18개·ledger를 대조했다.

원래 요청 `a702240f…`는 업무 호출 0회로 종결했고 원래 exit 1·보고서·품질 경고를 보존했다.
복구 시 도출한 코드 2를 과거 프로세스의 실제 종료 코드로 바꾸지 않았다.
timer 재개 뒤 별도 새 품질 요청 `fe3f28cd…`는 실제 보고서→seal→request/attempt→감시 종료까지 통과했다.
OI/품질 경고, daily의 기존 needs_review, 공개 관측기 비활성과 자동 정리 네 mask는 유지한다.
Chrony와 두 supervisor의 PID/invocation도 적용 전후 같았다.

## 다음 확인과 별도 후속

1. **T16:** 실제 daily 자료 처리와 다음 정기 새 checkpoint/T05 근거·같은 backup ID의 restic 저장/전체 검사를 확인한다.
   초기 daily/restic 처리 0건과 pack 대상 0개는 그 완료를 대신하지 않는다. 10/17 13:10 backup은 예약 시각이다.
2. **T05:** 후행 3개만으로 삭제하지 않는다. 보수적 72시간+60초·U≤5초·당일 backup/restic·현재 binding과
   새 미리보기·정확한 한 계획 승인·결과 확인·반복 자동화 승인을 구분한다. mask는 유지한다.
3. **시각 정책 잔여:** 현재 시각 미확인 상태의 진단, 독립 status 진입점, 수집기/부팅의 남은 600초 사용처,
   그 이후 별도 polling 복원을 유지한다. 이번 구현을 모든 시각 정책 분리 완료로 표시하지 않는다.
4. **보조 기록 보존:** request/pins·attempt·판정 JSONL·복구 승인/receipt와 참조 근거의 백업·회전·독립 복구는
   [TODO](../../TODO.md)의 별도 조건부 항목이다. 미완료 요청이 참조하는 byte 구간을 먼저 정리하지 않는다.
   현재 호스트의 중단 복구와 백업에서의 독립 복구를 구분한다.

이번 [Git 보존 검사](../../reports/baseline/work-summary-2026-10-17/summary.md)는 후보·원본 실패/성공 근거·문서의 정리다.
새 운영 업무·재부팅·전체 복원·시각 변경·자동 삭제를 수행하거나 기존 전체 시험을 반복한 결과가 아니다.
