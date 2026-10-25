# 해야 할 일

최종 정리: **2026-10-25**. 현재 진행할 일은 이 파일에서 관리한다.
최신 [10/25 자동 정리·보관 복구·공간 관리 마일스톤](docs/milestones/2026-10-25-retirement-and-archive-capacity-recovery.md)은
이전 기준 10/17 00:26 KST 이후 T05 수동/자동 정리 완료, T16 운영 검증 완료, T17 조사,
T18 장애 복구·출력 공유 효과와 T19의 제한된 추가 공간 회수를 정리한다.
운영 근거의 마지막 기준은 **10/25 20:15 KST 사후 대조**이며, Git 정리는 새 운영 관측이 아니다.
이전 [10/17 예약 업무 이전·시각 정책·요청 복구 마일스톤](docs/milestones/2026-10-17-scheduled-jobs-clock-policy-and-recovery.md)은
이전 10/11 18:20 KST 기준 이후 T13 완료·시험 정리, T15 운영 검증, T05 근거 축적과 T16 적용·Decimal 보완을 정리한다.
**T16 1차 운영 검증은 10/18 23:43 KST 완료했다.** 10/17 정오 10건·저녁 15건의 실제 처리와 중복 방지, Decimal 요청 복구·정기 backup/T05/restic을 연결했다. [완료 범위·원본·잔여 후속](reports/baseline/scheduled-clock-operating-review-2026-10-18/summary.md). 기존 OI 아카이브 점검은 T17, 보조 기록 보존과 나머지 시각 정책은 아래 조건부 목록에 남긴다.
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

**10/25 루트 개발 소스 통합·최종 검토 완료.** 수집 시각 근거·T13·T16·T05·T18을 `packages/`·`scripts/`·`integrations/`와 루트 회귀 검사에 합쳤다. 전체 977개 검사에서 발견한 T05 출력 보관 접점을 수정하고 관련 140개를 재검사해 남은 실패가 없다. [출처·수정·검증·한계](reports/baseline/source-integration-2026-10-25/summary.md). 최종 검토의 16개 연결 검사·21개 유닛 구문 검사와 코드 고정 범위·fixture·identity 후속을 [기록](reports/baseline/source-integration-2026-10-25/final-review.md)했고 README·구조·운영 안내에 연결했다. 기존 고정 소스·`/opt` 실행본·운영 설정은 유지하며, 새 통합본의 배포는 아래 조건부 검토에 별도로 남긴다.

**T18 장애 복구 범위는 10/25 완료했다.** 제한된 공간 회수·현재 수집/pack 복구, 출력 공유 적용·archiver 재개, 여섯 미보관 사본과 T05/전체 검사/요청 종결, 두 자연 no-op의 큰 본문 추가 할당 0 B를 확인했다. [완료·한계·별도 후속](reports/baseline/t18-incident-completion-2026-10-25/summary.md). T19의 다음 제한 묶음도 20:13에 **8.61GB 회수·가용24.55GB**로 완료했다. **다음 주력은 T19의 기록 보관·조회·용량 관리 설계**다. 과거 공백은 T18 후속, T05와 child 경계는 별도 운영 관찰로 관리한다.

**T05 첫 자연 예약·자동 삭제/보존은 10/19 완료했다.** [완료 범위·원본](reports/baseline/t05-recurring-operating-completion-2026-10-19/summary.md). T18의 보관 적체는 10/25 해소됐지만, timer 복원 직후 16:02의 시각 자격 검사 failed/1·삭제 0개는 별도로 보존한다. recurring/시스템 timer 활성·사용자 두 mask·manual 비활성을 유지하고 **10/26 15:40 자연 예약**을 관찰한다. 완료된 apply/수동 삭제·전체 검사를 반복하지 않는다. [현재 결과](reports/baseline/t18-archive-deploy-2026-10-25/operating-results.md).
**T15 1단계는 10/13 22:12 KST 운영 검증까지 완료했다.** 승인된 시스템 manager 이전 뒤 8개 업무의 실제 처리·수집기 유지, 정오 시도 10건과 정기 checkpoint `10dd2182…`의 T05 완료/순서·system restic `a2dbcdd4…` 저장/전체 검사/근거 bundle을 대조했다. [완료 범위·정확한 ID·별도 후속](reports/baseline/scheduled-system-operating-completion-2026-10-13/summary.md). 활성 목록에서 정리하며 업무별 시각 요구 분리·polling 복원은 아래 조건부 후속에 유지한다.
T13은 다음 주기·고정한 10/11 정오/저녁 대상의 후속 백업·복원까지 완료해 활성 목록에서 정리했다.
T04·T11 신규 v2의 운영 적용·첫 child 경계·예약 백업/복원과 T05 첫 자연 자동 삭제/보존 검증은 완료했다. 기존 v1 로그·T18 과거 공백 후속과 T19 용량 관리는 별도다.
기존 v1 로그 편입, 성능 계측 T14와 업무별 시각 요구 분리는 각 잔여 범위를 따르며 자동 착수·적용으로 해석하지 않는다.
T03은 실제 완료 근거를 남기고 활성 목록에서 제거했다.
시간원 교체(T10)는 현재 필수 후속에서 제외한다. reference age 600초 초과만으로 시간원 교체를 요구하지 않는다.
조사·고정 후보는 보존하고, 시간원 목록을 다시 검토할 조건은 아래 조건부 목록에 남긴다.
이 순서와 범위 기록은 새 정책 구현·운영 적용 승인을 대신하지 않는다.
예약 관찰은 실제 완료 시점에 맞춰 병행한다. 아래 시각은 모두 KST다.

- [ ] **T19 · 검증 비용·기록 보관·용량 관리 — 제한된 추가 회수 완료·장기 관리 범위 남음**
  - T18 장애 복구와 성공 manifest 중복 보관 억제는 완료했다. 과거 독립 출력은 자동 소급 정리되지 않았고, 임시 stdout 쓰기·반복 조회 및 일반 로그 증가 비용은 남는다. [T18 완료 범위·후속 연결](reports/baseline/t18-incident-completion-2026-10-25/summary.md).
  - **완료 근거:** 10/25 20:13, 승인 operation `7ce80073…`의 정확한 과거 출력 **1,022개**를 한 번에 공유했다. 두 원본·1,024개 경로·receipt 1,568개 보존, 임시 링크 0개, 선택 집합 할당 **8.61GB 감소**, 실제 가용 **15.95→24.55GB**. timer·권한·설정·원장·보호 프로세스를 유지했고 사후 대조까지 통과했다. [정확한 실행·감사·공간·한계](reports/baseline/t19-capacity-apply-2026-10-25/result.md). 완료한 apply/resume·전체 검증을 반복하지 않는다.
  - **남은 과거 출력:** 이번 두 manifest/1,022개 밖의 대량 출력은 미처리다. 추가 회수는 필요 용량과 참조를 보고 별도 목록·승인으로 정하며, 이번 승인으로 범위를 늘리지 않는다. 현재 여유를 확보했으므로 다음 묶음 실행을 자동 선행 조건으로 만들지 않는다.
  - **같은 설계 안에서:** 일반 진단 로그의 기간+총량 예산, 필수 receipt/미완료 참조의 보호와 마지막 링크 제거 조건, `/home`·`/mnt/backup`별 가용량/증가량 경고를 정한다. [관리 설계 초안](reports/baseline/archive-capacity-planning-2026-10-25/management-design.md). 로그 보관 날짜는 공유 stdout mtime을 사용하지 않는다. 필수 근거 무제한 증가나 전체 reports 일괄 삭제는 허용하지 않는다.
  - **그다음 작은 적용 단위:** 30분 새 사본 발견·재시도와 과거 manifest 전량 재대조를 분리한다. 재사용한 검증을 새 조회로 표시하지 않으며 신규 저장·전체 읽기 검사·T05 조건 변경은 이 초안에 포함하지 않는다. T14의 시간·I/O 계측은 재사용하되 성능 수치를 근거 없이 단정하지 않는다.
  - 보조 request/attempt/JSONL의 독립 백업·복구는 기존 조건부 항목을 유지한다. 이 통합 설계나 목록 등록을 실제 삭제·주기/정책 변경 승인으로 해석하지 않는다.

- [ ] **T17 · 10/15 UTC OI 아카이브 — 원인·공개 CHECKSUM 확인 완료, 수정 원본·자료 복구 대기**
  - **10/19 00:14 KST 읽기 전용 원인 조사 완료:** BTCUSDT 선물 OI archive 5m acquire `c24159c0…`의 10/16 18:00·20:00 두 시도는 같은 ZIP/CHECKSUM을 HTTP 200으로 받았다. 288개 날짜·5분 격자와 해시는 정상이지만 **금액 0인 행 14개 중 수량도 0인 행 8개** 때문에 기존 정책이 날짜 전체를 `quarantined_zero_oi`로 격리했다. 정상 revision/query가 없어 완료 proof와 reconcile이 거부된다. 당시/현재 설치본의 parser·판정 함수와 저장 issue를 대조했으며 T16 시각 정책 결함이나 단순 포인터 누락으로 분류하지 않는다. [원문·정확한 ID·판정 재현·한계](reports/baseline/t17-oi-archive-investigation-2026-10-19/summary.md).
  - acquisition은 기존 실패 2회·`needs_review`를 유지하며 10/16·17·18·19 refresh **4개**가 대기한다. 10/19 확인 주기 추가는 과거 완료 취소가 아니다. 운영 데이터/원장/설정·서비스를 변경하지 않았고 네트워크 요청·재시도·상태 해제는 하지 않았다. **조사 완료를 T17 자료 복구 완료로 표시하지 않는다.** [이전 상태](reports/baseline/scheduled-clock-operating-review-2026-10-18/summary.md#기존-oi-점검-필요는-별도-후속).
  - **10/19 21:13:52 KST 공개 CHECKSUM 대조 완료:** HTTP 200의 97바이트와 표시된 ZIP 해시가 기존과 동일했다. 이번 응답에서 수정된 ZIP 해시를 확인하지 못해 ZIP 다운로드·운영 재시도·상태 해제를 하지 않았다. 기존 task·두 attempt·issue 26·review 기록도 동일하다. 현재 ZIP 본문까지 새로 검증한 것으로 확대하지 않으며 **T17은 수정 원본 확인 대기**로 유지한다. [실제 응답·시각·한계·사후 대조](reports/baseline/t17-oi-source-check-2026-10-19/summary.md).
  - **같은 날짜의 REST도 품질 문제 확인:** issue 25에 연결된 저장 REST 응답은 금액 0인 행 6개를 포함한다. 기존 +5분 비교 가설에서 archive의 6개 행과 수량·금액이 일치하고, archive의 수량/금액 모두 0인 8개에 대응하는 행은 그 REST 응답에 없다. 정상 게시 REST는 00:00–13:40 UTC의 165행만 있으며 날짜 전체 완료가 아니다. 기존 10/19 품질 보고서도 이 날짜를 `blocked=288/comparable=0`으로 보류한다. 모든 과거 REST 응답을 전수 조사한 결과나 시각 의미 확정으로 확대하지 않고, 정상 REST로 즉시 대체할 수 없다는 근거로 연결한다. [원문·정확한 비교·한계](reports/baseline/t17-oi-rest-comparison-2026-10-19/summary.md).
  - **다음:** 다음 원본 점검에서 CHECKSUM 변경이나 수정 원본 근거가 확인되면 해당 ZIP만 별도 경로에서 품질·내용 차이를 검사한다. 통과한 경우에만 기존 review 절차의 정확한 한 acquisition 복구/재처리 범위를 정하고 운영 적용은 별도 확인한다. 원문·실패 이력·품질 조건을 보존하며 후속 refresh와 최종 proof까지 확인한다. 같은 자료의 무조건 재시도·새 자동 polling은 하지 않는다. T05 첫 자연 예약·자동 삭제 검증과 T16은 완료 상태를 유지하며, T19의 추가 공간 확보와 용량 관리를 우선한다.

## 별도 운영 관찰

- [ ] **T05 · 10/26 15:40 KST 자연 예약:** 10/25 16:02의 시각 자격 검사 failed/1·삭제 0개를 보존한다. 당시 당일 backup/restic·현재 시각·후행 관계·무결성·잠금과 새 plan/run의 결과를 확인한다. 후보 0·보류·실패·실제 삭제를 구분하며 수동 시작/reset·조건 완화는 하지 않는다. [근거와 범위](reports/baseline/t18-archive-deploy-2026-10-25/timer-restoration-followup.md).
- [ ] **수집기 · 다음 자연 child 경계:** 10/25 18:51 두 child가 complete=true로 종결된 뒤 19:04·19:31·**20:13**에도 다음 child는 `between_sessions`, `prior_hard_fault_blocks_stale_exception`으로 대기한다. 같은 supervisor·저장 이력과 시각 품질을 보존한다. 실제 자연 시작·현재 저장을 확인하며 시각 정책 변경·강제 재시작은 하지 않는다. 공간 회수 완료와 분리하며 용량 부족 재발로 단정하지 않는다. [이전 작은 관찰](reports/baseline/archive-capacity-planning-2026-10-25/review.md)·[적용 후 표본](reports/baseline/t19-capacity-apply-2026-10-25/post-application-audit.json).

## 이어서 처리

- [ ] **T18 후속 · 과거 수집 공백·interrupted collection — 복구 미완료**
  - 현재 수집·pack·archiver 복구와 분리한다. 10/19 용량 중단 구간, aggTrade 자동 보강의 20,000건/페이지 한도 partial, depth의 당시 되살릴 수 없는 호가 관측을 원본 이력으로 구분한다. [종료/재개 기록](reports/baseline/t18-capacity-recovery-2026-10-19/status.md), [기존 공백 대조](reports/baseline/t18-capacity-recovery-2026-10-19/interruption-and-gap-audit.json).
  - 다음 분석에서는 기존 근거와 시간/자료 인덱스의 제한된 조회로 정확한 구간·보강 가능성·중복 방지/예산을 확인한다. 전량 catalog 집계·전체 daily 재실행·원장 초기화는 하지 않는다. 실제 자료 재수집은 범위를 정해 별도 확인한다. T07·T12의 다른 품질/연결 문제와 혼합하지 않는다.

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
  - **10/17 T05 첫 수동 정리의 기존 검증 비용 관찰:** 16:50:43 시작 후 17:10에도 유지 사본 세 개의 전체 검사 중이며, 실제 pack/내부 파일 읽기와 CPU가 계속 증가한다. 17:10 누적 OS read_bytes 약 265.25GB, rchar 약 517.26GB는 전체 프로세스 값으로 단계별 비용 또는 고유 자료량과 같지 않다. [수동 정리 진행 표본](reports/baseline/t05-first-manual-resume-2026-10-17/progress-20261017T081005.json). 승인된 이번 검증을 생략하지 않고, 후속 계측에서 verify_files/verify_packed_layout와 사본 간 반복 읽기를 구분한다. 최종 17:15에는 사본/후보 검사 후 첫 rename에서 EXDEV로 실패했으며 총 24분 18초·CPU 21분 4.828초·peak memory 11.8GiB다. 실패와 성능 비용을 구분한다. [최종 원본](reports/baseline/t05-first-manual-exdev-2026-10-17/status.md).
  - **10/17 T05 첫 성공 실행의 실제 비용:** 19:58:42–20:32:33, 총 **33분 51.335초**, manager CPU **23분 44.345초**·peak memory **11.1G**다. worker 시작→새 journal attempt 시작은 **21분 9.054초**(사전 확인·유지 검사 등 포함), attempt 구간은 **12분 42.279초**(후보별 근거/시각/구성 검사·이동/파일 제거 등 포함)다. 각각을 순수 검증/삭제 비용으로 단정하지 않는다. restic 조회 명령 114회와 unlink 558,143개 기록이 있으며 단계별 I/O 세부 계측은 아직 없다. [성공 run·journal·manager 원본](reports/baseline/t05-mount-recovery-2026-10-17/completion.md).
  - **10/19 T05 첫 자연 자동 실행의 기존 기록:** 15:40:01–16:09:04 KST, 총 **29분 3.163초**·CPU **24분 23.617초**. worker→journal attempt **26분 8.519초**, attempt **2분 54.607초**, restic 조회 116회·unlink 135,165개다. 다단계 구간이며 순수 검증/삭제 비용으로 단정하지 않는다. 사후 전체 검사는 반복하지 않았고 I/O 세부 계측은 미실시다. [실제 근거](reports/baseline/t05-recurring-operating-completion-2026-10-19/summary.md).
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
| timer 일시 정지 후 복원과 예약 시각 검사 | **10/25 T18 실제 복원에서 확인.** T05 timer의 `Persistent=false`를 유지했어도 정기 시각을 넘겨 같은 manager에서 재활성화하자 16:02 실제 timer 요청이 생겼고 기존 15:40 guard가 failed/1·삭제 진입 0으로 거부했다. 다음 timer 복원 계약 개선 때 실효 trigger와 시각 거부의 처리/표시를 검토한다. 현재 T05 시각 조건·timer를 변경하거나 확인용으로 다시 시작하지 않으며, 다음 자연 예약 10/26 15:40 결과를 별도 관찰한다. [정확한 run·원본·상태](reports/baseline/t18-archive-deploy-2026-10-25/timer-restoration-results.json) |
| 배포 도구의 실제 manager 응답 계약 | **조건부 후속, 현재 배포 완료의 선행 조건 아님.** 다음 공통 배포 도구 정비 때 성공한 빈 job 출력, 생략된 Exec 명령 속성의 D-Bus 확인, 의존관계 집합 순서 차이를 회귀 입력으로 통합한다. 고정된 원래 도구/실패는 보존하며 현재 stop과 보완 rollback의 연결은 [명령·해시 문서](reports/baseline/t18-archive-deploy-2026-10-25/stop-rollback-tools.md)에 명시했다. 원래 apply를 재실행하거나 현재 업무 소스를 교체해 확인하지 않는다. |
| 정상적인 신규 사본 추가와 배포 승인 계약 | **10/25 별도 후속으로 기록, 미구현·미적용.** 코드·정책·경로·scope가 같은데 정기 사본 추가만으로 operation 재고정·재승인이 반복되는 문제를 다음 배포 계약 개선 때 검토한다. 변경하면 안 되는 입력과 정상적으로 늘어나는 자료 목록을 구분하고, 기존 항목 변경/누락·용량·미완료 요청 검사는 유지할 수 있는 범위를 정한다. **이번 T18 백업 재개의 선행 조건으로 추가하지 않으며**, 현재의 정확한 목록 검사나 새 operation의 별도 승인 조건은 우회하지 않는다. [이전 고정 계획의 보류 근거](reports/baseline/t18-archive-deploy-2026-10-22/deployment-status.md) |
| 연구용 호가 유효 구간 판정 | **10/10 검토 제안, 미구현·미적용**. 호가 연구 입력·내보내기 범위를 정할 때 검토하며 연속 수집 개선과 별개로 설계할 수 있다. 현재 운영 `status.healthy`는 누적 sync_losses, `replay.valid`는 collection complete를 요구한다. 이는 전체 수집의 완전성 판정이며 모든 부분 구간의 사용 불가를 입증하는 것은 아니다. 기존 partial·보고서·원문·전체 replay 판정은 보존하고, 별도 파생 결과에서 현재 Book 상태·구간 연속성/요청 top-N 충족·전체 손실 이력을 분리한다. 저장 anchor·증분 sequence/ID·범위·REST 대조 범위와 collection 연결로 경계를 입증하며, synced 하나·epoch 하나·짧은 수신 시각 차이로 정상 구간을 만들지 않는다. 손실 감지 시각을 실제 손실 시작으로 단정하지 않고 근거 부족 구간은 unknown으로 남긴다. 공백을 가로지르는 특징 lookback·미래 label 구간은 제외하고 복구 후 필요한 준비 구간을 둔다. T04의 로컬 시각 품질은 호가 품질과 별도로 연결하며 근거 available을 정상으로 해석하지 않는다. 사후 재구성 가능성과 당시 이용 가능했던 검증도 구분한다. 기존 0개·비어 있지 않은 top 부족 시험, T07의 실제 복구 사례와 T12 연결 경계를 검증 입력으로 활용한다. [현재 전체 재생·상태 판정](packages/marketdata/src/marketdata/depth.py), [실제 partial 후 후속 epoch](reports/baseline/t11-first-child-boundary-2026-10-09/summary.md) |
| 세션 경계의 연속 수집 | 미구현·별도 설계 검토. T04 운영 백업·복원 확인 후 수집 개선 후보로 검토. 한 시간 child 교체 외에 현재 Depth의 900초 소켓 재연결도 대상이다. 원문 수신과 파일/collection 경계를 분리하고, 필요한 연결 교체의 중첩·ID/내용 대조·anchor/재생 의존성·중복·저장 지연/강제 종료를 함께 검증한다. 15초 대기만 줄여 연속성을 달성했다고 처리하지 않으며 기존 partial·시각 이력·백업 계약을 유지한다. T12의 collection별 연결 경계도 함께 고려한다. [현재 실행 구조](docs/system-runtime-operations.md#실행-코드와-관리-주체) |
| 진단·백업 등 업무별 시각 요구 분리의 잔여 범위 | **8개 업무의 유효한 현재 관측 기반 정책과 실제 daily·정기 backup/T05/restic 연결은 10/18 T16 1차 운영 검증으로 완료했다.** [완료 범위·남은 한계](reports/baseline/scheduled-clock-operating-review-2026-10-18/summary.md)와 [8개 업무 계약](docs/scheduled-jobs-system-migration-contract.md)을 재사용한다. 후속은 **현재 시각 근거가 없는 상태의 진단 허용·독립 status 진입점 확대, 수집 supervisor 최초 시작/재시작·hard fault 회복·부팅 준비의 남은 600초 의존성**이다. T16 1차 완료로 재검토할 수 있으나, 해당 지연·진단 요구를 다룰 때 범위를 정하며 자동 착수·적용하지 않는다. 남은 경로의 `reference_stale` 표시·판정 사용을 정리하는 방향과 reference 시각·관측 시각·`reference_age_seconds`·오차 추정 원값 보존 합의를 유지한다. 초기 준비·조회 신선도·추정 오차·불연속·보류 후 재개를 경로별로 확인하고, 필드만 삭제하거나 과거 stale 이력을 고쳐 쓰지 않는다. 과거 **10/11 정오 약 67초 지연·18:05 대기**의 [근거](reports/baseline/t13-operating-review-2026-10-11/summary.md#기존-strict-gate의-대기는-별도), **10/12 정오 약 18분·공통 waiter 7,145.64초 대기**의 [근거](reports/baseline/t13-operating-completion-2026-10-12/summary.md#공통-waiter-대기는-별도-후속)는 완료 자료 상태와 구분해 보존한다. T05 삭제 정책은 그대로 두며 polling 복원은 남은 의존성 확인 뒤 별도로 적용한다. T14 계측은 이 후속을 기다릴 필요가 없다. [현재 계약](docs/system-runtime-operations.md#신선도와-다음-child-시작) |
| 예약 업무 보조 기록의 백업·회전·독립 복구 | **미구현·미검증 후속.** T16 1차 운영 검증은 완료했으며, 보조 로그 보관·회전 정책을 정할 때 범위를 검토한다. 완료를 구현 착수 승인으로 해석하지 않는다. `state/scheduled-clock-policy`의 request·고정 날짜/pins·attempt·판정 JSONL·명시적 복구 승인/원본/receipt와 요청이 참조하는 저장 근거를 대상으로 백업 범위·보관 기간·복원 연결을 정한다. 특히 네트워크 JSONL의 고정 byte 구간·해시는 미완료 요청의 복구 근거이므로 참조 중인 기록을 먼저 삭제하지 않으며, 이동·압축·회전 후에도 검증 가능한 연결을 유지하거나 명시적으로 검토한다. 백업에서 별도 경로로 복원해 원래 작업 범위·설정/코드 identity·완료 근거를 확인하고, 중복 실행 없이 재개/종결하거나 근거 부족을 거부하는지 검증한다. **현재 호스트 안의 중단 복구와 백업에서의 독립 복구는 별개**이며 기존 T13 원장/OI receipt/T05 bundle의 보존을 이 전체 기록의 보존 완료로 세지 않는다. T16 완료 때 이 항목을 함께 제거하지 않는다. [현재 한계·실제 복구 근거](reports/baseline/scheduled-clock-repair-deploy-2026-10-16/status.md#남은-확인과-원복) |
| 예약 업무의 잠금 보류 상태 표시 | **조건부 표시 개선 검토.** 10/17 restic의 실제 `lock_busy` 두 건은 공통 wrapper에서 `failed/75, work_failed`로 기록됐고, 각각 다음 timer에서 같은 request·identity·pins로 complete/0 종결됐다. 현재 재개·저장 안전성 문제는 확인되지 않았고 원래 실패 표시는 보존한다. 업무별 종료/status 표시를 정비할 때 잠금 보류와 실행 실패를 더 명확히 구분할지 검토한다. 이번 T16 점검에서 코드·정책을 바꾸지 않았다. [원본·정확한 재개](reports/baseline/scheduled-clock-operating-review-2026-10-18/summary.md#실패-이력과-자연-재개) |
| Chrony polling 기본값 복원 | 위 업무별 시각 요구 분리와 함께 **`maxpoll 8` → 기본값 `10`(1,024초)**으로 복원하고 `minpoll 6`(64초)은 유지하는 방향. 미구현·미적용이며, 기존 600초 실행 조건과의 충돌을 정리한 뒤 검증한다. 최대 간격은 고정 조회 주기가 아니므로 실제 polling은 Chrony가 범위 내에서 조정한다. 설정·프로필·연결 해시의 명시적 전환과 원복을 준비하고 기존 boot/closed/보정 증거를 보존한다. 실제 설정 변경·재시작은 후속 적용 범위로 별도 확인한다. [Chrony 4.5 설정](https://chrony-project.org/doc/4.5/chrony.conf.html) |
| 미동기화 부팅에서 원문 수집 | 미적용·구현 미확정. 현재 부팅 및 supervisor 최초 시작은 엄격한 준비 확인을 유지. 초기 수집 공백의 비용을 다시 검토할 때 미검증 시각의 데이터 계약과 초기 큰 보정 경계를 먼저 정함. 일정 시간 경과만으로 자동 통과시키는 변경은 승인되지 않음 |
| Chrony 시간원 목록 재검토 | 동기화 상실·추정 오차 한계 초과·가용 시간원 부족이 반복될 때 필요성을 판단. reference age 600초 초과나 응답 누락만으로 교체를 필수화하지 않음. [T10 조사·후보·제외 판단](reports/baseline/chrony-source-candidate-2026-10-09/summary.md) 보존 |
| 암호/복구 키트의 PC 외부 보관 | 외부 보관 수단을 정할 때. 현재 두 번째 SSD의 사본과 실제 사용 검증은 완료됨 |
| 다른 장비의 백업 데이터 사본 | 별도 장비·매체를 정할 때. 암호만 밖에 보관하는 것과 구분 |
| Proxmox 수집 VM 이전 | 새 VM 준비 후 고정 배포본·설정·마운트 UUID·권한·시각·부팅·복구와 pause/resume을 실제 확인 |
| 공개 아카이브 공개 시각 관측 | T13 스케줄러 운영 검증은 완료했으며 HEAD 관측기는 **구현·배포되었지만 `enabled=false`**다. 최초 실행 시각 조정에 실제 공개 구간이 필요할 때 고정한 자료·UTC 날짜·1–7일 관측 창을 별도 승인한다. 마지막 404→최초 200은 관측자에게 보인 구간이며 ZIP/CHECKSUM의 200을 내용 검증·수집 완료로 해석하지 않는다. [현재 운영 설정](docs/system-runtime-operations.md#대상별-daily-운영) |
| 루트 통합 소스의 고정 배포 전환 | **개발 통합과 별도인 미배포 범위.** 다음 운영 변경을 통합 소스로 배포하기로 할 때 최종 Python/import·유닛·전체 propose/scope·미완료 request·ledger 전환과 중지/원복을 새 고정 계획으로 확인한다. **10/25 최종 대조:** 기존 `seal_runtime.py`는 설치 패키지와 runtime만 포함하고 예약 업무가 읽는 루트 소스·scripts는 포함하지 않는다. 새 배포에서는 설치 패키지↔루트 소스의 상대 파일 목록·해시 일치와 실제 실행 파일·유닛·의존성의 전체 manifest를 검증한다. 기존 수집용 receipt만으로 통합본 전체가 고정됐다고 판단하지 않는다. 기존 운영 authority를 새 코드에 재사용하지 않으며 현재 `/opt` 실행본과 과거 operation 도구는 유지한다. [개발 통합 근거](reports/baseline/source-integration-2026-10-25/summary.md)·[최종 대조](reports/baseline/source-integration-2026-10-25/final-review.md) |
| 루트 회귀 fixture의 보고서 의존 분리 | **개발 검사 정비 시 검토, 운영 변경 아님.** 새 timer 바이트 회귀가 날짜별 `origins.json`을 직접 읽는다. 소스만 내보낸 환경에서 검사를 실행할 때 필요한 기대 timer 해시·출처를 `tests/fixtures/`로 고정하고, 기존 depth fixture의 보고서 참조도 같은 기준으로 정리한다. 원래 보고서와 감사 출처는 보존한다. 현재 전체 저장소에서는 검사가 통과하며 통합 완료를 취소하는 항목은 아니다. [확인 위치](reports/baseline/source-integration-2026-10-25/final-review.md) |
| 통합 소스의 예약 요청 identity 범위 | **개발 소스 변경·배포 단위 정비 시 검토.** 현재 모든 core·scripts·runtime을 묶으므로 연구 도구나 renderer 변경도 미완료 요청의 scope를 바꾼다. 누락을 막는 보수적인 기본값은 유지하고, 추후 실제 실행 의존성의 명시적 목록 또는 고정 manifest로 범위를 정리할지 검토한다. 업무 소스·설정 변경을 놓치거나 옛 요청을 자동 이전하도록 줄이지 않는다. 측정된 작은 파일 읽기를 운영 성능 장애로 단정하지 않는다. [측정·범위](reports/baseline/source-integration-2026-10-25/final-review.md) |
| T05 유닛 재생성기의 공통 부모 mount 반영 | **루트 renderer 구현 반영, 통합본의 운영 적용은 별도.** 루트 `scripts/render_retirement_units.py`는 source/staging의 공통 부모 쓰기 경계를 출력하며 정리 service·manual service와 읽기 전용 preview를 구분한다. 다음 통합본 배포에서 최종 유닛·권한과 실제 system namespace rename을 연결한다. 기존 고정 실행본의 옛 renderer와 과거 기록은 보존하고 현재 설치 유닛은 유지한다. [통합 검사](reports/baseline/source-integration-2026-10-25/summary.md)·[운영 근거](reports/baseline/t05-recurring-operating-completion-2026-10-19/summary.md) |
| T05 수동·자동 정리의 당일 백업 조건 분리 | **미구현·미적용 후속.** 첫 수동 정리 검증 이후 또는 날짜 경계로 인한 수동 점검 지연을 다룰 때 검토한다. 15:40 자동 실행을 위해 둔 같은 KST 날짜 조건을 미리보기·수동 정리에도 일괄 적용할 필요성을 재검토한다. 미리보기의 후보/보류 사유 표시, 실제 정리의 최근 검증 복구 지점·보존 조건, 자동 실행의 백업 주기 성공·실패·지연을 구분한다. 자료 기준 시각과 무결성 검사 시각을 혼동하지 않고, 오래된 성공 하나로 지속적인 백업 실패를 허용하지 않는다. 임의의 새 시간 한도를 지금 정하거나 기존 검사를 우회하지 않는다. U·72시간+60초·후행 3개·잠금·승인은 유지하며, T05 첫 정리 완료 때 이 후속을 함께 삭제하지 않는다. [관측과 진행 합의](reports/baseline/t05-readiness-2026-10-17/summary.md#관측-후-진행-합의) |
| restic 복구 지점 만료·공간 회수 | 필요 시 승인 정책에 맞는 만료 기록·보관 그룹·미리보기를 준비. T05의 완료 상한·기존 목록·선행 근거가 의존하는 snapshot도 확인하고, 아직 필요한 근거는 보존하거나 검증 가능한 후속 지점에 유지한다. 로컬 checkpoint 삭제 후에도 근거 연결이 복구돼야 한다. `forget`과 `prune`은 T05 범위 밖이며 별도 검증·승인. [T05 근거 보존 계약](docs/t05-checkpoint-clock-safety-design.md#6-근거의-저장restic-편입복원) |
| 장기 aggTrades 전체 backfill | T08 이후 연구 기간·예상 용량·수집 부하를 정한 뒤 진행 |
| 현물 aggTrade/depth 스트림 | 실제 현물 데이터 수요가 생길 때 범위 결정 |
| Freqtrade 이벤트/호가 특징 어댑터 | 연구에서 필요한 입력이 정해질 때. 수집 코어의 독립성 유지 |

외부 보관·VM 복구·보관 지점 정책의 기존 근거는 [restic 전환 문서](docs/restic-transition-and-retention.md)에 있다.
Windows/RTC/Chrony 전환, 실제 root 접근 시험, 이전 restic 예약 3/3·독립 복구는 이미 완료한 범위이므로
새 작업 목록에 다시 넣지 않는다. 완료 근거는 각 보고서에 보존한다.
