# 2026-10-25 자동 정리·보관 복구·공간 관리

**T05의 첫 수동 정리와 자연 자동 정리, T16의 합의된 1차 운영 검증을 완료했다.
T18의 용량 장애를 복구하고 성공한 manifest 출력의 중복 보관을 막았으며,
T19의 제한된 추가 회수로 운영 디스크 여유를 확보했다.** T17 자료 복구와 과거 수집 공백,
장기 기록·조회·용량 관리, 다음 자연 예약·child 관찰은 별도 미완료다.

범위는 [이전 마일스톤](2026-10-17-scheduled-jobs-clock-policy-and-recovery.md)의
마지막 운영 대조 **10/17 00:26 KST 이후 10/25 20:15 KST 사후 대조까지의 작업 전체**다.
마지막 커밋 `6308c7f` 이후의 파일 변경과 구분한다. 이번 정리에서 새 운영 실행이나 관측을 추가하지 않았다.

## 결과와 남은 범위

| 작업 | 완료한 범위 | 별도 후속 |
|---|---|---|
| T05 | 현재 scope 정합성, 첫 수동 10개 정리·보존, 반복 승인·시스템 timer 활성화, 첫 자연 자동 2개 정리·6개 보존 | 10/25 timer 복원 직후의 시각 자격 검사 실패와 10/26 자연 예약 관찰 |
| T16 | 8개 요청 종결·실제 daily 처리·중복 방지·Decimal 복구·정기 backup/T05/restic 연결 | restic 잠금 보류 표시, 남은 시각 정책·보조 기록 보존 |
| T17 | 정확한 OI 원문·판정·공개 CHECKSUM·저장 REST 비교 | 수정 원본과 한 acquisition의 근거 있는 복구 대기 |
| T18 | 제한된 공간 확보, 당시 수집/pack 자연 복구, 출력 공유 배포·archiver 재개, 여섯 사본 편입, 두 자연 no-op 효과 | 과거 수집 공백·interrupted collection |
| T19 | 정확한 과거 출력 1,022개 공유·8.61GB 회수·사후 대조 | 나머지 출력, 기간/총량 예산·필수 근거 보호·용량 경고·조회 비용 분리 |

## T05: 수동 계획에서 시스템 반복 실행까지

기간과 후행 사본은 이미 축적돼 있었다. [10/17 준비 대조](../../reports/baseline/t05-readiness-2026-10-17/summary.md)에서
실행을 막던 당일 backup/restic 조건과 현재 구성의 비활성 scope 불일치를 분리했다.
[승인한 비활성 문서 갱신](../../reports/baseline/t05-disabled-scope-2026-10-17/deployment-review.md)은
`enabled=false`·승인 필드 null을 유지했으며 삭제나 자동화 승인으로 취급하지 않았다.
당일 보존 완료 후 [정식 미리보기](../../reports/baseline/t05-formal-preview-2026-10-17/status.md)에서
현재 inventory로 새 목록을 확정했다.

첫 수동 plan은 `31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08`이다.
실효 guard·잠금 보류와 systemd namespace의 분리된 쓰기 mount에서 발생한 EXDEV를 단계별로 확인했다.
공통 부모 `ReadWritePaths=/mnt/backup/marketdata-historical`로 고정하고 실제 관리자 환경의 rename 시험을 거쳤다.
삭제 목록·보존 조건은 그대로 유지했다.

[구성 수정 operation `eced87a4…` → 재개 operation `821367d2…` → 성공 run `f57f334b…`](../../reports/baseline/t05-mount-recovery-2026-10-17/completion.md)을 연결했다.
앞선 구성 적용 뒤 마지막 재개에서는 구성 쓰기 0개·daemon-reload 없이 같은 계획의 **10개**를 처리했다.
최신 보호 3개와 편입 전 제외 3개, 실패 run·journal 이력과 T05 근거를 보존하고 임시 manual 승인을 회수했다.
558,143개는 제거한 checkpoint 파일 수이며 시장 자료의 논리 건수나 restic 삭제 건수가 아니다.
여유 공간 약 28.20GB 증가는 동시 업무를 포함한 관측이다.

[반복 후보](../../reports/baseline/t05-recurring-candidate-2026-10-18/summary.md)는
잠금 진입 전 명시적 Busy와 정상 최종 기록이 모두 있는 경우만 다음 예약에서 새 계획으로 재평가한다.
본문 진입 뒤 오류·감시/기록 실패·미완료 journal을 자동 우회하지 않는다.
기존 검사·실제 관리자 시험을 연결하고 [최종 적용](../../reports/baseline/t05-recurring-deploy-2026-10-18/status.md)을 별도로 승인받았다.

- 실행본: `t05-recurring-retirement-20261018-e57a7995f03fb301`
- 활성화 operation: `2c03a81f309d0eca6f03e24a5d84fb838d75598fd1d38dbc6dd2a4204a57ae6e`
- 첫 자연 run: `7712cfd499d845cd988cad6cf5b818a4`
- 새 자동 plan: `e886258a9eed7ac0f22e540cd04c60bf1d53b8819176d0c17b1b43cf652aec0f`

[10/19 15:40 자연 실행](../../reports/baseline/t05-recurring-operating-completion-2026-10-19/summary.md)은
10/15·16 사본 **2개**를 정리해 16:09:04에 complete/0으로 끝났다.
당일 backup ID·snapshot·전체 검사·T05 bundle, timer/job/invocation, 새 plan/run/journal을 연결했다.
최신 3개·편입 전 제외 3개와 빈 staging·미완료 없음까지 사후 대조했다.
수동 plan이나 EXDEV scope 예외를 반복 실행에 재사용하지 않았다.

## T16 완료와 T17의 자료 문제 분리

[10/18 완료 보고서](../../reports/baseline/scheduled-clock-operating-review-2026-10-18/summary.md)는
reference age 약 830초인 유효 관측에서 실제 daily 처리가 진행됐음을 확인했다.
정오 10건·저녁 15건, **25개 시도와 관련 작업 31개**를 구분하고 다음 평가의 시도 0건으로 불필요한 재처리가 없음을 대조했다.
8개 요청의 저장 참조·종료 판정·감시 종료, 기존 Decimal 실패를 보존한 복구, 정기 backup→T05→restic을 연결했다.
두 restic lock_busy/75는 같은 request의 다음 성공으로 복구됐지만 과거 failed 표시를 지우지 않았다.

[T17 원인 조사](../../reports/baseline/t17-oi-archive-investigation-2026-10-19/summary.md)는
10/15 UTC BTCUSDT OI archive acquire `c24159c0…`의 두 시도를 대조했다.
같은 ZIP/CHECKSUM·288개 정상 날짜 격자에 금액 0인 행 14개(그중 수량도 0인 행 8개)가 있어
기존 정책이 날짜를 격리했고 정상 publication/proof를 만들지 않았다. T16 결함이나 단순 포인터 누락으로 분류하지 않았다.
[공개 CHECKSUM](../../reports/baseline/t17-oi-source-check-2026-10-19/summary.md)은 기존과 같았다.
[저장 REST](../../reports/baseline/t17-oi-rest-comparison-2026-10-19/summary.md)에도 금액 0인 행 6개가 있고,
정상 게시 165행은 날짜 일부뿐이라 정상 REST로 전체를 즉시 대체할 근거가 없다.
`needs_review`·실패 이력·후속 refresh 대기를 유지했으며 자료 복구 완료로 표시하지 않았다.

## T18: 반복 출력의 용량 장애와 복구

[10/19 장애 조사](../../reports/baseline/collector-capacity-incident-2026-10-19/summary.md)에서
같은 성공 manifest의 독립 stdout이 반복 축적돼 운영 디스크를 잠식한 것을 확인했다.
보고 당시 사용 약 277.73GB 중 약 136.27GB가 반복 manifest 출력이었다.
T05가 정리하는 별도 backup 디스크의 여유와 운영 디스크의 용량은 분리했다.

[긴급 복구](../../reports/baseline/t18-capacity-recovery-2026-10-19/status.md)는
승인한 archiver timer 신규 예약만 보류한 뒤 종료된 성공 출력 **1,952개**의 경로·바이트·권한·receipt를 유지하며 공유했다.
약 15.45GB를 확보했고 수동 start/reset 없이 두 수집기와 pack이 자연 재개한 저장·시각 근거를 확인했다.
과거 관측 공백과 일부 보강 한도는 현재 복구와 분리했다.

업무 수정은 [archive_backups.py](../../scripts/archive_backups.py)에서 새 dump와 기대 해시 대조를 수행한 뒤
[archive_output.py](../../scripts/archive_output.py)로 성공한 정확한 `/backup.json` 출력만 공유하는 것이다.
repository·본문 해시·소유권·mode가 맞는 본문을 한 벌로 유지하고 각 출력 경로와 명령 receipt는 보존한다.
새 body/기존 body 모두 파일·부모 디렉터리 동기화를 거치고, 중단 후 재호출·경로 `..` 거부를 보완했다.
조회 빈도·전체 읽기 검사·batch 3개·T05 정책은 변경하지 않았다.

[관리자 사전 점검·고정 소스](../../reports/baseline/t18-archive-deploy-2026-10-22/review.md)를 보존했다.
정상 신규 사본 추가로 첫 operation은 보류하고 최신 입력으로 다시 승인받았다.
배포 도구의 빈 job 출력·생략된 ExecStartPre·관계 순서 처리 실패를 별도 복구 경로로 보완했다.
[실패·승인·최종 적용](../../reports/baseline/t18-archive-deploy-2026-10-25/deployment-status.md)은 원래 authority와 승인 시각을 유지한다.

- 소스: `restic-output-20261022-585c3e3686704e5c` (기존 고정 preserve Python 환경을 계속 의존)
- operation: `27dd5101bfa0b7005ce0a931b075bc9cccad5a5331e9350a30c4765cbf1ee225`
- 성공 recovery: `ab49fce3b04087ceb44874b0fde1c452bd457e4b7b9e255dfda4aa7b8ad90eb6`
- 변경 후 T05 scope: `2875edd74ccef07cd4a1f09917d147ab0a73cde0a6921a5f3f7cd9930344ef83`

10/25 16:02 설치·구성 6개·boot ledger·반복 승인과 9개 timer 복원을 완료했다.
이어서 [실제 보관 결과](../../reports/baseline/t18-archive-deploy-2026-10-25/operating-results.md)에서
10/20–25 여섯 사본을 3개씩 두 회차로 편입하고 사본별 snapshot·전체 검사·T05 bundle·요청 종결을 확인했다.
같은 snapshot 30개의 자연 no-op 두 회차는 실제 dump/해시 검증을 각각 수행하면서
**큰 본문 추가 보존 할당 0 B**를 기록했다. 최초 body 약 379.85MB와 작은 receipt/JSONL 증가를 구분했다.
[합의한 T18 장애 복구 범위](../../reports/baseline/t18-incident-completion-2026-10-25/summary.md)는 완료다.
임시 출력 쓰기·반복 조회·과거 독립 출력과 장기 보관 상한까지 해결한 결과는 아니다.

## T19 추가 공간 확보와 현재 경계

[장기 관리 초안](../../reports/baseline/archive-capacity-planning-2026-10-25/management-design.md)은
필수 근거/미완료 참조, 일반 로그의 기간·총량 예산, 파일시스템별 용량 경고와 조회 비용을 한 범위로 묶었다.
장기 설계 적용을 기다리지 않고 다음 제한 묶음을 정확한 목록으로 승인받았다.

[10/25 20:13 완료·20:15 사후 대조](../../reports/baseline/t19-capacity-apply-2026-10-25/result.md)의 operation은
`7ce8007352fb195b713f2efc067cf5712c453a826dc6d8d96657f3f49c30eb65`다.
두 원본을 유지하며 **1,022개**를 공유해 총 1,024개 경로·receipt 1,568개·내용·권한을 보존했다.
선택 집합의 실제 할당은 **8.61GB 감소**, `/home` 가용량 관측은 **15.95→24.55GB**다.
자연 archiver 종료를 기다렸고 timer·service·시각·정책·원장 변경 없이 한 번에 끝났다.
이는 전체 과거 출력 정리 완료나 로그 만료 정책 적용이 아니다.

다음 두 운영 관찰은 [TODO](../../TODO.md)에 그대로 남긴다.

1. **T05 10/26 15:40 자연 예약.** 10/25 timer 복원 직후 16:02의 실행은 시각 자격 검사 failed/1,
   plan/selection/삭제 진입 없음·삭제 0개였다. 과거 실패를 성공으로 바꾸거나 수동 재실행하지 않는다.
   `Persistent=false`만으로 재활성화 직후 요청이 없다고 보장하지 않으며 실제 원인 조사는 별도다.
2. **수집기의 다음 자연 child.** 18:51 두 child가 완료된 뒤 20:13 표본까지 다음 child는
   `prior_hard_fault_blocks_stale_exception`으로 대기했다. 공간 확보와 현재 시각 품질·시작 허용은 별개다.
   강제 재시작이나 정책 완화 없이 다음 실제 경계를 확인한다.

기존 시각 정책 잔여·polling, 보조 request/attempt/JSONL의 독립 백업/복구,
renderer 재생성·배포 manager 응답·정상 사본 추가 승인 계약·T14 계측도 TODO의 기존 후속 위치를 유지한다.
이번 [Git 보존 검사](../../reports/baseline/work-summary-2026-10-25/summary.md)는 위 코드와 근거를 정리한 것이며,
운영 적용·삭제·전체 검증을 다시 실행한 기록이 아니다.
