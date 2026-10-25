# T19 다음 제한된 공간 회수 묶음 — 읽기 전용 검토안

2026-10-25 **19:04–19:07:50 KST** 관찰. T18 장애 복구는 [완료 보고서](../t18-incident-completion-2026-10-25/summary.md)로 닫았다. 현재 배포본·timer·정책을 유지한 채 과거 독립 stdout의 다음 묶음만 골라 실제 바이트를 대조했다. **파일 교체·삭제·하드링크 생성·서비스/timer 제어는 0건이며, 이 문서는 적용 승인 요청이나 실행 가능한 operation이 아니다.**

**19:30 후속:** 이 선택 목록을 유지한 적용 도구·29개 검사·실제 호스트 metadata check를 완료했다. 현재 실행 가능한 operation과 별도 승인 범위는 [최종 적용 검토안](../t19-capacity-apply-2026-10-25/review.md)에 있다. 아래의 도구 미준비 설명은 19:07 당시 상태이며, 원래 proposal/바이트 대조 기록은 변경하지 않았다. 실제 출력 교체는 아직 0건이다.

## 고정된 정확한 다음 대상

목록 proposal ID: `a15f778666f6f377765466bd27076f4474977c5629ca1228b9cdbd7d5cf85e93`  
[verified-selection.json](verified-selection.json) 바이트 SHA-256: `975299ec183583d3345bf5b35a5619285d4a74604ca95f4083a3a69c8f0d12a5`.

| 범위 | 값 |
|---|---|
| 실제 읽어 비교한 파일 | **1,024개**, 두 snapshot의 `/backup.json` 성공 출력 각 512개 |
| 유지할 독립 원본 | **2개**, snapshot별 한 벌 |
| 제안한 교체 경로 | **1,022개**, 원래 stdout 이름을 유지한 하드링크 원자 교체 |
| 출력 조건 | 종료된 성공 run·dump exit 0, cutoff **10/18 09:00 KST 이전**, UID/GID 1000·0600·xattr 없음·현재 nlink=1 |
| 직접 확인 | 각 파일 바이트 동등성·기대 SHA-256·metadata 전후 일치, command/run receipt **1,568개**의 해시와 성공 연결 |
| 제외 | 앞서 처리한 9/27·28 출력, 현재 공유 body pool/새 run, 실패/진행 중 출력, 다른 권한, 기존 하드링크, 운영 자료·checkpoint·restic 객체 |
| 조사 범위 | 이전 검토에서 알았던 종료 run 폴더 995개 중 598개와 작은 JSON 9,318개까지만 탐색. 두 snapshot 512개씩 확보 후 중단 |

| snapshot | backup ID | 기대 manifest SHA-256 | 교체 경로 | 예상 회수 |
|---|---|---|---:|---:|
| `648d7c93eadab36b79295808bfa54b0e6f45b22e4e38e5ed363b540572361f9e` | `11b8326f40d3c00e3526fad36e7377b5ae211e7375baa76745ec644fd01186cf` | `54e41e36c00d3428ef0e6a76b86b81e2f4b47ed6e3f592df8464e1a62ac0beaa` | 511 | 4,366,135,296 B |
| `bbb553b48c9ea943e64dbf1e2f2d021b46d7c2c78313c505029d7c842c9d8df2` | `12156f6dd0a71fba1181c1e201f6c02163220bcae90d290993127c0b5c689af5` | `2717547ce2cf187252a2ee8ab93e70ec7d7d16019f6798af420889f02996dd1a` | 511 | 4,246,839,296 B |

- 유지 원본: `/home/yoonhc/projects/algoTrade/historical_data/reports/restic-archive-runs/002863d37b6d4023ad59819a424b3772/01ce6738c708448f90df7885e45df63e.stdout` (UID/GID 1000, 0600, 8,542,813 B, inode 15883582).
- 유지 원본: `/home/yoonhc/projects/algoTrade/historical_data/reports/restic-archive-runs/002863d37b6d4023ad59819a424b3772/21cac7c8dc7b425d8975a13f08469ff1.stdout` (UID/GID 1000, 0600, 8,308,303 B, inode 15883585).

전체 target 경로, 원래 device/inode/nlink/mtime/ctime·할당량·소유권/mode/xattr와 command/run receipt 참조는 고정 JSON에 있다. 전체 과거 출력이 같은 내용이라고 가정하지 않았다. 선택한 **8,627,771,392 B**만 평균 읽기 예산 64MiB/s, 낮은 CPU/I/O 우선순위로 128.6초 동안 대조했다. 대형 조사 사본이나 별도 본문 파일을 만들지 않았다. [메타데이터 선택](selection-metadata.json), [읽기 전용 검증 코드](verify_selection.py).

## 보존 방법과 참조 경계

기존 10/19 방식의 정확한 바이트 대조, snapshot/권한별 원본 유지, 임시 하드링크 한 개와 원자 교체·디렉터리 동기화, 감사·중단 후 상태 식별을 재사용할 수 있다. 각 원래 경로·내용 해시·소유권·mode·xattr, command/run receipt 및 호출자가 저장한 경로 참조는 유지한다. **inode·link 수·ctime은 바뀌고 mtime은 유지 원본과 공유**한다. 조회 시각은 변경하지 않는 receipt를 따른다.

현재 설치 `request_contract.py`의 SHA-256 `bff0d83ace0b372e3d7aabd23821fb0cd57aed4b0be265364cdc330c7dbefe2c`는 10/19 실제 하드링크/요청 복구 호환 시험의 코드와 같다. [기존 호환 근거](../t18-capacity-recovery-2026-10-19/hardlink-compatibility.json)를 재사용했다. 현재 writer의 UUID·exclusive `xb` 출력 생성 경로와 이번 선택의 오래된 종료 run을 구분했다. 과거 모든 임의 외부 도구의 inode/mtime 사용까지 전수 보증한 것은 아니며, 현재 참조가 없으므로 지워도 된다는 방식으로 판단하지 않았다.

업무/요청/시각 원장·T05 baseline·완료 상한·순서·snapshot을 변경하지 않는다. 현재 request/과거 receipt를 새로 seal하거나 날짜가 오래됐다는 이유로 절단/압축/삭제하지 않는다. 선택된 stdout 경로와 바이트를 그대로 유지하므로 내용 기반 참조를 보존한다. 계획과 실제 metadata/바이트가 달라지면 적용 범위를 자동 확대하지 않는다.

## 공간 계산

| 항목 | 바이트 | 약 GB / GiB |
|---|---:|---:|
| 검증 종료 때 `/home` 가용 | 15,960,825,856 | 15.96GB / 14.86GiB |
| 예상 회수 — 대상 독립 inode 할당 합 | **8,612,974,592** | **8.61GB / 8.02GiB** |
| 작업 감사 예산 전 예상 가용 | 24,573,800,448 | 24.57GB / 22.89GiB |
| 제안 감사 metadata 예산 | 8,388,608 | 8MiB |
| 감사 예산 차감 뒤 예상 가용 | **24,565,411,840** | **24.57GB / 22.88GiB** |

큰 임시 payload 사본은 **0 B**, 교체 시 임시 hardlink 한 개만 필요하다. 기존 보호 여유 10GiB는 그대로다. 디렉터리/감사 metadata·동시 수집·적용 시점 차이 때문에 실제 `df` 증가량은 달라질 수 있고, 열려 있는 원래 inode가 있으면 회수가 지연될 수 있다. 적용 직전 대상과 가용량을 다시 확인하고 실제 결과로만 회수를 완료 처리한다.

## 도구 재사용의 제한과 다음 단계

**10/19 `deduplicate.py`를 그대로 실행할 수는 없다.** 그 고정 operation은 이미 완료됐고 원래 guard는 archiver timer inactive·service idle을 요구한다. 현재는 정상적인 timer 활성 상태를 유지하라는 지시이므로 옛 명령이나 승인으로 새 파일을 처리하지 않는다. 이번 목록에 맞춘 실행 도구/authority는 아직 만들지 않았다.

다음 적용안은 기존 바이트/metadata·원자 교체·감사/재개 로직을 유지하면서 **활성 timer를 그대로 둔 채 archiver가 유휴인 작업창에서 기존 archive 잠금을 비차단 획득하는 경계**를 검증해 고정한다. 잠금 경합 시 파일을 바꾸지 않고 보류한다. 작업 중 자연 예약이 겹치면 기존 archiver의 잠금 보류·다음 자연 재평가와 요청 보존이 유지되는지 확인해야 한다. 이 검증을 하기 전 old guard만 제거해 실행하지 않는다. 현재 유닛/enable/mask·T05·수집기를 정지하거나 교체할 권한으로 확대하지 않는다.

완성된 실행 도구·잠금/중단 검사·적용 직전 재대조와 정확한 operation을 붙인 뒤 별도 파일 변경 승인을 받는다. 다음 정기 사본이나 정상적인 새 로그 추가는 이 오래된 선택 항목의 변조와 구분하되, 이번 선택의 바이트/권한/참조 검사는 유지한다.

## 별도 운영 관찰

[19:04 가용량](current-capacity.json)은 `/home` 약 15.96GB와 backup 파일시스템 약 791.40GB를 구분한다. 백업 디스크 여유를 운영 `/home` 여유로 계산하지 않는다.

18:51 두 child의 보고서는 `complete=true`다. depth는 35,229건, aggTrade는 32,207건을 남겼다. 19:04 시각 표본은 같은 supervisor PID/invocation 아래 `between_sessions`, child 없음, `clock_usable=false`와 `prior_hard_fault_blocks_stale_exception`을 기록한다. **다음 child 시작은 아직 확인하지 못했다.** [depth collection](depth-collection.json)·[clock](depth-clock.json), [aggTrade collection](aggtrade-collection.json)·[clock](aggtrade-clock.json). 용량 오류와 분리하고 시각 정책·이력을 유지하며 수동 재시작하지 않는다.

T05의 10/26 15:40은 아직 미래 예약이다. 10/25 16:02 자격 검사 실패/1·삭제 0개를 보존하고 다음 당시 조건으로 자연 평가하도록 둔다. [T05 후속](../t18-archive-deploy-2026-10-25/timer-restoration-followup.md).

장기 관리 방향은 [T19 통합 설계 초안](management-design.md)에 묶었다. 이 다음 묶음이 전체 과거 출력 정리나 장기 보관/조회 최적화 완료는 아니다.
