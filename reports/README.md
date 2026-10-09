# 검증 기록

[2026-10-09 시스템 전환 마일스톤](../docs/milestones/2026-10-09-system-runtime-and-cutover.md)이
이전 10/3 마일스톤 이후의 독립 복구·정리·호가 보완부터 시각 관리·운영 전환까지 묶습니다.
[같은 날 후속 마일스톤](../docs/milestones/2026-10-09-clock-evidence-and-capture-policy.md)은 그 기준 시각
00:47 이후의 aggTrade 보강·백업 편입과 T04·T11 구현·실제 적용을 다룹니다.
후보의 미설치·미검증 표시는 각 시험 당시 상태이며,
현재 적용 상태는 [시스템 서비스 운영 안내](../docs/system-runtime-operations.md), 남은 일은 [TODO](../TODO.md)를 따릅니다.

이번 Git 보존 범위와 고정 배포본 해시·기존 검사 결과·문서 링크 대조는
[작업 정리 검증](baseline/work-summary-2026-10-09/summary.md)에 기록했습니다.

`baseline/clock-runtime-deploy-2026-10-09/`는 T04·T11의 실제 운영 적용 기록입니다.
[요약](baseline/clock-runtime-deploy-2026-10-09/summary.md)에 반복 ExecStartPre 출력 파서 결함으로 인한 중단,
보완 코드·원래 계획의 해시 연결, 19:33 적용·초기 저장과 20:17까지의 시각 근거 관측을 남겼습니다.
전환 공백의 aggTrade 4,031개 자동 보강과 호가 원문 공백을 구분하며, 첫 child 전환·새 근거 백업은 아직 미완료입니다.

`baseline/clock-runtime-transition-2026-10-09/`는 최종 수집본 `65296619a374c325`와 전환 도구의 검증입니다.
[요약](baseline/clock-runtime-transition-2026-10-09/summary.md)에 종료 표본 순서 수정·305건,
전환 58건과 clean 재현, 실제 root 종료·재시작, 새 근거가 남은 상태의 이전 runtime 원복·백업 호환을 보존했습니다.
원래 후보·실패 기록과 고정 재현 입력도 포함합니다.

`baseline/clock-evidence-policy-candidate-2026-10-09/`는 T04·T11 최소 구현의 초기 고정 후보입니다.
[요약](baseline/clock-evidence-policy-candidate-2026-10-09/summary.md)에 collection 연결·불변 artifact·조회,
지속 supervisor와 stale 단독 예외, 304건과 격리 restic 복원 근거를 남겼습니다.
그 후보의 `26e7fd4bbd0a68a5`와 이후 운영 적용된 `65296619a374c325`를 구분합니다.

`baseline/chrony-source-candidate-2026-10-09/`는 미적용 시간원 후보와 설정 전환 도구입니다.
[요약](baseline/chrony-source-candidate-2026-10-09/summary.md)에 실측·Chrony 4.5 장애 관측·전환 도구 48건을 보존했고,
T10은 이후 활성 목록에서 제외했습니다. 600초 내 선택 변경을 Chrony의 보장으로 요구하지 않습니다.
검토용 설정·승인되지 않은 계획을 현재 운영에 적용한 것으로 취급하지 않습니다.

`baseline/chrony-reference-age-2026-10-09/`는 [응답 수신 격차와 기존 600초 gate의 영향 조사](baseline/chrony-reference-age-2026-10-09/summary.md)입니다.
당시 원문 관측과 시각·ID 연결 근거를 보존하며, 이후 실행 정책 변경은 T11의 별도 적용 기록을 따릅니다.

`baseline/aggtrade-gap-repair-2026-10-09/`는 [264,069개 누락 ID 보강·독립 파일 대조](baseline/aggtrade-gap-repair-2026-10-09/summary.md)와
[530개 파일의 실제 checkpoint·예약 restic 전체 검사 편입](baseline/aggtrade-gap-repair-2026-10-09/backup-inclusion.md) 근거입니다.
과거 WS 수신 시각·실패 보고서를 수정하지 않았으며 상시 수집 전 4개 공백은 남아 있습니다.

`baseline/aggtrade-coverage-2026-10-09/`는 aggTrade 카탈로그 전체 범위와 공개 아카이브 제공 확인이다.
[요약](baseline/aggtrade-coverage-2026-10-09/summary.md)에 상시 수집 이후 잔여 공백 1구간 264,069건,
초기 시험 사이 4구간·376회 복구 기록, 내부 공백의 REST 대조와 파일 12개 검증을 구분했다.
실제 일별/월별 ZIP 제공과 CSV header를 확인했으며, REST 48시간 제한이 아카이브 보존 제한은 아님을 명시했다.
전체 행을 다시 읽은 검사는 아니며 아카이브 수집·등록이나 보강은 아직 수행하지 않았다.

`baseline/system-cutover-2026-10-08/`는 사용자가 지금 전환 작업창을 승인한 뒤 시작한 실제 운영 전환이다.
[진행 기록](baseline/system-cutover-2026-10-08/summary.md)에 현재 날짜 누락 보강,
21:40 두 collector 저장 후 종료·20개 unit persistent mask, 21:48 최종 checkpoint·21:56 restic 전체 검사 완료를 남겼다.
21:57 첫 Windows 인계 준비를 확정했으며, 새 snapshot과 갱신 revision·종료 collection의 포함을 대조했다.
[첫 Windows 결과와 Linux 복귀 점검](baseline/system-cutover-2026-10-08/summary.md)을 추가했다.
Windows UTC flag·동기화는 사용자 확인, Linux 20개 mask·외부 동기화는 실제 조회로 구분했다.
두 번째 왕복 뒤 RTC UTC 유지·실제 RTC/시스템 근접성·외부 동기화·기존 보류를 다시 확인했다.
Windows 동기화 전 표시 시각은 미확인으로 남겼다. 설치기 옵션/별칭 순서의 두 중단과 해결을 보존했고,
23:52 고정 배포본·Chrony 설치/권한 검사, 23:57 시각 기준 통과·23:58 최종 재부팅 인계를 완료했다.
10/9 최종 재부팅에서 시각 준비 완료·step 0회·두 system collector 시작과 실제 저장을 확인했다.
[부팅 후 점검](baseline/system-cutover-2026-10-08/post-reboot-summary.md)에 첫 저장 subset의 ID/시각/해시 검증,
기존 업무 재개와 전환 공백의 aggTrade 264,069건 미복구를 구분했다. 초기 subset 이후 00:20:43 호가 범위 소진 1건도
자동 복구·품질 손실 유지로 기록했다. 첫 1시간 세션 종료·새 정기 백업은 후속이다.
자동 정리는 보류됐으며, 전환이 끝나도 상시 시각 검사 검증·승인 전에는 자동 재개하지 않는다.

`baseline/system-cutover-backup-followup-2026-10-08/`는 어젯밤 보강 자료를 포함한 다음 정기 복구 지점의 확인이다.
[요약](baseline/system-cutover-backup-followup-2026-10-08/summary.md)에 14:08 checkpoint 완료,
14:35 예약 restic의 1개 저장·14:41 전체 읽기 검사와 4종 4,608행의 실제 snapshot 내용 대조를 기록했다.
15:40 기존 자동 정리 완료와 restic 12개 지점 유지도 확인했다. 새 날짜 10/7 UTC 갱신 대기는 별도 항목이다.
운영 전환·시각 변경·서비스 제어 없이 기다리던 백업 후속 확인을 마쳤다.

`baseline/system-cutover-readiness-2026-10-07/`는 운영 전환 전 기존 daily 미완료와 최신 복구 지점의 후속 확인이다.
[요약](baseline/system-cutover-readiness-2026-10-07/summary.md)에 공개 파일 4종의 현재 제공 확인,
기존 daily 재실행으로 10/6 UTC 누락 4,608행 해소·정상 종료와 checkpoint/restic ID·기존 전체 검사 대조를 남겼다.
13:58 기준 사본에는 23:31 갱신분이 포함되지 않으므로 전환 전 새 사본의 저장·검사가 필요함을 구분했다.
고정 후보·설정·수집 프로세스·시각·정리 승인은 유지했으며 실제 전환·재부팅은 수행하지 않았다.

`baseline/system-runtime-root-preflight-2026-10-07/`는 고정 bootstrap 후보의 실제 관리자 접근 시험이다.
[요약](baseline/system-runtime-root-preflight-2026-10-07/summary.md)에 같은 보호 옵션에서 Chrony Unix socket·client 응답 소켓,
실제 yoonhc manager 조회, 파일/디렉터리 fsync·일반 계정 공유 잠금의 통과를 기록했다.
최초 `/run` noexec 배치 오류와 시험 경로 수정·재실행을 구분했고, 시험 unit/폴더 정리와 운영 보존을 확인했다.
[운영 전환 범위](baseline/system-runtime-root-preflight-2026-10-07/transition-review.md)는 별도 승인 대상으로 남겼다.
시험 전부터 존재한 daily update의 waiting_source/exit 2도 전환 전 확인 항목으로 분리했다.

`baseline/system-runtime-bootstrap-revision-2026-10-07/`는 위 bootstrap 후보의 재전송·소켓 권한·업무 재개 보완입니다.
[요약](baseline/system-runtime-bootstrap-revision-2026-10-07/summary.md)에 실제 chronyc 응답 유실 시 요청 2회→1회,
공통 읽기 전용 대기 뒤 8개 업무 재개, 업무 재시도 제한 유지, 297건 및 새 경로 297건 재현을 남겼습니다.
배포판 유닛과 후보 drop-in의 의존 관계 검사는 통과했으며, 당시 미완료였던 실제 root 접근은 위 후속 기록에서 확인했습니다.
당시 운영 소스·서비스·시각/RTC·정리 승인과 기존 후보는 유지했습니다. 이 고정 후보의 실제 적용은 위 10/8 전환 기록에서 확인합니다.
이전 후보의 중복 방지 검증은 프로그램 재호출 범위였고 chronyc 내부 재전송까지 확인한 것은 아니었습니다.

`baseline/system-runtime-bootstrap-candidate-2026-10-06/`는 UTC RTC 통일을 전제로 한 부팅 초기 보정의 새 후보입니다.
[요약](baseline/system-runtime-bootstrap-candidate-2026-10-06/summary.md)에 신규 37건+기존 248건,
깨끗한 경로의 285건 재현, 보정 intent 전후 SIGKILL·재시작 금지·실제 사용자 systemd 재시도를 기록했습니다.
RTC 확인 전 Chrony 시작 거부와 기존 user 작업 8개의 시작 검사를 준비했으며, 설치한 부팅은 hold합니다.
이전 후보와 운영은 보존했고, 실제 RTC/시각 변경·서비스 전환·재부팅은 하지 않았습니다.

`baseline/system-runtime-candidate-2026-10-06/`는 시스템 수집 서비스·Chrony·고정 배포본의 후보입니다.
[요약](baseline/system-runtime-candidate-2026-10-06/summary.md)에 신규 49건+기존 199건 통과와
깨끗한 경로의 248건 재현, 임시 systemd 재시도·종료와 시각 제어 없는 Chrony 시험을 남겼습니다.
듀얼부팅을 반영해 RTC 쓰기·자동 큰 보정을 제외했습니다. 운영 설치·시각 변경·수집 중지는 하지 않았으며,
시각 근거의 백업/연구 연계·자동 삭제의 영구 시각 gate는 별도 후속으로 구분했습니다.
[전환·되돌리기 검토](baseline/system-runtime-candidate-2026-10-06/deployment-review.md)는 당시 초기안이며,
이후 bootstrap 보완 후보와 실제 전환 기록으로 적용 범위를 확정했습니다.

`baseline/stream-startup-readiness-2026-10-05/`는 재부팅 뒤 네트워크·시각 준비 상태의 읽기 전용 점검입니다.
[요약](baseline/stream-startup-readiness-2026-10-05/summary.md)에 사용자 network-online target 부재,
수집 시작 3.93초 뒤 시스템 네트워크 준비·102.82초 뒤 최초 NTP 동기화, RTC 약 41초 오차를 남겼습니다.
두 스트림의 실행 전 준비 확인과 RTC 보완 범위를 제안했으며, 구현·운영 설정 변경·재시작은 하지 않았습니다.

`baseline/depth-status-followup-2026-10-05/`는 호가 상태 공개 패치의 첫 정기 종료와 저녁 운영 점검입니다.
[요약](baseline/depth-status-followup-2026-10-05/summary.md)에 종료 7개·증분 244,357개 저장 일치,
REST 2,464회 일치와 선택한 3개 재생·2개 원문 대조를 남겼습니다. 재부팅 interrupted·기동 전
네트워크 미준비 partial을 보존했고 첫 정기 종료 항목은 완료했습니다. 별도로 NTP 보정에 따른
수신 시각 약 41초 역행을 확인했으며, 운영 설정 변경 없이 시각·기동 조건 점검과 24시간 관찰을 남겼습니다.

`baseline/trial-artifact-retention-expanded-2026-10-05/`는 시험 저장소·캐시·상위 폴더까지 확대한 현재 정리안입니다.
[요약](baseline/trial-artifact-retention-expanded-2026-10-05/summary.md)에 정확한 네 경로·예상 회수 197.34 GB,
pilot 자료의 보존 원본 대조·복제 snapshot 7개 중복 확인·9/26 원래 사본 33,586개 파일 검증을 남겼습니다.
시험 전용 프로필의 기록 보관·해제와 키트 안내 추가도 적용 범위로 제안합니다. 첫 결과 파일 저장 오류와
정상 재검사를 구분했고, 실제 삭제·설정 해제·키트 변경은 0건입니다. 아래 172 GB 초기안을 대체합니다.

`baseline/trial-artifact-retention-plan-2026-10-05/`는 대용량 시험 산출물의 삭제 없는 수동 정리 계획입니다.
[요약](baseline/trial-artifact-retention-plan-2026-10-05/summary.md)에 추출·복원·재백업 12개 경로,
예상 회수 172.00 GB, 보존할 네 snapshot과 manifest 대조, 후보 밖 증거 복사를 남겼습니다.
실제 삭제는 0건이며 시험 저장소·키트·원본·기존 백업은 보존합니다. checkpoint 반복 승인을
이 후보에 적용하지 않으며, 10/6 첫 자동 정리 관찰 후 별도 수동 승인·재점검이 후속입니다.

`baseline/checkpoint-automation-activation-2026-10-05/`는 별도 사용자 승인에 따른 자동 정리 예약 활성화입니다.
[요약](baseline/checkpoint-automation-activation-2026-10-05/summary.md)에 19:38 활성화,
실제 사용자 서비스 환경의 삭제 없는 점검·Linger/마운트/저장소 ID 일치·고정 승인 범위를 남겼습니다.
첫 예약은 10/6 15:40 KST이며, 19:53 확인까지 정리 실행·삭제는 0건입니다.
첫 예약 결과와 정리 후 다음 새 daily/restic 연결은 미완료 관찰로 구분합니다.

`baseline/checkpoint-automation-2026-10-05/`는 매일 15:40 KST 작업용 사본 자동 정리 후보입니다.
[요약](baseline/checkpoint-automation-2026-10-05/summary.md)에 고정 배포본·관련 69개 격리 시험,
당일 daily와 동일 backup ID의 restic 저장·전체 검사 대조, 운영 미리보기 후보 1개·보호 3개·
편입 전 제외 3개와 삭제 0건을 남겼습니다. 기존 코드·검증·잠금을 유지하며 새 계획을 매번 만듭니다.
[반복 승인 범위](baseline/checkpoint-automation-2026-10-05/approval-review.md)는 이 후보 보고 시점의 제안이며,
당시 새 타이머는 미설치·승인 파일은 비활성이었습니다. 이후 별도 승인·활성화는 위 기록으로 이어집니다.
기존 수동 승인으로 자동 삭제하지 않습니다.

`baseline/checkpoint-retirement-followup-2026-10-05/`는 첫 checkpoint 정리 이후 새 예약 백업 확인입니다.
[요약](baseline/checkpoint-retirement-followup-2026-10-05/summary.md)에 13:58 새 daily 완료,
동일 사본의 14:05 예약 restic 저장·전체 읽기 검사, 원래 8개 snapshot과 로컬 사본 6개 보존을
남겼습니다. 오늘 snapshot 1개 추가 후 14:35 예약은 처리 0개로 정상 종료했고, 기존 초기 3/3
관찰·복구 증거는 유지했습니다. 호가 새 버전 첫 정기 종료 확인은 15:05 이후 별도 항목입니다.

`baseline/depth-status-deploy-2026-10-05/`는 승인한 호가 상태 공개 버전의 실제 운영 반영입니다.
[요약](baseline/depth-status-deploy-2026-10-05/summary.md)에 14:01 호가 서비스만 종료·기동,
이전 17,522개 저장 완료와 interrupted 보존, 새 1,350개 저장·REST 13회 일치·CLI 정상 결과를
남겼습니다. 첫 정기 종료는 15:05 이후 확인할 미완료 항목이며, 기존 데이터 갱신의
waiting_source와 정기 백업·restic 진행 상태를 배포 결과와 구분했습니다.

`baseline/depth-status-release-2026-10-05/`는 일반 저장 오류 후속 검증과 배포 후보 고정입니다.
[요약](baseline/depth-status-release-2026-10-05/summary.md)에 호가 부족 없는 지속 저장 실패에서
메모리 ID·epoch가 섞이는 문제의 재현·보완, 일반 오류 4건 추가와 호가 167건 통과를 남겼습니다.
기준 소스·미커밋 입력을 함께 고정하고 깨끗한 경로의 적용·167건 재현·역패치 검증을 마쳤습니다.
이 검증 당시에는 운영에 적용하지 않았으며, 별도 승인 후 반영 결과는 위 운영 기록으로 이어집니다.

`baseline/depth-status-candidate-2026-10-05/`는 호가 실패 상태를 이벤트 저장보다 먼저
공개하는 후보의 격리 검증입니다. [요약](baseline/depth-status-candidate-2026-10-05/summary.md)에
0개·8개 경로의 저장 정지 중 외부 CLI 거부, DB·JSON·원문 오류와 강제 종료·재시도,
호가 관련 163건 통과를 남겼습니다. 미저장 건수·ID는 앞당기지 않으며 DB 기록 자체가
실패하면 이전 상태가 남는 한계도 구분했습니다. 후보 패치만 보관했고 운영에는 적용하지 않았습니다.

`baseline/depth-independent-2026-10-05/`는 두 범위 손실의 독립 원문 계산과 소비 경로 시험입니다.
[요약](baseline/depth-independent-2026-10-05/summary.md)에 8,221행·81회 범위 채택의 공통 가격·수량
일치와 마지막 교체를 생략해도 두 사건의 부족한 쪽이 0개가 되는 결과를 남겼습니다.
새 회귀 테스트 6건은 저장 전 상태 조회가 이전 `healthy=true`를 보일 수 있다는 한계도
고정했습니다. 보고서 저장 뒤의 거부와 복구 후 partial 유지는 확인했고 운영 코드는 변경하지 않았습니다.

`baseline/depth-decisions-observation-2026-10-05/`는 보류 기록 적용 뒤 45개 종료 세션의 점검입니다.
[요약](baseline/depth-decisions-observation-2026-10-05/summary.md)에 첫 24시간 관찰 완료,
보류 평가 105회·남은 문맥 67개 검산, 실패 2세션의 원문·재생 일치와 급격한 범위 손실을 기록했습니다.
추가 예산 소진은 0회이며 한 실패는 사전 보류 없이, 다른 실패는 대조 해소·범위 갱신 뒤 발생했습니다. 운영 정책은 유지했고,
검증 대기 1건·연결 공백 및 감사와 겹친 정기 정리의 첫 실패·자동 재시도 완료도 함께 보존했습니다.

`baseline/checkpoint-retirement-apply-2026-10-05/`는 승인한 작업용 사본 5개의 첫 수동 정리 결과입니다.
[요약](baseline/checkpoint-retirement-apply-2026-10-05/summary.md)에 9/27~10/1 정리 완료,
남은 로컬 사본 6개·restic 8개 보존, 여유 공간 증가 관측 5.56GB와 동일 계획 재실행의 추가 삭제 없음을
남겼습니다. 당시 정리 후 예약 11회는 모두 처리 0개 성공이었으며, 10/5 새 daily → 예약 restic
연결은 위 후속 점검에서 완료를 확인했습니다.
수집·기존 예약은 유지했고 자동 정리·forget/prune·시험 사본 정리는 적용하지 않았습니다.

`baseline/checkpoint-retention-readiness-2026-10-05/`는 최종 코드로 실행된 10/4 정기 백업 확인입니다.
[요약](baseline/checkpoint-retention-readiness-2026-10-05/summary.md)에 daily 무결성 948,193건 통과,
같은 새 사본의 예약 restic 저장·전체 읽기 검사와 journal/ID 연결을 남겼습니다. 정리 후보 5개·
예상 회수 5.56GB의 [특정 승인 계획](baseline/checkpoint-retention-readiness-2026-10-05/plan-review.md)을
준비했습니다. 이 승인 전 기록 시점에는 실제 삭제 0건이며, 이후 승인·적용 결과는 위 기록으로 이어집니다.

`baseline/checkpoint-retention-guards-2026-10-04/`는 정리 계획의 코드·시간·공간 기록 보강입니다.
[요약](baseline/checkpoint-retention-guards-2026-10-04/summary.md)에 관련 14개 테스트 통과,
코드 변경·snapshot 연결 변경 거부와 적용 전후 공간 관측을 기록했습니다. 운영 적용은 비활성화이며,
새 정기 daily → restic 연결과 첫 삭제 직전 reader 점검·특정 계획 승인은 후속입니다.

`baseline/checkpoint-retention-2026-10-03/`는 작업용 사본 정리기의 구현·격리 시험과 운영 미리보기입니다.
[요약](baseline/checkpoint-retention-2026-10-03/summary.md)에 공통 입력 잠금, 강제 중단 5곳의 재개,
정리 후 다음 daily/restic 연결과 중복 방지, 최종 434개 테스트 결과를 기록했습니다.
운영 후보는 4개·예상 회수 4.17GB이며 삭제 0건, 적용 비활성화·새 타이머 없음입니다.

9/23 이후 변경과 아래 운영 검증을 묶은
[9/27 마일스톤](../docs/milestones/2026-09-27-collection-and-backup-stabilization.md)에
restic 시험 전의 기준 상태와 후속 순서를 정리했습니다. 각 baseline의 관측 시각·범위는 유지합니다.

그 이후 restic 병행 운영·예약 성공 3/3과 운영 사본 복원, 호가 재시도·조기 갱신·보류 기록,
네트워크 관측은 [10/3 마일스톤](../docs/milestones/2026-10-03-restic-and-depth-observability.md)에
종합했습니다. 10/3 11:23까지 확보한 근거를 사용하며, 새 보류 기록의 첫 종료 세션·24시간
감사와 최종 백업 보관 정책은 후속 항목으로 남겼습니다.

`baseline/restic-transition-2026-10-03/`는 원본 경로·운영 암호·처리 상태·캐시 없이 수행한 독립 복구입니다.
[요약](baseline/restic-transition-2026-10-03/summary.md)에 두 번째 SSD의 복구 키트, 최신 명시적 ID의
추출·복원·무결성 865,619건 통과·조회 107개 일치·새 독립 백업 검증을 남겼습니다.
작업용 3개와 날짜별 restic 보관을 구체화했고 네 작업용 후보의 회수 예상은 약 4.17 GB입니다.
삭제는 실행하지 않았으며 PC 외부 암호 보관·실제 새 VM 검증과 정리 기능 구현은 후속입니다.

`baseline/restic-observation-2026-10-03/`는 아래 호가 감사와 겹쳤던 daily 재시도의 완료 확인입니다.
[요약](baseline/restic-observation-2026-10-03/summary.md)에 13:50 정상 완료·무결성 864,023개
실패 0, 같은 사본의 14:05 restic 예약 저장·14:09 전체 읽기 검사 성공을 남겼습니다.
예약 7회의 journal·실행 결과를 대조했고 새 사본 처리 1회와 처리 0개 탐색 6회를 구분했습니다.
기존 관찰 3/3과 복원 증거는 유지하며, 새 보류 기록의 24시간 감사는 아직 후속입니다.

`baseline/depth-decisions-followup-2026-10-03/`는 보류 기록 적용 뒤 첫 종료 세션의 검증입니다.
[요약](baseline/depth-decisions-followup-2026-10-03/summary.md)에 35,229개 증분·35,584행 재생,
원문 가격·수량 쌍 2,961,262개와 REST 대조 351회 일치, 실제 보류 1회의 사유·해소 검산을
남겼습니다. 보류는 이전 대조 대기·최소 간격이 함께 적용된 경우이며 예산 소진이 아니었습니다.
동시간 daily의 읽기 잠금 경합과 자동 재시도도 보존했습니다. 당시 미완료였던 백업은 위
16시 점검에서 완료를 확인했으며, 새 기록의 24시간 관찰은 후속 항목입니다.

`baseline/depth-decisions-2026-10-03/`는 조회 보류 사유 기록만 분리해 운영에 설치한 결과입니다.
[요약](baseline/depth-decisions-2026-10-03/summary.md)에 422개 테스트, 11개 조건·22회 요청·품질
일치 비교와 기록 비용 표본을 남겼습니다. 요청 예산·간격은 유지하며 긴급 예약 기능은 제외했습니다.
11:17:30 정기 교체로 적용됐고 첫 표본 501개 저장·REST 4회 일치와 요청 문맥 저장을 확인했습니다.
설치 당시 실제 보류 발생 표본·첫 종료 수집·24시간 검사를 후속으로 남겼으며 반복 타이머는
추가하지 않았습니다. 첫 종료 세션과 실제 보류 검증은 위 후속 기록으로 이어집니다.

`baseline/depth-observation-2026-10-03/`는 조기 호가 갱신의 첫 24시간 운영 점검입니다.
[요약](baseline/depth-observation-2026-10-03/summary.md)에 24개 종료 세션의 complete 21·partial 3,
845,353개 수신·저장 일치, REST 8,745회 일치와 검증 대기 1건, 범위 복구 4회·미재구성 31개를
남겼습니다. 전 구간 표준 파일·ID 연결과 새 실패 2개·정상 표본의 원문 대조는 통과했지만,
예산 소진 외에 1초 이내 범위 소진도 확인했습니다. 네트워크 1,440개 표본은 정상이며,
운영 정책은 유지했습니다. 보류 사유 기록의 운영 적용은 위 별도 기록으로 이어집니다.

`baseline/depth-budget-candidate-2026-10-02/`는 운영에 적용하지 않은 호가 보류 기록과 예산
예약 후보의 격리 검증입니다. [요약](baseline/depth-budget-candidate-2026-10-02/summary.md)에
428개 테스트, 11개 조건·44회 비교, 예약 0회의 기존 요청 시각·품질 일치, 2회·4회 예약의
이점과 악화 반례를 보존했습니다. 후보 패치는 별도로 남겼으며 운영 코드·설정은 유지했습니다.
24시간 운영 점검과 기록 전용 구현의 운영 적용은 위 10/3 기록으로 이어집니다.

`baseline/depth-refresh-2026-10-01/`는 호가 조기 갱신·top 부족 처리와 별도 네트워크 관측의
구현·검증 기록입니다. [요약](baseline/depth-refresh-2026-10-01/summary.md)에 411개 검사,
실시간 증분 1,741개·REST 대조 16회 일치, 기존 장애 3개 재생 판정 유지, 10/2 소스 반영과
실제 네트워크 타이머 실행을 남겼습니다. 10/2 08:09:37에 정기 교체로 적용됐고, 약 10시간의
종료 세션 10개 중 9 complete·1 partial을 확인했습니다. 352,278개 증분 저장 및 REST
3,595회 일치는 확인했으나 추가 조회 예산 소진 중 범위 소진 1회와 지연 snapshot 1회가
남았습니다. 24시간 관찰과 예산 배분 검토는 후속입니다.

`baseline/restic-observation-2026-10-01/`는 첫 병행 예약 관찰을 완료한 기록입니다.
[요약](baseline/restic-observation-2026-10-01/summary.md)에 실제 날짜별 성공 **3/3**, 기존 복원
증거 유지, daily 무결성 692,746건 통과, 지연됐던 9/29 자료 4종 4,608행의 조회·체크섬 검증을
남겼습니다. 9/30 자료의 정오 대기와 수집 품질 후속 항목은 구분하며 사본 삭제·백업 대체는 하지 않았습니다.

`baseline/stream-followup-2026-10-01/`는 첫 실제 지연 재시도와 새 호가 실패·통신 장애 점검입니다.
[요약](baseline/stream-followup-2026-10-01/summary.md)에 재시도 2회 복구, 범위 소진 5회와
미재구성 24개, 실패 세션 3개의 106,756행·가격 수량 쌍 31,230,632개 대조를 남겼습니다.
네트워크 장애 시 호가 수신 공백과 aggTrade 자동 REST 보충 4,218행을 구분했고,
고정 50분의 51,964개 체결 ID 연속성과 OI REST 라벨 24개를 확인했습니다.

`baseline/depth-observation-2026-09-30/`는 새 재시도 정책의 첫 24시간 운영 점검입니다.
[요약](baseline/depth-observation-2026-09-30/summary.md)에 완료 24개 세션의 증분 845,439개
수신·저장 일치, 동기화 손실·미재구성 0개, REST 일치 8,407회와 종료 경계의 검증 대기 1건,
표본 2개 원문 가격 수량 쌍 7,871,093개 대조를 남겼습니다. 실제 지연 재시도는 0회이므로
복구 효과의 운영 검증과 연결 경계 복원은 통과로 간주하지 않습니다.

`baseline/restic-observation-2026-09-30/`는 두 번째 새 날짜의 예약 성공 기록입니다.
[요약](baseline/restic-observation-2026-09-30/summary.md)에 daily 무결성 607,815건 통과,
14:05 예약의 새 사본 저장·전체 검사와 실제 날짜 집계 **2/3**, 55회 예약의 성공·54회 no-op 제외,
기존 복원 증거 유지와 별도 일별 원천 갱신의 9/29 자료 4종 대기를 기록했습니다.

`baseline/depth-retry-2026-09-29/`는 지연 스냅샷의 첫 재시도를 1초로 줄인 구현·검증 기록입니다.
[요약](baseline/depth-retry-2026-09-29/summary.md)에 1·2·4·8·10초 대기와 최근 60초 추가 요청
8회 제한, 383개 항목의 최종 통과 근거, 과거 세션 재생, 실시간 1,655개 증분·REST 대조 11회,
서비스 재시작 없는 소스 반영을 남겼습니다. 이후 19:53:47 운영 적용과 첫 완료 세션의
증분 35,229개·원문 가격 수량 쌍 4,641,746개·REST 대조 351회 일치를 확인했습니다.
실제 지연 사례의 개선 효과·24시간 품질과 선제 갱신 5초 제한은 후속 관찰·개선입니다.

`baseline/depth-followup-2026-09-29/`는 미재구성 101·102개와 새 범위 소진 2건의 원문 분석입니다.
[요약](baseline/depth-followup-2026-09-29/summary.md)에 3개 세션 106,741행·가격 수량 쌍
20,626,748개 대조, 초기 스냅샷의 ID 연결 부재와 10초 재시도 대기, 5초 이전 범위 소진,
실제로 동작한 같은 소켓 버퍼 복구 2건의 독립 계산을 남겼습니다. 운영 코드와 과거 partial
판정은 유지했으며, 초기 재시도와 선제 갱신 정책 개선은 후속 구현 항목입니다.

`baseline/restic-observation-2026-09-29/`는 수정 후 첫 새 날짜의 예약 성공 기록입니다.
[요약](baseline/restic-observation-2026-09-29/summary.md)에 daily 무결성 522,952건 통과,
13:35 예약의 새 사본 저장·전체 저장소 검사와 실제 날짜 집계 **1/3**, 반복 예약 50회 판별,
어제 복원 시험의 유지와 기존 파일 해시 대조를 남겼습니다. 새 호가 partial 2개와 미재구성
102개는 백업 무결성과 구분한 후속 분석 항목입니다. 남은 두 날짜는 실제 실행 후 집계합니다.

`baseline/restic-observation-2026-09-28/`는 첫 새 사본의 중간점검과 예약 판별 수정 기록입니다.
[요약](baseline/restic-observation-2026-09-28/summary.md)에 실제 저장·전체 검사, 오래된 systemd
환경변수의 반복 실행 재현과 현재 Job 기반 수정, 테스트 38개, 새 스냅샷의 수동 복원·독립 백업
검증을 남겼습니다. 날짜별 예약 성공은 엄격한 증거 기준으로 0/3이며, 복원 시험은 통과했습니다.
호가 partial 3개와 원천 제공 대기도 백업 무결성과 구분해 기록합니다.

`baseline/restic-timer-2026-09-27/`는 별도 restic 사용자 타이머 적용과 첫 실제 실행 검증입니다.
[요약](baseline/restic-timer-2026-09-27/summary.md)에 설치 전 점검·linger·서비스/타이머 성공·다음 예약·롤백,
새 테스트 22개를 남겼습니다. 최초 탐색은 처리 0개이므로 새 날짜 집계 0/3이며, 새 snapshot 복원도 대기입니다.
이후 집계는 실행 중인 observation 기록에서 확인하며 미래 성공을 미리 기록하지 않습니다.

`baseline/restic-archive-2026-09-27/`는 기존 완료 사본의 restic 병행 보관 구현과 수동 검증입니다.
[요약](baseline/restic-archive-2026-09-27/summary.md)에 관련 테스트 70개, checkpoint 기준 snapshot time,
실제 사본 저장·전체 저장소 검사·재실행 시 snapshot 1개 유지, 원래 사본과 유닛 보존을 기록합니다.
별도 타이머 적용과 날짜별 장기 보관 정책·중복 사본 정리는 후속 단계입니다.

`baseline/restic-trial-2026-09-27/`는 현재 하드링크 사본과 restic의 독립 비교입니다.
[요약](baseline/restic-trial-2026-09-27/summary.md)에 같은 두 복구 지점의 할당량 37.61% 감소,
두 날짜의 추출·별도 경로 복원·조회·새 독립 백업 검증, 원래 manifest와 source-root 검사 보존,
관련 테스트 41개·수집 상태 222개 표본과 호스트 설정 분리를 기록합니다. 캐시·시험 작업 공간은
따로 집계하며 운영 백업 예약을 전환한 것은 아닙니다.

`baseline/scheduled-maintenance-2026-09-27/`는 다음 날 실제 예약에서 잠금 대기를 확인한 기록입니다.
[요약](baseline/scheduled-maintenance-2026-09-27/summary.md)에 623.883초 대기·재시작 0회·
백업 후 정리 완료, 무결성 353,024건 통과·신규 증분 사본, 수집 병행 상태와 재부팅 구간의
호가 재생·24시간 체결/OI 감사를 남깁니다. 무중단 호가 수집 판정은 아닙니다.

`baseline/maintenance-wait-2026-09-26/`는 백업과 파일 정리의 잠금 경합 개선 기록입니다.
[요약](baseline/maintenance-wait-2026-09-26/summary.md)에 전체 테스트 295개, 실제 systemd의
기존 시작 제한 재현·대기 후 완료·시간 초과 시험, 수집 재시작 없는 적용과 운영 확인을 남깁니다.
다음 날짜의 자동 경합 해소는 별도 관찰 항목입니다.

`baseline/daily-backup-2026-09-26/`는 새 날짜의 첫 자동 증분 백업을 확인한 기록입니다.
[요약](baseline/daily-backup-2026-09-26/summary.md)에 전체 무결성 268,824건 통과,
8.710 GB 중 5.666 GB 재사용·3.045 GB 신규 저장, 백업 중 수집 점검과 파일 정리의
재시도 한도 복구를 남깁니다. 백업·정리 일정 충돌의 재발 예방은 후속 개선 항목입니다.

`baseline/depth-review-2026-09-26/`는 호가 복구 보강의 원인·정확성을 다시 검토한 기록입니다.
[요약](baseline/depth-review-2026-09-26/summary.md)에 별도 전체 호가장을 정답으로 한 33개 검사,
기존 호가 검사 64개 재실행, 실제 원문의 독립 계산과 남은 급격한 범위 소진 한계를 남깁니다.
운영 코드 변경이나 서비스 재시작 없이 검증을 보강했습니다.

`baseline/depth-history-2026-09-26/`는 최근 이벤트 버퍼를 이용한 호가 복구 구현·적용 기록입니다.
[요약](baseline/depth-history-2026-09-26/summary.md)에 뒤처진 검증 스냅샷과 같은 소켓의 anchor
정렬, 과거 판정 보존, 테스트 256개, 실제 실패 자료·3분 실시간 시험과 12:38 운영 적용을 남깁니다.
급격한 가격 범위 소진이 모두 예방되거나 과거 공백이 복구됐다는 의미는 아닙니다.

`baseline/observation-2026-09-26/`는 초기 수신 버퍼 배포 후 첫 24시간 점검입니다.
[요약](baseline/observation-2026-09-26/summary.md)에 새 연결 96회의 원문·ID 연결 확인,
남은 범위 소진 2회와 같은 소켓 재동기화 실패 1회, 체결 1,186,304건 감사,
표본 호가 재생과 OI·유지보수 상태를 보존합니다. 다음 날짜 자동 백업은 13:10 실행 전 상태입니다.

`baseline/depth-coverage-2026-09-25/`는 과거 호가 가격 범위 소진 3건의 읽기 전용 분석입니다.
[요약](baseline/depth-coverage-2026-09-25/summary.md)에 13,121행 원문 대조·실패 재현,
소진 직전 호가와 갱신 간격, 뒤처진 검증 스냅샷 두 건의 과거 이벤트 정렬 시나리오를 기록합니다.
운영 개선 배포가 아니며, 기존 실패 기록을 보존하고 24시간 관찰·다음 날짜 자동 백업을 기다립니다.

`baseline/depth-bootstrap-2026-09-25/`는 호가 초기 동기화 실패의 원인 점검과 개선 기록입니다.
[요약](baseline/depth-bootstrap-2026-09-25/summary.md)에 최근 실패의 시각·ID 대조,
초기 수신 버퍼, 실제 연결 교체 시험·원문 대조·재생과 적용 범위를 보존합니다.

`baseline/incremental-backup-2026-09-25/`는 두 번째 SSD로 자동 백업을 전환한 기록입니다.
[요약](baseline/incremental-backup-2026-09-25/summary.md)에 파일 단위 증분 저장, 실제 두 사본의
절약량, 독립 복원·조회 대조, 마운트 누락 시 실패 처리와 225개 테스트를 보존합니다.
백업끼리 공유한 파일의 손상 범위와 별도 장비 사본이 아직 없다는 한계도 기록합니다.

`baseline/observation-2026-09-25/`은 상시 수집 전환 후 최근 24시간을 점검한 기록입니다.
[요약](baseline/observation-2026-09-25/summary.md)에 체결 전체 ID 연속성, 호가의 부분 실패와
표본 재생, OI 관측, 자동 유지보수·백업, 실제 저장량과 전체 사본 백업의 용량 시나리오를
보존합니다. 호가 전 기간 무손실이나 모든 데이터셋의 정상 판정을 의미하지 않습니다.

`baseline/history-extension-2026-09-23/`은 2019년 이후 원천별 제공 시작점까지 확장한 기록입니다.
[요약](baseline/history-extension-2026-09-23/summary.md), 최종 자료별 ledger, 원천 가용 범위,
REST 복구, 과거 형식 예외, 실시간 depth의 스냅샷 지연과 증분 보존, 기존 스냅샷 보존,
210개 테스트와 새 백업을 함께 기록합니다. 이전 baseline은 수정하지 않습니다.

`baseline/v0.1.0/`은 첫 버전의 검토된 검증 근거입니다. 이 디렉터리의 기록은
그대로 보존하고, 다음 기준 버전을 만들 때 새 버전 디렉터리에 기록을 추가합니다.

| 파일 | 내용 |
|---|---|
| `validation.json` | 34개 테스트·린트·무결성 검사와 검증한 소스 파일 해시 |
| `bootstrap-2026-09-14_21.json` | 고정 7일 구간의 데이터별 수집 결과와 완전성 |
| `smoke.json` | 스냅샷·출력 ID, 반복 내보내기, OI 시각 비교 |
| `freqtrade-loader.json` | Freqtrade 2026.8의 실제 파일 로딩 검사 |
| `freqtrade-backtests.json` | 현물·선물 백테스트 결과 파일 해시와 연동 검증 |
| `legacy-oi-audit.json` | 기존 OI 원본 916개에 대한 독립 감사 |
| `legacy-oi-import.json` | 원본 가져오기와 20일 격리 기록 |
| `snapshot-manifest.json` | 연구 입력의 정확한 revision·파일 해시·품질 상태 |
| `export-manifest.json` | Freqtrade 출력 파일·형식·변환 규칙·해시 |

검증 구간은 UTC `[2026-09-14, 2026-09-21)`입니다. 보고서에 기록된 실행 시각과
파일 해시는 실제 검증 당시의 값이며, baseline을 정리한 시각으로 덮어쓰지 않았습니다.
`snapshot-manifest.json`의 SHA-256은 보고서의 `snapshot_id`와 일치합니다.

`baseline/operations-2026-09-22/`는 자동 수집 활성화 시점의 별도 검증 기록입니다.
`validation.json`에 56개 테스트, 파일 무결성 검사, 타이머의 실제 실행 결과, OI 서비스
재시작 전후 검증과 소스 해시를 기록했습니다. `status.json`은 검증 당시 9개 데이터셋의
운영 범위·누락·최신 시각과 기존 OI의 격리 20일을 보존합니다. 첫 버전의 검증 자료는
수정하지 않았으며, 현재 상태는 고정 보고서 대신 `marketdata status`로 확인합니다.

`baseline/research-2023_2026/`은 UTC `[2023-09-22, 2026-09-22)`의 과거 수집과
연구 구간 검증 기록입니다. [요약](baseline/research-2023_2026/summary.md)에서 데이터별 수량과
미해결 누락, 월별 CSV, 구간별 스냅샷 ID를 확인합니다. 초기 수집 보고서와 별도 원천 복구 결과를
구분하며 `quality.json`이 복구 후 최종 상태입니다. 19개 스냅샷의 전체 manifest는 로컬
`data/snapshots/`에 보존하고, Git에는 ID·범위·크기·검증 결과를 담은 인덱스를 보관합니다.
이 단계는 전략 성과나 OI 공개 시각의 검증을 포함하지 않습니다.

`baseline/freqtrade-userdata-2026-09-22/`는 기존 `ft_userdata` Docker에 연결한 검증 기록입니다.
[요약](baseline/freqtrade-userdata-2026-09-22/summary.md)에 4개 출력, 실제 native 파일 29개 검사,
4개 백테스트, 20개 신호의 lookahead 분석, 테스트 78개와 기존 설정 보존 확인을 기록했습니다.
출력별 manifest와 실행별 명령·이미지·설정·결과 해시를 함께 보존합니다.

수집·검증 스크립트가 생성하는 `reports/` 바로 아래의 보고서, 테스트 XML, 로그는
로컬 실행 결과로 Git에서 제외합니다. 최초 페이지 분할 오류를 기록한
`bootstrap-initial-2026-09-14_21.json`도 로컬에 보존하되 baseline에는 최종 검증 결과를 넣었습니다.

`baseline/oi-quality-2026-09-22/`는 OI 관측·일별 품질 보고서의 활성화 기록입니다.
[요약](baseline/oi-quality-2026-09-22/summary.md)에 테스트 95개, 7일의 출처 간 값 비교,
v2 관측 42회, 초기 요청 경계 보정 근거와 실제 타이머 실행 결과를 기록했습니다.
최신 누적 결과는 고정 기준 기록 대신 `reports/oi-quality/latest.json`으로 확인합니다.

`baseline/backup-2026-09-22/`는 실시간 수집 확장 전 로컬 백업·복원 검증 기록입니다.
[요약](baseline/backup-2026-09-22/summary.md)에 테스트 111개, 10,487개 백업 파일,
20개 스냅샷의 107개 구간 조회 대조와 수집 전후 상태를 기록했습니다. 같은 파일시스템의
복구 지점이며, 외부 백업·정기 예약이나 aggTrade·depth 수집 구현 완료를 뜻하지 않습니다.

Git에는 시장 데이터·SQLite·백테스트 결과 ZIP을 포함하지 않습니다. manifest와 해시는
입력을 식별하는 근거이며 원본 파일의 백업을 대신하지 않습니다. 같은 연구를 재현하려면
해당 `data/`와 일관된 `state/` 백업도 함께 보관해야 합니다.

`baseline/aggtrade-2026-09-22/`는 BTCUSDT 선물 aggTrade REST·WebSocket의 소규모 검증 기록입니다.
[요약](baseline/aggtrade-2026-09-22/summary.md)에 과거 9,138건의 캔들 대조, 실시간 6,533건과
연결 교체 중 누락된 502건의 REST 복구, 출처 간 값 비교, 테스트 137개와 백업·복원 검증을
기록했습니다. 표본 수집은 종료했으며 상시 수집 서비스·현물·장기 백필·호가 depth는 후속 범위입니다.

`baseline/depth-2026-09-22/`는 선물 호가 스냅샷·증분 수집과 재동기화·재생 검증 기록입니다.
[요약](baseline/depth-2026-09-22/summary.md)에 3분 표본의 1,720개 업데이트, 연결 교체 2회,
독립 REST 호가 대조 6회, 원문 가격·수량 쌍 334,553개 대조, 테스트 162개와 백업 복원을
기록했습니다. 연결 사이 공백은 복구된 것으로 표시하지 않았으며 상시 서비스도 활성화하지 않았습니다.

`baseline/stream-soak-2026-09-23/`는 aggTrade·depth를 기존 OI 수집과 함께 1시간 실행한 기록입니다.
[요약](baseline/stream-soak-2026-09-23/summary.md)에 체결 90,009건의 연속성, depth 35,349행의
재생·원문 대조, 독립 호가 대조 117회, 자원 관측과 테스트 168개·백업 복원 결과를 보존했습니다.
초기 범위 소진 실패와 수정 후 실행을 구분하며, 지연된 REST 응답 1회의 사후 검증도 실시간
기록과 별도로 남겼습니다. 검증용 수집은 종료했으며 상시 서비스는 후속 범위입니다.

`baseline/packing-2026-09-23/`는 무기한 보관을 유지하면서 이벤트 파일을 묶은 검증 기록입니다.
[요약](baseline/packing-2026-09-23/summary.md)에 개별 파일 3,826개의 묶음 6개 전환,
카탈로그·원문 바이트 보존, 기존 체결·호가 자료 재검증과 format 2 백업 복원 결과를 기록했습니다.
전체 테스트 185개가 통과했습니다. 정리한 것은 검증된 중복 사본이며 논리 데이터는 삭제하지 않았습니다.

`baseline/automation-2026-09-23/`는 두 스트림 상시 서비스와 유지보수 타이머를 활성화한 기록입니다.
[요약](baseline/automation-2026-09-23/summary.md)에 짧은 세션 두 번의 자동 전환, 체결 1,150건의
ID 감사, depth 1,177행의 재생·원문 대조, 첫 자동 유지보수와 전체 검증·백업을 보존했습니다.
운영값은 1시간 세션이며 전체 테스트 199개가 통과했습니다. 기존 아카이브 4종의 전일 파일은
404로 대기 중이므로 파일 무결성 검증 통과와 일반 운영 범위의 완전성을 구분했습니다.
