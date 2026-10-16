# T16 요청 복구·Decimal 수정본 운영 적용

**10/17 00:17:06 KST 승인한 한 요청 복구와 수정본 전환을 완료했다. 이후 설치·유닛·ledger, 원래 요청의 근거 기반 종결, 새 OI 품질 보고서부터 요청 종료까지의 실제 경로를 대조했다. T16 전체 운영 검증은 실제 daily 자료 처리와 다음 정기 backup/T05/restic 연결이 남아 있다.**

## 적용 범위와 근거

| 항목 | 값 |
|---|---|
| 운영 실행본 | `scheduled-clock-policy-20261016-686bb70c9ee4ef11` |
| 실행 소스 SHA-256 | `686bb70c9ee4ef11d9845e8e41cc31a4d846fb2dc8881e9d9640d7f5fbd4ce60` |
| 인계 도구 | `scheduled-clock-handoff-20261016-1437b0f0212d4c6c` |
| 승인·적용 계획 | `8ce6ba51659556e5901e85c81694a9d585c0817dbc9a0c34c990547467217f31` |
| 구성 계획 | `78d7c634bced73af83e765e8f0c9e2a1a4b2029d5d0f0e3375e98e654f4dbd77` |
| 부팅 | `eb163bf2-6642-4336-b4d6-6cd58771b13d` |
| root attempt | `aac1eccde20c4e2a8540d783796c7c80` |
| 실제 적용 구간 | 10/17 00:15:01.851836–00:17:06.264390 KST |

[원본 적용 결과의 사본](operating-evidence/root-attempt.json), [단계별 인계 상태](operating-evidence/root-apply.json), [실제 manager 명령](operating-evidence/root-commands.json), [구성 receipt](operating-evidence/configuration-receipt.json)를 보존했다. 원본 경로와 해시는 [호스트 대조](operating-evidence/post-apply-native.json)의 `source_copies`에 있다. root 원본은 `/var/lib/marketdata-clock/scheduled-policy-handoffs/8ce6ba51659556e5901e85c81694a9d585c0817dbc9a0c34c990547467217f31/`다.

설치 객체 **5,523개**의 내용·링크·root 소유권·모드, 고정 도구, 구성 **13개**의 승인 after, 실효 유닛 **18개**를 대조했다. 설치 단계에서 최종 `/opt`의 세 variant interpreter/import를 UID 1000으로 확인한 근거도 일치한다. 현재 ledger는 실제 receipt와 같고, 부팅 closed·보정 근거를 유지하며 구성 이력 한 건만 추가했다.

8개 system timer는 enabled/active이고 이전 user service/timer 16개는 masked·유휴다. 업무의 이전 strict waiter 의존성은 없으며 새 전용 waiter·보호 옵션이 맞는다. timer 보류는 00:15:07부터 재개 00:17:06까지 약 119초였다. 실행 중 업무에 stop을 보내지 않고 정상 종료를 기다렸으며 취소·재발행한 no-PID 업무 요청은 0개다. 구성 전환 중 전용 준비 유닛을 갱신한 명령과 업무 종료를 구분한다.

Chrony PID **1245**, depth supervisor **224785**, aggTrade supervisor **224784**와 각 invocation이 적용 전후 및 00:19 호스트 대조에서 일치했다. 수집기·Chrony 재시작이나 시각 변경은 없었다. T05 근거 재등록·checkpoint 삭제는 하지 않았으며 사용자·시스템 정리 mask 네 곳을 유지했다. 공개 관측기 `observation.enabled=false`도 [대조](operating-evidence/pack-and-prior-state-review.json)했다.

## 원래 요청 복구와 새 품질 실행의 구분

[복구 receipt](operating-evidence/recovery-receipt.json)와 [실제 원본·종결 대조](operating-evidence/recovery-review.json)를 확인했다.

| 항목 | 값 |
|---|---|
| 원래 request | `a702240f54774dacab107dc28fa2a776` |
| 원래 request SHA-256 | `c69363e7038590756e2461b26079f9a7789ca5abe98da365cb8423c3bf199e11` |
| 종결 request SHA-256 | `c995ccdc184ab1cece5ef45f0b5d4b25b7f88b8338a55e7dbe36c8572249152a` |
| 원래 보고서 SHA-256 | `52058f8c256a26d34fbfc8d2b05f61d7857c2e352613cbeeb11c82a8cd90de1e` |
| 복구 attempt | `d479bf1947254646994ac9e4187e00d8` |

원래 ID·identity·scope·pins·인자를 유지한 채 실제 저장 보고서로 complete를 확정했다. 보고서 원문, 원래 request, journal prefix, 원래 프로세스 **exit 1**과 직렬화 실패가 보존돼 있다. 새 관측·감시 종료를 확인했으며 **복구 중 업무 호출 0회**, `observed_exit_code=null`이다. 코드 2는 저장 보고서에서 도출한 값이며 과거 CLI의 실제 반환값으로 고쳐 쓰지 않았다. `needs_review=true`와 품질 경고를 유지했다.

그 뒤 timer 재개에 포함된 기존 `OnActiveSec=5s`에 따라 **00:17:12–00:17:16 새 품질 요청**이 실행됐다. 이것은 원래 pending을 재실행한 복구가 아니다. 새 request `fe3f28cddb0d4c1997d2a469a9a9a45e`, 새 invocation `b6a9c04c89d743348bb75e646f585ef3`, 새 identity와 관측을 가진 별도 실행이다.

새 보고서 `1ae7d68e78011609470a8d51141430a70e2207b7c55a224332d4822f5d7d554f`의 SHA-256은 `33f1715bf2b00f1ee379293639a9ef8ccf2711f55233fbf6f85c4692e68eab9c`다. 실제 비교 행이 있는 보고서와 seal·request·attempt·JSONL 종료가 일치했고 감시/기록 오류는 없다. [현재 요청과 저장 근거](operating-evidence/initial-jobs/oi-quality/request.json), [systemd 종료·UID 대조](operating-evidence/initial-runtime-journal-review.json), [원본 journal](operating-evidence/new-quality-unit.jsonl)을 보존했다. UID 1000, 실제 exit **2**, unit `Result=success`이며 품질 검토 필요를 정상 자료 판정으로 바꾸지 않았다.

## 초기 예약 실행

00:23의 [요청·저장 결과 대조](operating-evidence/initial-jobs-review.json), 00:26의 [실제 서비스 상태](operating-evidence/initial-runtime-journal-review.json), [pack 종료 보충](operating-evidence/pack-and-prior-state-review.json)을 연결한다. 운영 파일별 읽기 시점의 대조이며 전체 업무를 한 transaction으로 고정한 관찰은 아니다.

| 업무 | 확인한 결과 | 아직 주장하지 않는 범위 |
|---|---|---|
| OI 갱신 | 새 scope 요청 종결, 실제 sync 행/해시 일치, 자료 partial·exit 2 유지 | 모든 자료 확보·품질 정상 |
| OI 관측 | 정확한 fetch/probe 기록과 요청 종결 일치 | 미래 모든 응답의 성공 |
| OI 품질 | 위 새 보고서·request·attempt·실제 exit 2 연결 | 기존 품질 경고 해소 |
| T13 daily | runner_ok=true, 처리 0건, no_due_tasks, 원장과 요청 일치 | 실제 신규 자료 처리 |
| pack | 00:22:07–00:23:48, phase=finished·exit 0, 보고서와 요청 일치 | 새 pack/개별 파일 정리의 실제 효과: 이번 대상 0개 |
| restic | system timer 출처·UID·invocation·새 wrapper 해시와 요청 일치 | 신규 snapshot/전체 검사: processed=0 |
| network | 새 JSONL 고정 구간·해시와 요청 종결 일치 | 관측되지 않은 시간대의 네트워크 정상 |
| backup | 구성·설치 연결 확인, 새 wrapper 요청은 아직 없음 | 새 checkpoint·T05 근거·restic 보존 |

초기 6개 업무의 읽은 종료 기록에 새 finalization 오류는 없었고, 이후 pack도 같은 계약으로 종결했다. OI 갱신은 자료 partial로 exit 2를 반환해 기존 `Restart=on-failure`에 따른 재평가가 나타난다. wrapper 요청의 종결과 systemd의 재시작 상태·자료 완료를 구분하며 이번 수정에서 해당 정책을 바꾸지 않았다.

daily의 **346 complete·1 needs_review·21 planned**는 [수정 전 00:10 실행](operating-evidence/daily-last-before-repair.json)에도 같았다. 새 확인 주기의 예정 작업과 기존 완료 취소를 혼동하지 않는다. 공개 관측기를 켜거나 기존 점검 상태를 해제하지 않았다.

자연스럽게 reference age가 600초를 넘은 상태에서 OI·probe·quality·daily·restic·network의 시각 판단이 이어졌다. 새 품질 실행 표본은 **666.55–666.82초**, U 최대 약 **0.13835초**였고, 이 초기 표본 전체의 age 최대는 **760.86초**다. 실제 새로운 관측 아래의 판단이며 운영 시각/시간원 장애를 주입하지 않았다. 모든 업무·모든 장애 경로의 검증으로 확대하지 않는다.

## 남은 확인과 원복

이번 결함의 한 요청 복구·수정본 적용·새 OI 품질 결과 종결은 완료했다. T16은 다음을 확인한 뒤 완료 판단한다.

1. 정오/저녁 예약에서 실제 daily 자료 처리와 기존 원장·저장 결과 연결. 처리 0건 재평가는 대신하지 않는다.
2. 다음 정기 backup의 새 checkpoint·T05 완료/순서 근거, 같은 backup ID의 restic 저장·전체 읽기 검사. 다음 backup 예약은 **10/17 13:10 KST**이며 완료 보장 시각이 아니다.

[이번 정확한 계획의 중단·원복 절차](deployment-review.md)를 사용한다. 원복 대상은 이전 T16 `544c61aca49f1355`로 Decimal 결함도 남아 있다. 복구 완료 기록·새 자료·원장·T05 근거를 지우지 않으며, 새 scope의 미완료 요청이 있으면 정확한 보존 검토가 필요하다.

현재 시각 미확인 진단, 수집/부팅의 남은 600초 의존성, polling 복원, 보조 request/attempt/JSONL 전체 백업·회전·독립 복구는 기존 후속이다. 이 호스트에서의 복구를 백업에서의 독립 복구 보장으로 확대하지 않는다. 추가 전체 백업·운영 장애 주입·자동 정리 재개는 하지 않았다.
