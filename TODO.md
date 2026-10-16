# 해야 할 일

최종 정리: **2026-10-17**. 현재 진행할 일은 이 파일에서 관리한다.
최신 [10/17 예약 업무 이전·시각 정책·요청 복구 마일스톤](docs/milestones/2026-10-17-scheduled-jobs-clock-policy-and-recovery.md)은
이전 10/11 18:20 KST 기준 이후 T13 완료·시험 정리, T15 운영 검증, T05 근거 축적과 T16 적용·Decimal 보완을 정리한다.
T16의 실제 daily 처리·정기 backup/T05/restic 검증과 보조 기록 보존 후속은 남아 있다.
이전 [10/11 checkpoint 근거와 daily 실제 처리 마일스톤](docs/milestones/2026-10-11-checkpoint-evidence-and-daily-processing.md)은
이전 기준 01:04 KST 이후 T05 구현·설치·등록, T04 시험 공간 정리와 T13 실제 처리·정오 백업을 정리한다.
이전 [10/11 운영 검증과 daily 전환 마일스톤](docs/milestones/2026-10-11-daily-scheduler-and-operating-verification.md)은
이전 마일스톤의 10/9 20:17 KST 이후 첫 세션·T06·T11 경계, T02 24시간·T04 복원·T13 구현/배포를 정리한다.
그 이전 보강·백업·시각 정책·실제 적용과 실패/재개는
[10/9 시각 근거와 수집 정책 마일스톤](docs/milestones/2026-10-09-clock-evidence-and-capture-policy.md)에 보존한다.
T11의 첫 실제 child 경계는 **21:59 KST에 저장된 기록으로 확인 완료**했다.
두 스트림 모두 자연스러운 reference 지연 중 같은 supervisor에서 다음 child를 시작했다.
[첫 경계·종료 근거·정책 효과](reports/baseline/t11-first-child-boundary-2026-10-09/summary.md).
이 완료는 두 스트림의 다음 child 시작 정책에 한정한다. 미동기화 부팅 수집과 다른 업무의 시각 정책 확대는
미적용이며 아래 조건부 검토 항목으로 구분한다.
T02의 첫 24시간(10/9 00:11:18–10/10 00:11:18) 관찰도 완료했다.
[결과](reports/baseline/t02-first-24h-2026-10-10/summary.md)에 구버전의 gate 지연·전환 공백과
새 runtime의 stale 중 다음 child 4회씩 시작을 구분했다. 호가 손실은 T07로 남긴다.
T13의 10/9 UTC 공개 자료 네 종류는 **10/10 18:52:54 KST 기존 daily 재실행으로 편입·검증 완료**했다.
[4,608행·원본/Parquet 해시·8개 target complete](reports/baseline/t13-daily-rerun-2026-10-10/summary.md).
예약·재시도 정책은 **10/11 00:51 KST 운영 전환, 00:53 timer 활성화, 00:59 첫 실행·자동 재평가 확인까지 완료**했다.
[적용·중단/재개·처리 0건 결과](reports/baseline/t13-daily-deploy-2026-10-11/summary.md).
**T13은 10/12 17:16 KST 고정 범위의 운영 검증을 완료했다.** 다음 KST/UTC 경계·기존 완료 보존을 확인하고, 10/11 정오 10건·저녁 16건의 시도·관련 작업 32개·원문/표준 자료가 들어간 checkpoint `fd4bf841…`와 restic `616ed4d6…`의 전체 검사·선택 복원을 대조했다. 원장 6개 테이블과 24,206행이 일치했다. 10/12 저녁 자료는 범위에 추가하지 않았다. [완료 보고서·정확한 ID·잔여 후속](reports/baseline/t13-operating-completion-2026-10-12/summary.md).
T13 미리보기 4개와 선택 복원본 1개는 **10/12 18:45 KST 부모 폴더 포함 정리 완료**했다. 파일 104개·폴더 158개를 삭제하고 약 22.13GB의 여유 공간 증가, 운영 checkpoint/restic·보고서·T05 근거·정리 mask 보존을 [확인](reports/baseline/t13-trial-cleanup-2026-10-12/summary.md)했다.

T01 보강과 T03 백업 편입은 실제 근거를 남기고 완료했다. T10은 시간원 교체 완료가 아니라 현재 필수 후속에서 제외했다.
T06은 **21:42 KST 승인한 네 시험 경로의 수동 정리를 완료**했다. [삭제·보존·공간 확인 결과](reports/baseline/trial-artifact-retirement-2026-10-09/summary.md).
T04·T11은 **10/9 19:33:46 KST 운영 반영과 초기 저장·근거 등록을 확인**했고,
신규 v2 근거의 예약 백업·복원도 **10/10 15:24:19 KST 완료**했다.
[정확한 backup/snapshot ID·32개 artifact·7,111개 조회 구간 대조](reports/baseline/t04-operating-clock-restore-2026-10-10/summary.md).
해당 T04 복원 시험 폴더는 **10/11 17:59 KST 부모 포함 정리 완료**했다. 약 79.97GB의 여유 공간 증가와 원본·백업·검증 기록 보존을 [확인](reports/baseline/t04-restore-cleanup-2026-10-11/summary.md)했다.
기존 v1 로그 편입은 아래 T04 잔여 항목에 유지하며 전체 과거 시각 근거가 해결됐다고 표시하지 않는다.
[현재 운영 안내](docs/system-runtime-operations.md), [실제 적용·관측](reports/baseline/clock-runtime-deploy-2026-10-09/summary.md).

## 관리 방법

- 작업을 시작할 때 이 목록과 관련 근거를 읽고, 사용자의 최신 지시에 맞춰 우선순위를 확인한다.
- 작업 중 발견한 후속 과제는 바로 추가한다. 구현할 일, 실제 실행을 기다리는 관찰, 선택이 필요한 보류를 구분한다.
- 작업을 마칠 때 상태·다음 행동·근거 링크를 갱신한다. 일부만 끝났다면 남은 부분만 적는다.
- 완료 표시·활성 항목 제거 전에는 **최신 합의 범위 → 실제 결과 → 남은 범위 → 후속 기록 위치**를 대조한다. 구현·검증·운영 적용·관찰 중 필요한 단계가 남았는지 확인하고, 일부 단계의 완료를 전체 과제의 완료로 표현하지 않는다.
- 범위를 나누거나 미룰 때는 남은 내용을 먼저 후속 항목 또는 조건부 검토 목록에 남기고 완료 기록에서 연결한다. 이미 조건부 목록에 있으면 위치·재검토 조건 확인으로 충분하며, 이를 자동으로 활성 과제나 필수 구현으로 바꾸지 않는다.
- 다음 작업을 정할 때 조건부 목록의 재검토 조건도 확인한다. 조건이 갖춰졌다는 사실과 실제 실행 승인은 구분한다.
- 완료 조건을 충족하면 결과 문서에 근거를 남기고 활성 목록에서 제거한다. 완료 항목을 이 파일에 계속 쌓지 않는다.
- 예정 시각 경과·처리 0건·테스트 통과만으로 실제 운영 관찰을 완료 처리하지 않는다.
- 목록 등록은 운영 적용·삭제 승인을 대신하지 않는다. 이미 받은 승인과 보류 조건은 해당 작업 기록을 따른다.

## 먼저 진행

최신 순서: **T05 설치·기존 사본 등록·첫 정기 완료/순서 근거의 restic 보존/복원 확인 완료 → 후행 사본·보호 기간 확인 → 새 미리보기 → 별도 승인 단계**다. 10/15 확인에서 10/14까지 신규 후행 사본 **3개**를 확인했으며, 기간·당일 보존·정확한 삭제 계획은 별도다. 자동 정리는 계속 masked다.
**T15 1단계는 10/13 22:12 KST 운영 검증까지 완료했다.** 승인된 시스템 manager 이전 뒤 8개 업무의 실제 처리·수집기 유지, 정오 시도 10건과 정기 checkpoint `10dd2182…`의 T05 완료/순서·system restic `a2dbcdd4…` 저장/전체 검사/근거 bundle을 대조했다. [완료 범위·정확한 ID·별도 후속](reports/baseline/scheduled-system-operating-completion-2026-10-13/summary.md). 활성 목록에서 정리하며 업무별 시각 요구 분리·polling 복원은 아래 조건부 후속에 유지한다.
T13은 다음 주기·고정한 10/11 정오/저녁 대상의 후속 백업·복원까지 완료해 활성 목록에서 정리했다.
T04·T11 신규 v2의 운영 적용·첫 child 경계·예약 백업/복원은 완료했으며, T05는 별도 후속이다.
기존 v1 로그 편입, 성능 계측 T14와 업무별 시각 요구 분리는 각 잔여 범위를 따르며 자동 착수·적용으로 해석하지 않는다.
T03은 실제 완료 근거를 남기고 활성 목록에서 제거했다.
시간원 교체(T10)는 현재 필수 후속에서 제외한다. reference age 600초 초과만으로 시간원 교체를 요구하지 않는다.
조사·고정 후보는 보존하고, 시간원 목록을 다시 검토할 조건은 아래 조건부 목록에 남긴다.
이 순서와 범위 기록은 새 정책 구현·운영 적용 승인을 대신하지 않는다.
예약 관찰은 실제 완료 시점에 맞춰 병행한다. 아래 시각은 모두 KST다.

- [ ] **T16 · 업무별 시각 요구 분리 — 운영 적용·Decimal 보완/한 요청 복구 완료, 실제 daily 처리·정기 백업 연결 검증 필요**
  - 10/15 승인 범위: 현재 Chrony 관측이 유효한데 reference age만 오래된 경우의 8개 업무 판단을 후보에 연결한다. 기존 계약표를 사용하고 자료 날짜·실행 기한·냉각·pack 선정·backup 날짜·상태 최신성과 보류 후 재개를 검증한다.
  - 운영 적용은 후보 승인과 구분해 아래 10/16·10/17 승인 계획으로 완료했다. 현재 시각 근거가 없는 상태의 진단 허용, 수집 supervisor 최초 시작/회복·부팅 준비의 600초 의존성, polling 복원은 후속으로 남긴다. T05 정책·기존 근거·정리 mask는 유지한다.
  - 첫 후보 `scheduled-clock-policy-20261015-90f97df305c662c2`의 52개 정책 검사와 기존 결과를 보존한다. 이후 실제 thread·임시 request로 무감시 재개, 기록 오류의 시각 보류 오분류, close 실패 시 최종 저장 생략을 격리 재현했다. 운영 재현은 아니다. [이전 후보·검토](reports/baseline/scheduled-clock-policy-2026-10-15/summary.md), [재현 원본](reports/baseline/scheduled-clock-policy-2026-10-15/review/probe-results.json).
  - **이전 수정 후보 `scheduled-clock-policy-20261015-5b02fc74867e5bab`**: 감시/기록 장애 고정과 thread 생존 확인, 명시적 시각 조건만 재평가, close 실패 뒤에도 업무 결과·종료 오류·request 저장 시도, 실제 업무 설정 내용 identity와 8개 CLI의 저장 근거별 종결을 구현했다. 종료 코드 2를 일괄 완료 처리하지 않으며, 저장 후 중단은 같은 근거를 재검증해 업무 재호출 없이 복구한다. 기존 후보 runtime 290개는 그대로 보존하고 당시 후보는 18개 변경·1개 추가다. [구현·요청 복구 계약](reports/baseline/scheduled-clock-policy-recovery-2026-10-15/summary.md), [정확한 파일·해시](reports/baseline/scheduled-clock-policy-recovery-2026-10-15/manifest.json).
  - **90개 정책/복구 검사, 기존 회귀 base 119·daily 82·preserve 146개, 17개 유닛 구문/순서 통과.** 실제 프로세스의 결과 저장 직후 SIGKILL 복구, 날짜를 넘긴 backup의 사본/T05 근거 보존, 설정 변경·결과 변조 거부까지 확인했다. provider·HTTP·systemd 출처는 해당 wrapper 연결 시험에서 대역이며 운영 적용 근거가 아니다. [검증·한계](reports/baseline/scheduled-clock-policy-recovery-2026-10-15/validation.json).
  - **10/15 저장·복구 추가 대조:** close 누락·OSError 복원 분류 변경·latest 갱신 후 복구 거부를 확인했다. 7개는 당시 동작 조사이며 수정 완료 검사에 합산하지 않는다. [이전 확인·재현 근거](reports/baseline/scheduled-clock-policy-recovery-2026-10-15/review-storage-boundaries/review.md).
  - **10/16 보완 고정 후보 `scheduled-clock-policy-20261016-544c61aca49f1355`**: SQLite 명시적 종료, pending failure 원래 오류/처리 분류 보존, OI의 정확한 fetch 행·network의 JSONL 고정 구간/해시 연결을 완료했다. 실제 이전 /opt CLI가 같은 시험 저장소의 latest를 갱신한 뒤 동일 후보가 원래 요청을 중복 관측 없이 복구했고, 원래 이력 누락/변조·모호한 receipt는 거부했다. runtime 변경 3개·추가/삭제 0개이며 이전 291개 해시는 보존했다. [구현·범위](reports/baseline/scheduled-clock-policy-storage-2026-10-16/summary.md), [최종 파일·해시](reports/baseline/scheduled-clock-policy-storage-2026-10-16/manifest.json).
  - **119개 정책/복구(기존 90+추가 29), 관련 기존 회귀 97개(base 15·daily 82), 17개 유닛 구문/순서 통과.** preserve 기존 회귀 146개는 소스 무변경을 확인한 과거 근거 재사용이며 이번 검사 수에 합산하지 않는다. 추가 실패/완료 재중단 시험의 transaction·예산·HTTP 중복 방지도 확인했다. 이 검사는 아래 관리자 서비스/구성 전환 검증과 별개다. [검증·재사용·한계](reports/baseline/scheduled-clock-policy-storage-2026-10-16/validation.json).
  - **10/16 구성 전환·관리자 시험 준비:** 후보 runtime 291개를 유지하고 13개 구성 경로의 새 계획 `5e7473d9…`를 고정했다. 기존 T15 엔진으로 중단/재개/원복 48개, 실제 새 관측→구성 원복→이전 /opt 후속 관측→새 계획 재적용 2개, 시험 유닛/범위 11개를 통과했다. 3개 가상환경을 옮긴 뒤 8개 업무 driver도 통과했다. 이는 실제 systemd 보호 환경 검증을 대신하지 않는다. [결과·정확한 해시](reports/baseline/scheduled-clock-handoff-2026-10-16/summary.md).
  - **10/16 21:32 KST 실제 관리자 시험 완료:** 8개 보호 환경/UID 1000/진입점, 서로 다른 실제 invocation의 동일 request 복구, 새 restic wrapper의 timer·수동 출처 구분을 통과했다. 운영 전후 동일, 시험 12개 유닛·경로 정리, 오류 0개. [원본](reports/baseline/scheduled-clock-handoff-2026-10-16/system-preflight-result-48167a9e9eb24fdd.json).
  - **system→system 운영 인계 후보 고정:** 도구 `scheduled-clock-handoff-20261016-778dca2b72c6e867`, 최종 계획 `7eccd61f…`의 76개 설치/인계/중단·재개·원복 검사가 통과했다. 기존 runtime 291개와 전환 엔진을 유지한다. 종료 대기 중 coordinator EX·service mask 없이 기존 guard를 유지하고, pending 확정 뒤 no-PID 요청을 기록·인계한다. 22:00 KST 실제 호스트의 파일/소유권/실효 유닛/ledger 일치·T16 request 0개도 읽기 전용 확인했다. [결과·정확한 해시](reports/baseline/scheduled-clock-deploy-2026-10-16/summary.md).
  - **10/16 22:23:51 KST 실제 적용 완료:** 승인 계획 `7eccd61f…`의 root attempt·inner receipt와 설치 객체 5,523개·구성 13개·실효 유닛·ledger를 대조했다. 8개 timer 활성·이전 user mask·Chrony/두 supervisor 신원을 유지했다. OI/probe·daily·network는 자연스러운 reference age 600초 초과에서도 실행됐고 pack 요청과 새 restic timer 출처도 확인했다. daily는 처리 0건, restic도 processed=0이므로 신규 자료/백업 완료와 구분한다. 22:35 두 자연스러운 다음 child와 이전 collection의 종료까지 시각 근거도 확인했다. [실제 상태·원본 근거](reports/baseline/scheduled-clock-deploy-2026-10-16/status.md).
  - **운영 결함·격리 보완 당시:** 실제 Decimal을 포함한 OI 품질 보고서는 저장됐지만 공통 encoder 두 곳에서 기록 실패가 났다. JSONL은 finalization_failed, 디스크 request `a702240f…`는 pending으로 남았다. 새 소스 `scheduled-clock-policy-20261016-686bb70c9ee4ef11`에서 encoder 2개만 수정했고 123개(기존 116+새 7)가 통과했다. 기존 후보·/opt·미완료 요청은 보존했다. 보고서와 request의 정확한 관측/ID/hash 연결을 읽기 전용으로 확인했으며, 이후 실제 복구·설치는 아래 10/17 기록을 따른다. [보완·복구 경계](reports/baseline/scheduled-clock-result-fix-2026-10-16/summary.md).
  - **10/16 23:24 KST 영향 범위 재확인:** 8개 업무의 request/attempt/JSONL을 읽었고 동일 직렬화 오류는 기존 OI 품질 요청 한 건만 확인했다. 원래 request·보고서 해시는 일치하며 결과 포인터 없는 pending이다. backup은 새 wrapper 실행 전이므로 성공 범위에 포함하지 않는다. [파일별 읽기 대조·한계](reports/baseline/scheduled-clock-result-fix-2026-10-16/impact-review.json).
  - **10/16 요청 복구·수정본 전환 준비 완료:** 운영 계획 `8ce6ba51…`, 도구 `scheduled-clock-handoff-20261016-1437b0f0212d4c6c`를 고정했다. 원래 scope·ID·pins에서 정확한 저장 보고서를 검증하고 새 감시를 거쳐 요청 한 건을 종결한 뒤 수정본으로 전환하는 계획이다. 113개(기존 설치/인계 76+복구 22+연결 15) 통과, 실제 별도 프로세스 SIGKILL 6개 경계와 새 기록을 남긴 원복을 포함한다. 5,523개 설치 객체를 임시 경로로 복사해 세 variant import·두 encoder를 확인했다. 기존 실제 systemd 검사는 무변경 접점의 근거로 재사용했고 새 root 시험으로 세지 않는다. 당시 별도 승인 대기였으며 이후 승인·적용은 아래 10/17 기록을 따른다. [정확한 해시·검증·한계](reports/baseline/scheduled-clock-repair-deploy-2026-10-16/summary.md).
  - **10/17 운영 적용 승인·직전 대조 통과:** 사용자 명시적 승인 아래 같은 계획 `8ce6ba51…`의 boot·구성 13개·ledger·보호 파일/소유권/mask·원래 request/report/journal/scope·5,523개 설치 원본을 재확인했다. 00:14 KST 8개 업무 유휴·timer 활성·Chrony/두 supervisor 신원 유지·현재 시각 준비 통과 뒤 사용자 터미널에서 승인 명령을 실행했다. [읽기 전용 원본](reports/baseline/scheduled-clock-repair-deploy-2026-10-16/preapply-readonly-review-20261017.json).
  - **10/17 00:17:06 KST 한 요청 복구·수정본 `686bb70c9ee4ef11` 적용 완료:** 원래 `a702240f…`의 ID·identity·scope·pins와 보고서/exit 1 실패를 보존하고 업무 호출 0회로 종결했다. 실제 설치 5,523개·구성 13개·실효 유닛 18개·ledger를 대조했다. timer 재개 후 새 품질 요청 `fe3f28cd…`가 실제 보고서→seal→request/attempt→감시 종료까지 통과했고 UID 1000·실제 exit 2·품질 경고 유지다. OI/probe/daily/restic/network와 후속 pack 요청 종결도 확인했다. daily 처리 0건·restic 신규 저장 0건·pack 대상 0개는 실제 신규 자료/백업 검증으로 세지 않는다. Chrony/두 supervisor 신원·user mask·정리 네 mask·공개 관측기 비활성 유지. [현재 운영 결과·원본 근거](reports/baseline/scheduled-clock-repair-deploy-2026-10-16/status.md).
  - **다음:** 첫 실제 daily 자료 처리 및 다음 정기 backup/T05·같은 checkpoint의 restic 저장/전체 검사를 확인한다. 다음 backup 예약은 10/17 13:10 KST이며 완료 보장 시각이 아니다. 새 후보·요청 변경을 이전 승인으로 자동 적용하지 않는다. 원복은 이전 T16의 Decimal 결함도 되돌리며, 복구 완료 기록·품질 경고는 유지한다. 정확한 요청 해시·pins·근거를 검토하고 모호한 발행 반복·request 삭제·데이터 원복을 하지 않는다. [현재 수정본의 적용·중단·원복 절차](reports/baseline/scheduled-clock-repair-deploy-2026-10-16/deployment-review.md).
  - 현재 시각 미확인 진단 시작·독립 status 진입점 확대는 후속이다. 새 보조 JSONL/request/시도 이력 전체의 자동 백업/회전·독립 복구도 미완료이며, T13 원장/OI receipt/T05 bundle의 기존 보존과 구분한다. 아래 조건부 목록의 **예약 업무 보조 기록의 백업·회전·독립 복구**에 범위·근거 의존관계를 별도로 유지하며 T16 완료 때 함께 삭제하지 않는다. 로그·조회 비용은 T14에서 실제 운영 후 측정한다. 후보 보완을 전체 시각 정책 분리 완료로 세지 않는다. [기존 판단 계약](docs/scheduled-jobs-system-migration-contract.md).

- [ ] **T05 · 자동 checkpoint 정리의 상시 시각 검사 — 첫 정기 근거·보존/복원 확인, 후행 사본·보호 기간·삭제 단계 대기**
  - **[10/11 최종 보완 설계](docs/t05-checkpoint-clock-safety-design.md)**를 기준으로 **`t05-completion-evidence-20261011-c9d413cfe11ec0e7`**을 별도 경로에 구현했다. U 5초·72시간+60초·완료 직후 근거·실제 생성 순서·restic 보존/독립 복원을 연결했다. 고정 소스 151개·변경 18개이며 운영 workspace 실행 소스는 바꾸지 않았다. [새 후보·완료/잔여 범위](reports/baseline/t05-completion-evidence-2026-10-11/summary.md).
  - 기존 후보 **`t05-clock-retirement-20261011-3618649f76cfd2f4`**의 **108개 통과**, 별도 경로 144개 파일·39개 재현은 당시 결과로 보존하며 새 후보의 검사 수에 합산하지 않는다. [기존 후보](reports/baseline/t05-clock-retirement-2026-10-11/summary.md). 새 후보의 관련 **215개 최종 통과**(전체 실행 214개 통과·Git checkout 가정 검사 1개 실패 → 검사 보완 후 해당 1개 통과). 별도 경로 재설치·151개 파일/import 해시 대조·핵심 **58개 재현 통과**는 중복 합산하지 않는다. [새 검증 기록](reports/baseline/t05-completion-evidence-2026-10-11/validation.md). 실행 코드·보호/승인 조건을 바꿔 검사 실패를 통과시키지 않았다.
  - **시각 정책 보완:** 총 불확실성 U ≤ 5초와 `현재 관측 하한−완료 상한 ≥ 72시간 + 1분`을 별도로 요구한다. 고정 여유 60초는 더 기다리는 기간이며 U에 합산하지 않는다. 개별 100ms·500ms·100ppm 차단을 대체하고 reference_stale/600초 차단은 사용하지 않는다. 조회 3초·수명 15초·기존 감시/불연속 검사를 유지하고, 관측 하한을 외삽하지 않으며 긴 검증 뒤 최초 삭제 직전에 재조회한다. 작은 시각 이동의 추가 정밀 모델은 보류하며, U·1분 여유가 모든 미검출 이동을 보장한다고 표현하지 않는다. 원값·여유 성분·정책/부팅/provider 근거를 보존한다.
  - **완료·백업 연결:** 실제 완료 표시 확정 → 새 관측 → backup ID·manifest 해시와 완료 상한 연결 → restic 편입·복원 조회를 구현했다. 데이터 백업 성공과 시각 근거 실패를 구분하고 첫 유효 사후 상한을 재사용한다. 기존 manifest는 유지한다. **시험 정리 대상 로컬 사본 2개 제거·원래 control state 분리 후** 명시적 restic 지점에서 완료 상한·후행 관계·다음 정리 판단 재구성을 통과했다. 삭제 승인·미완료 journal은 자동 활성화하지 않는다. 운영의 기존 7개 사후 근거 등록과 **10/12 첫 정기 checkpoint의 완료 직후 근거·실제 restic 보존/복원 확인까지 완료**했다. [공유 복원의 T05 별도 판정](reports/baseline/t13-operating-completion-2026-10-12/summary.md#같은-복원의-t05-판정). 이전의 “백업 코드는 변경하지 않음”은 새 범위에 적용하지 않는다.
  - **최근 3개는 단순한 전환으로 보완:** 기존 완료 사본 목록을 먼저 확인·고정하고, 그 이후 생성을 시작한 새 사본의 순차 실행·ID 연결을 기록한다. 확실히 후행인 검증 사본 3개를 유지하고 기존 사본의 보수적 72시간 + 1분도 지나면 정리를 검토할 수 있다. 과거 사본끼리의 전 순서나 별도 순번 시스템은 만들지 않는다. 기존 날짜 정렬의 보호 3개는 초기 보호이며 영구 고정하지 않는다. 연결 없는 사본·중복/복원본·전환 중 실행은 설계의 보수적 규칙을 따른다.
  - 10/11 **11:59 관리자 임시 시스템 유닛의 접근 시험은 통과**했으나 당일 checkpoint/restic 미완료로 `daily_not_today` 보류·후보 미산정·삭제 0개였다. 당시 전체 preview는 미완료였으며 아래 15:50 새 후보 결과와 구분해 보존한다. [원본](reports/baseline/t05-clock-retirement-2026-10-11/system-preflight-result-ae5d62d13d034b72.json).
  - **15:50 새 시스템 미리보기 완료:** 같은 backup `9849b0ef…`와 restic `fd78b31a…`의 당일 완료/전체 검사 근거 아래 preview·읽기 전용 inventory가 통과했다. **관리 대상 현존 7개 모두 보호·후보 0개**, 기존 등록 범위 이전 3개는 제외. 새 완료 상한/후행 근거가 아직 없어 보호된 결과이며 근거 등록·삭제·운영 설치 0건이다. reference age 1,654–1,681초에서도 유효한 U 약 0.147–0.149초로 평가했다. 운영 경로 142개·Chrony/두 supervisor PID/invocation·mask 유지, 시험 유닛/경로 정리 완료. 첫 실행의 namespace UID 기준 오류와 호스트 대조 보완은 기록에 보존했다. 후보·시험 코드·보호 조건은 그대로다. [결과·한계](reports/baseline/t05-completion-evidence-2026-10-11/operating-preview.md), [원본 대조](reports/baseline/t05-completion-evidence-2026-10-11/system-preflight-review.json).
  - **16:21 KST 설치·기존 7개 사후 근거 등록 완료, 17:22:52 두 실행 명령 전환, 17:24 최종 대조 완료.** 승인된 고정 release·설정·비활성 시스템 유닛을 설치하고 최종 경로의 새 inventory/등록 승인으로 baseline과 첫 사후 상한을 만들었다. available 7·missing 0, baseline `5ec056db…`, 등록 plan `0404b2e4…`다. 실제 소스 151개·환경 1,646개·일반 계정 import·최종 registry 쓰기를 확인했다. [실제 단계별 결과](reports/baseline/t05-evidence-deploy-2026-10-11/status.md).
  - 첫 활성화는 `After`/`Requires`의 같은 구성원이 다른 순서로 출력된 것을 오판해 중단·원복됐다. 실제 이전 두 명령 복원과 의존성 동일을 확인한 뒤 **출력 회귀 5개+기존 전환 11개 재검사=16개 통과**한 보완으로 재개했다. 원래 도구·실패 기록·보완 해시·receipt를 보존했고 후보/정책은 변경하지 않았다. 기존 base 링크·ExecStartPre 전체·예약·사용자 manager는 유지하며 정리 mask·Chrony/두 supervisor PID/invocation도 동일하다. 실제 업무를 중단하거나 새 backup을 요청하지 않았고, 17:24 restic job은 기존 시각 gate 대기 중이다. [중단·재개·현재 원복](reports/baseline/t05-evidence-deploy-2026-10-11/recovery.md).
  - **10/12 첫 정기 근거 확인 완료:** checkpoint `fd4bf841…`의 `after_completion` 상한 **14:21:46.296229 KST**, U **0.137841655초**를 manifest·backup ID에 연결했다. restic `616ed4d6…` 저장·전체 검사와 독립 선택 복원에서 baseline 1개·기존 7개·신규 1개의 object 9개 및 완료 상한/선후관계를 확인했다. 원래 registry·선행 checkpoint를 읽지 않고 복원 근거만으로 대조했다. [실제 완료·보존·복원 결과](reports/baseline/t13-operating-completion-2026-10-12/t05-restored-evidence.json).
  - **10/13 system 이전 후 두 번째 정기 근거 확인 완료:** checkpoint `10dd2182…`의 완료 직후 상한 **14:26:29.724714 KST**, U **0.1382557355초**와 같은 baseline·10/12 선행 관계를 확인했다. restic `a2dbcdd4…` 저장·전체 검사 후 지정 지점의 객체 10개를 읽어 기존 근거 9개 보존·완료 상한·순서를 대조했다. T15 이전은 T05 재등록/보호 기간 초기화를 하지 않았다. [실제 결과](reports/baseline/scheduled-system-operating-completion-2026-10-13/summary.md).
  - **10/15 01:40 KST 세 번째 후행 사본 보존 확인:** 10/14 checkpoint `8dba1987…`와 restic `94c79c5a…`의 저장·15:15:12 전체 검사 완료를 연결했다. 명시적 snapshot에서 작은 근거 bundle을 읽어 객체 11개·기존 baseline·9개 선행 관계를 재구성했다. **후행 사본 3개**이며 새 backup/전체 검사/대규모 복원을 실행하지 않았다. [정확한 ID·증거](reports/baseline/scheduled-clock-policy-2026-10-15/t05-observation/result.json).
  - **다음 확인:** 10/15 당일 정기 backup/restic 완료와 보수적 기간·근거 보존 조건을 확인한 뒤 현재 binding/scope의 삭제 없는 새 미리보기. 과거 비활성 grant를 재사용하지 않는다. 기존 사후 상한은 10/11 16:21:02.643–.730 KST이며 **현재 관측 하한**이 10/14 16:22:02.643–.730 KST 이후여야 한다. 그때도 잠금·정확한 계획·별도 승인이 모두 필요하다. T13 완료나 첫 근거 복원·예약 시각 경과를 삭제 가능 또는 T05 전체 완료로 세지 않는다.
  - 적용은 **별도 승인한 근거 생성 기능·시스템 `User=yoonhc` 환경 설치 및 기존 목록/사후 근거 등록 → 정기 백업으로 후행 3개·기간·근거 보존 조건 축적 → 새 미리보기 → 한 계획의 별도 승인·첫 수동 삭제/보존 확인 → 새 반복 승인·시스템 timer 활성화**로 나눈다. 목록 확정·근거 등록은 운영 상태 쓰기이며 미리보기에 포함하지 않는다. 새 미리보기에서는 당일 maintenance daily와 같은 backup ID의 restic 저장·전체 검사 완료도 요구하며, T13 완료나 예약 시각 경과로 대신하지 않는다. 조건을 앞당기려는 연속 백업·시각 변경은 하지 않는다. 기존 사용자 및 새 시스템 자동 정리 service/timer는 모두 masked이고 새 manual/recurring 승인도 비활성이다. 과거 grant·미완료 정리 자동 재개로 우회하지 않는다.
  - 다른 예약 업무의 시스템 manager 이전은 별도 T15로 완료했다. T05 삭제 정책·기존 근거는 그대로 유지하며 업무별 시각 정책, 수집·Chrony·RTC·부팅 보정 권한, T13/T14/T04 잔여 범위는 이 과제로 변경하지 않는다. 완료는 새 근거의 생성·보관·복원과 실제 승인 단계별 결과로 판단한다. [기존 보관 계약](docs/restic-transition-and-retention.md). restic 만료 시 근거 snapshot 의존관계 검토는 아래 조건부 항목에 남겨 둔다.

## 이어서 처리

- [ ] **T04 잔여 · 기존 v1 시각 로그의 불변 보존·백업 편입 — 미완료**
  - **신규 v2 범위는 완료**: 10/10 예약 checkpoint `4381041b…`, restic `764f183c…`, 전체 읽기 검사,
    별도 경로의 전체 추출·프로그램 복원 후 두 종료 collection의 artifact 32개·조회 7,111개 구간이 일치했다.
    [운영 완료 근거·원본 receipt·설정/수집 유지](reports/baseline/t04-operating-clock-restore-2026-10-10/summary.md).
  - 남음: 기존 v1 파일 목록과 닫힌 범위·해시를 고정하고 기존 검증·백업 경로에 원본 그대로 보존하는 방법을 정한다.
    실제 checkpoint·restic 편입과 복원 후 파일 해시 일치를 확인하기 전에는 완료 처리하지 않는다.
    과거 collection 연결을 추정 생성하거나 기존 received_at·partial·clock_usable 이력을 수정하지 않는다.
    근거가 없는 과거 구간은 unknown이며 이번 v2 성공을 v1의 자동 편입 성공으로 확대하지 않는다.
  - 신규 v2 후보·전환·원복·실제 적용의 완료 근거는
    [운영 안내](docs/system-runtime-operations.md)와 [최종 전환 검증](reports/baseline/clock-runtime-transition-2026-10-09/summary.md)에 보존한다.

- [ ] **T14 · 백업 파이프라인 단계별 성능 측정 — 기존 시간 분해 완료, I/O·CPU 세부 계측 미실시**
  - 10/9 기존 기록: Daily 62분 59초 중 pack/정리 1분 23초, `Store.validate` 53분 7초,
    backup 단계 약 8분 29초. manifest의 생성 구간 5분 25초만을 backup 단계 전체 시간으로 보지 않는다.
    restic 입력 검증 4분 21초·저장 1분 16초·전체 읽기 검사 66초이며, 해당 Daily의 누적 CPU는 68분 53초다.
    [단조 시각 phase 로그·기존 receipt·미측정 범위](reports/baseline/backup-pipeline-review-2026-10-10/existing-run-timing.json).
  - 추가 실제 총시간: 10/13 system daily **75분 38초**, 10/14 **81분 35.7초**. 전자는 [T15 완료](reports/baseline/scheduled-system-operating-completion-2026-10-13/summary.md), 후자는 T05 세 번째 사본과 운영 maintenance 기록에 연결된다. 이 총시간만으로 I/O 병목이나 개선 효과를 판단하지 않는다.
  - 다음: 정상 예약 실행에서 단계별 경과 시간·CPU·논리 읽기와 실제 디스크 읽기/쓰기·파일/pack 수,
    ZIP 반복 열기/목록 처리·Parquet 읽기·SQL 조회 비용, 잠금/시각 gate/예약 대기와 수집 저장 지연을 구분해 계측한다.
    지금 확보한 시간 분해만으로 디스크 I/O 병목이나 개선 배수를 확정하지 않는다. 측정하려고 운영 캐시를 비우거나 전체 검사를 중복 실행하지 않는다.
  - 우선 후보: 검증 범위와 현재 잠금·무결성 조건을 유지하면서 반복 읽기·pack 열기·동일 내용 계산을 줄이는 방법.
    새 snapshot이 생긴 archiver 실행 말미의 저장소 전체 검사도 성장 비용을 측정하되,
    검사 주기·범위를 바꾸는 정책은 보존/손상 발견 주기·정리 승인 조건과 함께 별도 검토한다. 부분 검사를 전체 검사 완료로 표시하지 않는다.
  - 완료: 실제 단계별 계측·수집 영향·재현 입력으로 우선순위를 정하고, 검증을 유지하는 개선 후보와 별도 정책 변경을 구분한다.
    성능 개선 코드·운영 설정 변경은 아직 적용하지 않았다. T04의 현행 전체 검사·복원 완료 조건은 유지한다.

- [ ] **T12 · 동일 event segment의 collection 연결 — 기존 코드 경계 사례 확인**
  - 거래소 ID·내용·로컬 received_at까지 완전히 같은 합성 입력을 두 collection에 반복하면 segment ID가 같아
    두 번째 collection의 event 연결이 누락되고 replay와 보고서가 달라진다. 기존 `/opt` 코드→같은 기존 코드에서도 재현했다.
  - 실제 운영 발생 여부는 미확인이다. T04·T11의 새 회귀로 단정하지 않고 원문·보고서는 그대로 둔다.
  - 다음: content 중복 보관과 collection별 연결의 계약을 좁게 확인하고, 필요한 최소 변경과 회귀 범위를 제안한다.
    이번 운영 전환 작업에 수정 기능을 추가하지 않는다.
  - 근거: [구분과 한계](reports/baseline/clock-runtime-transition-2026-10-09/validation-notes.md),
    [이전 코드 대조 결과](reports/baseline/clock-runtime-transition-2026-10-09/duplicate-fixture-control.json).

- [ ] **T07 · 10/9 신규 호가 범위 소진 후속 — 품질 이력 확인, 추가 분석 필요성 판단**
  - 00:20:43, collection `bc6bc9d2cb6149ba864f0d2003adf3af`, sequence `5580`의 `coverage_exhausted`다.
    같은 연결에서 복구됐지만 손실 이력은 남으며, 이번 기록의 근본 원인은 독립 재생으로 확정하지 않았다.
  - T02에서 failure_notice의 초기 관측과 최종 보고서 일치, 손실·partial 보존,
    재생 일치·valid=false와 이후 epoch 복구를 확인했다.
    [실제 저장·재생 대조](reports/baseline/t02-first-session-2026-10-09/summary.md).
    당시 외부 상태 조회의 공개 지연은 새로 측정하지 않았다. 남은 것은 추가 원인 분석의 필요성 판단이며,
    필요하면 이 사건의 저장 원문으로 범위를 좁힌다.
  - T11 첫 child에서도 **20:32:11**, collection `d2d18424592f4f549f5bda828e270759`,
    sequence `34626`의 `coverage_exhausted` 1건이 확인됐다. epoch 4의 후속 저장 뒤에도 partial·exit 2를 유지했고
    다음 child로 이어졌다. [경계·원문 대조](reports/baseline/t11-first-child-boundary-2026-10-09/summary.md).
    이 사건의 독립 원인 분석도 아직 수행하지 않았다. reference 지연을 원인으로 단정하지 않는다.
  - 첫 24시간 집계에서 **02:00:25**, collection `f8cddef376fe42a9af10c052b254a375`,
    sequence `28974`의 범위 소진도 연결했다. 실패 파일·failure_notice·후속 epoch 저장과 partial은 확인했고
    독립 원인 분석은 미수행이다. 첫 24시간 총 3건을 유지한다.
    [T02 24시간 결과·원문 표본](reports/baseline/t02-first-24h-2026-10-10/summary.md).
  - 완료: 확인된 처리·남은 불확실성을 기록한다. 기존 10/4·10/5 검증을 다시 열거나 요청 예산·범위 정책을 근거 없이 바꾸지 않는다.
  - 근거: [실제 사건과 복구 기록](reports/baseline/system-cutover-2026-10-08/post-reboot-summary.md).

- [ ] **T08 · aggTrades 공개 아카이브 수집·대조 — 미구현**
  - 일별/월별 ZIP·checksum·revision·출처를 보존하고 기존 WS/REST의 ID·가격·수량과 대조하는 경로를 만든다.
  - 완료: 공개된 실제 파일로 누락 보강·중복/충돌·미제공 상태·재실행·백업/복원을 검증한다.
    당시 WS 수신 시각 등 아카이브에 없는 필드는 만들지 않는다.
  - 이후 상시 수집 전의 **4개 공백, 1,901,391개 ID**도 실제 원천을 확인해 보강한다.
    2019년부터의 장기 전체 backfill 범위는 별도로 결정한다.
  - 근거: [보유 공백과 실제 아카이브 형식 확인](reports/baseline/aggtrade-coverage-2026-10-09/summary.md).

## 조건이 갖춰지면 검토

아래는 당장 실행할 작업이나 확정된 구현 약속이 아니다. 착수할 때 범위와 완료 조건을 구체화한다.
10/10 신규 v2의 T04 운영 백업·복원 조건은 충족했다. 아래 관련 항목은 검토할 수 있지만 구현·운영 적용 승인은 별도다.

| 항목 | 다시 검토할 조건·남은 범위 |
|---|---|
| 연구용 호가 유효 구간 판정 | **10/10 검토 제안, 미구현·미적용**. 호가 연구 입력·내보내기 범위를 정할 때 검토하며 연속 수집 개선과 별개로 설계할 수 있다. 현재 운영 `status.healthy`는 누적 sync_losses, `replay.valid`는 collection complete를 요구한다. 이는 전체 수집의 완전성 판정이며 모든 부분 구간의 사용 불가를 입증하는 것은 아니다. 기존 partial·보고서·원문·전체 replay 판정은 보존하고, 별도 파생 결과에서 현재 Book 상태·구간 연속성/요청 top-N 충족·전체 손실 이력을 분리한다. 저장 anchor·증분 sequence/ID·범위·REST 대조 범위와 collection 연결로 경계를 입증하며, synced 하나·epoch 하나·짧은 수신 시각 차이로 정상 구간을 만들지 않는다. 손실 감지 시각을 실제 손실 시작으로 단정하지 않고 근거 부족 구간은 unknown으로 남긴다. 공백을 가로지르는 특징 lookback·미래 label 구간은 제외하고 복구 후 필요한 준비 구간을 둔다. T04의 로컬 시각 품질은 호가 품질과 별도로 연결하며 근거 available을 정상으로 해석하지 않는다. 사후 재구성 가능성과 당시 이용 가능했던 검증도 구분한다. 기존 0개·비어 있지 않은 top 부족 시험, T07의 실제 복구 사례와 T12 연결 경계를 검증 입력으로 활용한다. [현재 전체 재생·상태 판정](packages/marketdata/src/marketdata/depth.py), [실제 partial 후 후속 epoch](reports/baseline/t11-first-child-boundary-2026-10-09/summary.md) |
| 세션 경계의 연속 수집 | 미구현·별도 설계 검토. T04 운영 백업·복원 확인 후 수집 개선 후보로 검토. 한 시간 child 교체 외에 현재 Depth의 900초 소켓 재연결도 대상이다. 원문 수신과 파일/collection 경계를 분리하고, 필요한 연결 교체의 중첩·ID/내용 대조·anchor/재생 의존성·중복·저장 지연/강제 종료를 함께 검증한다. 15초 대기만 줄여 연속성을 달성했다고 처리하지 않으며 기존 partial·시각 이력·백업 계약을 유지한다. T12의 collection별 연결 경계도 함께 고려한다. [현재 실행 구조](docs/system-runtime-operations.md#실행-코드와-관리-주체) |
| 진단·백업 등 업무별 시각 요구 분리의 잔여 범위 | **8개 업무의 유효한 현재 관측 기반 정책은 T16으로 구현·운영 적용했고, 실제 daily 처리·정기 backup/T05/restic 연결 검증은 활성 T16에 남아 있다.** [현재 적용 범위](reports/baseline/scheduled-clock-repair-deploy-2026-10-16/status.md)와 [8개 업무 계약](docs/scheduled-jobs-system-migration-contract.md)을 재사용한다. 후속은 **현재 시각 근거가 없는 상태의 진단 허용·독립 status 진입점 확대, 수집 supervisor 최초 시작/재시작·hard fault 회복·부팅 준비의 남은 600초 의존성**이다. T16 운영 검증 이후 또는 해당 지연·진단 요구를 다룰 때 범위를 정하며 자동 적용하지 않는다. 남은 경로의 `reference_stale` 표시·판정 사용을 정리하는 방향과 reference 시각·관측 시각·`reference_age_seconds`·오차 추정 원값 보존 합의를 유지한다. 초기 준비·조회 신선도·추정 오차·불연속·보류 후 재개를 경로별로 확인하고, 필드만 삭제하거나 과거 stale 이력을 고쳐 쓰지 않는다. 과거 **10/11 정오 약 67초 지연·18:05 대기**의 [근거](reports/baseline/t13-operating-review-2026-10-11/summary.md#기존-strict-gate의-대기는-별도), **10/12 정오 약 18분·공통 waiter 7,145.64초 대기**의 [근거](reports/baseline/t13-operating-completion-2026-10-12/summary.md#공통-waiter-대기는-별도-후속)는 완료 자료 상태와 구분해 보존한다. T05 삭제 정책은 그대로 두며 polling 복원은 남은 의존성 확인 뒤 별도로 적용한다. T14 계측은 이 후속을 기다릴 필요가 없다. [현재 계약](docs/system-runtime-operations.md#신선도와-다음-child-시작) |
| 예약 업무 보조 기록의 백업·회전·독립 복구 | **미구현·미검증 후속.** T16 운영 검증을 마무리한 뒤 또는 보조 로그 보관·회전 정책을 정할 때 검토한다. `state/scheduled-clock-policy`의 request·고정 날짜/pins·attempt·판정 JSONL·명시적 복구 승인/원본/receipt와 요청이 참조하는 저장 근거를 대상으로 백업 범위·보관 기간·복원 연결을 정한다. 특히 네트워크 JSONL의 고정 byte 구간·해시는 미완료 요청의 복구 근거이므로 참조 중인 기록을 먼저 삭제하지 않으며, 이동·압축·회전 후에도 검증 가능한 연결을 유지하거나 명시적으로 검토한다. 백업에서 별도 경로로 복원해 원래 작업 범위·설정/코드 identity·완료 근거를 확인하고, 중복 실행 없이 재개/종결하거나 근거 부족을 거부하는지 검증한다. **현재 호스트 안의 중단 복구와 백업에서의 독립 복구는 별개**이며 기존 T13 원장/OI receipt/T05 bundle의 보존을 이 전체 기록의 보존 완료로 세지 않는다. T16 완료 때 이 항목을 함께 제거하지 않는다. [현재 한계·실제 복구 근거](reports/baseline/scheduled-clock-repair-deploy-2026-10-16/status.md#남은-확인과-원복) |
| Chrony polling 기본값 복원 | 위 업무별 시각 요구 분리와 함께 **`maxpoll 8` → 기본값 `10`(1,024초)**으로 복원하고 `minpoll 6`(64초)은 유지하는 방향. 미구현·미적용이며, 기존 600초 실행 조건과의 충돌을 정리한 뒤 검증한다. 최대 간격은 고정 조회 주기가 아니므로 실제 polling은 Chrony가 범위 내에서 조정한다. 설정·프로필·연결 해시의 명시적 전환과 원복을 준비하고 기존 boot/closed/보정 증거를 보존한다. 실제 설정 변경·재시작은 후속 적용 범위로 별도 확인한다. [Chrony 4.5 설정](https://chrony-project.org/doc/4.5/chrony.conf.html) |
| 미동기화 부팅에서 원문 수집 | 미적용·구현 미확정. 현재 부팅 및 supervisor 최초 시작은 엄격한 준비 확인을 유지. 초기 수집 공백의 비용을 다시 검토할 때 미검증 시각의 데이터 계약과 초기 큰 보정 경계를 먼저 정함. 일정 시간 경과만으로 자동 통과시키는 변경은 승인되지 않음 |
| Chrony 시간원 목록 재검토 | 동기화 상실·추정 오차 한계 초과·가용 시간원 부족이 반복될 때 필요성을 판단. reference age 600초 초과나 응답 누락만으로 교체를 필수화하지 않음. [T10 조사·후보·제외 판단](reports/baseline/chrony-source-candidate-2026-10-09/summary.md) 보존 |
| 암호/복구 키트의 PC 외부 보관 | 외부 보관 수단을 정할 때. 현재 두 번째 SSD의 사본과 실제 사용 검증은 완료됨 |
| 다른 장비의 백업 데이터 사본 | 별도 장비·매체를 정할 때. 암호만 밖에 보관하는 것과 구분 |
| Proxmox 수집 VM 이전 | 새 VM 준비 후 고정 배포본·설정·마운트 UUID·권한·시각·부팅·복구와 pause/resume을 실제 확인 |
| 공개 아카이브 공개 시각 관측 | T13 스케줄러 운영 검증은 완료했으며 HEAD 관측기는 **구현·배포되었지만 `enabled=false`**다. 최초 실행 시각 조정에 실제 공개 구간이 필요할 때 고정한 자료·UTC 날짜·1–7일 관측 창을 별도 승인한다. 마지막 404→최초 200은 관측자에게 보인 구간이며 ZIP/CHECKSUM의 200을 내용 검증·수집 완료로 해석하지 않는다. [현재 운영 설정](docs/system-runtime-operations.md#대상별-daily-운영) |
| restic 복구 지점 만료·공간 회수 | 필요 시 승인 정책에 맞는 만료 기록·보관 그룹·미리보기를 준비. T05의 완료 상한·기존 목록·선행 근거가 의존하는 snapshot도 확인하고, 아직 필요한 근거는 보존하거나 검증 가능한 후속 지점에 유지한다. 로컬 checkpoint 삭제 후에도 근거 연결이 복구돼야 한다. `forget`과 `prune`은 T05 범위 밖이며 별도 검증·승인. [T05 근거 보존 계약](docs/t05-checkpoint-clock-safety-design.md#6-근거의-저장restic-편입복원) |
| 장기 aggTrades 전체 backfill | T08 이후 연구 기간·예상 용량·수집 부하를 정한 뒤 진행 |
| 현물 aggTrade/depth 스트림 | 실제 현물 데이터 수요가 생길 때 범위 결정 |
| Freqtrade 이벤트/호가 특징 어댑터 | 연구에서 필요한 입력이 정해질 때. 수집 코어의 독립성 유지 |

외부 보관·VM 복구·보관 지점 정책의 기존 근거는 [restic 전환 문서](docs/restic-transition-and-retention.md)에 있다.
Windows/RTC/Chrony 전환, 실제 root 접근 시험, 이전 restic 예약 3/3·독립 복구는 이미 완료한 범위이므로
새 작업 목록에 다시 넣지 않는다. 완료 근거는 각 보고서에 보존한다.
