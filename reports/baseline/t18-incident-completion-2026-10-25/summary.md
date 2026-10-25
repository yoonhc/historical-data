# T18 장애 복구·성공 출력 중복 보관 억제 완료

2026-10-25 사용자 검토에 따라 **이번 장애 복구의 합의 범위를 완료로 닫는다.** 제한된 공간 확보, 현재 수집·pack 복구, 출력 공유 수정본 설치·archiver 재개, 미보관 여섯 사본 편입, 자연 no-op에서 큰 성공 본문 추가 보존 할당 억제가 완료 범위다. 장기 용량 관리나 과거 자료 공백까지 완료했다는 뜻은 아니다.

## 완료 근거

| 범위 | 실제 결과 | 원본 연결 |
|---|---|---|
| 제한된 긴급 공간 확보 | 10/19 승인한 1,952개 경로를 공유해 관측 가용량 약 15.45GB 증가 | [정확한 승인·감사·보존](../t18-capacity-recovery-2026-10-19/status.md) |
| 당시 현재 수집·pack 복구 | 수동 start/reset 없이 자연 재개, 원문/segment/시각 chunk 및 pack 종결 확인 | [수집·pack·공백의 구분](../t18-capacity-recovery-2026-10-19/status.md) |
| 출력 공유 배포·보관 재개 | 10/25 두 실패와 승인된 복구를 보존해 구성/ledger/scope·9개 timer 전환 완료 | [적용·authority·실패 기록](../t18-archive-deploy-2026-10-25/deployment-status.md) |
| 10/20–25 미보관 편입 | 정확한 여섯 backup ID를 3개씩 두 회차에 저장; snapshot·전체 검사·T05 bundle·request/monitor 종결 | [사본별 연결](../t18-archive-deploy-2026-10-25/operating-results.md) |
| 중복 본문 억제 효과 | 같은 snapshot 30개의 자연 no-op 두 회차에서 새 dump/검증 30회씩, 큰 본문 추가 보존 할당 0 B | [할당·작은 기록 측정](../t18-archive-deploy-2026-10-25/archival-audit-20261025T090337Z.json) |
| 배포 중 수집 보존 | 18:05 원문·segment·새 시각 chunk 표본, 같은 supervisor와 연결 | [실제 표본·시각 품질 한계](../t18-archive-deploy-2026-10-25/collector-sample-20261025T090543Z.json) |

기존 apply/recovery/수동 정리나 완료된 검증을 반복하지 않는다. root 설치 receipt의 당시 `operating_observation_pending=true`, 실패 원본, authority·승인 시각은 고쳐 쓰지 않는다. [이전 진행 이력](history.md)과 [향후 stop/rollback 도구](../t18-archive-deploy-2026-10-25/stop-rollback-tools.md)를 보존한다.

## 완료 밖으로 분리한 범위

| 후속 위치 | 남은 내용 |
|---|---|
| TODO **T19 · 검증 비용·기록 보관·용량 관리** | 과거 독립 stdout의 제한된 추가 공간 회수, 일반 진단 로그 기간+총량, 필수/미완료 참조 보호, 파일시스템별 용량 경고, 반복 manifest 조회 비용 분리 |
| TODO **T18 후속 · 과거 수집 공백·interrupted collection** | 용량 중단 구간, aggTrade 보강 한도 뒤 partial, 당시 depth 관측의 복구 불가/불확실 범위; 새 재수집은 별도 검토 |
| TODO **별도 운영 관찰: T05** | 10/25 16:02의 자격 검사 failed/1·삭제 0개 보존, 10/26 15:40의 실제 당시 조건·새 plan/run 결과 확인 |
| TODO **별도 운영 관찰: 수집기 child 경계** | 다음 자연 시작·저장 여부와 기존 시각 품질 표시; 현재 저장 성공과 다음 시작 허용을 구분 |
| TODO 기존 **조건부: 예약 업무 보조 기록의 백업·회전·독립 복구** | request·pins·attempt·JSONL 참조가 있는 기록의 보존과 독립 복구. 현재 호스트 안의 복구를 전체 백업 복구 보장으로 확대하지 않음 |
| TODO 기존 **조건부: 배포 manager 응답/신규 사본 승인 계약** | 실제 응답 차이 회귀와 정상 목록 추가의 승인 계약; 현재 배포의 새 선행 조건으로 올리지 않음 |

[현재 TODO](../../../TODO.md). 완료 범위를 활성 목록에서 제거했고 남은 작업을 위 위치로 옮겼다. 응급 수정마다 새 대형 과제를 만드는 대신 장기 비용·보관·용량 설계를 T19 한 항목으로 묶고, 운영 적용은 작은 단위로 나눈다.

## 최신 경계 관찰

10/25 19:04 가용량은 약 **15.96GB**다. 17:51 시작한 두 child는 18:51에 `complete=true`로 종결됐으며 depth 35,229건·aggTrade 32,207건을 기록했다. 같은 supervisor 아래 다음 child는 `between_sessions`, `prior_hard_fault_blocks_stale_exception`으로 대기한다. 이번 용량 장애가 다시 발생했다고 판단하지 않았고, 다음 child의 성공을 미리 완료 처리하지 않는다. 기존 시각 정책·실패/품질 이력은 보존하며 강제 재시작하지 않는다. [현재 관찰·다음 공간 회수안](../archive-capacity-planning-2026-10-25/review.md).
