# T16 실제 적용·초기 관찰 상태

**이 문서는 10/16 22:23:51 KST 첫 T16 적용과 당시 초기 관찰·Decimal 결함의 기록이다.** 설치·실효 유닛·ledger·초기 업무와 수집 child 전환을 대조했지만 OI 품질 결과 기록이 실패했다. 이 실패와 원래 근거를 보존한다.

이후 별도 승인된 계획 `8ce6ba51…`으로 **10/17 00:17:06 KST 한 요청 복구·수정본 `686bb70c9ee4ef11` 적용과 새 품질 요청 종결을 확인**했다. [현재 운영 상태·근거](../scheduled-clock-repair-deploy-2026-10-16/status.md)를 따른다. T16 전체 검증은 실제 daily 자료 처리·정기 backup/T05/restic 연결이 남아 있다.

| 항목 | 고정 값 |
|---|---|
| 당시 운영 후보 | `scheduled-clock-policy-20261016-544c61aca49f1355` |
| 인계 도구 | `scheduled-clock-handoff-20261016-778dca2b72c6e867` |
| 승인·적용 계획 | `7eccd61f65c66fd5f3d70f8893e23aa01abe246caa719256e96e004d59753a7a` |
| 부팅 | `eb163bf2-6642-4336-b4d6-6cd58771b13d` |
| 원본 root attempt | `c73b0606267244a79a202e244e7b0daa` |
| 적용 구간 | 22:23:43.709821–22:23:51.922505 KST |

## 실제 적용 대조

[적용 후 호스트 대조](post-apply-native.json)의 `source_evidence`에 root attempt·apply/approval·inner receipt·ledger의 원래 경로와 사본/해시를 연결했다. 원본 root 기록은 `/var/lib/marketdata-clock/scheduled-policy-handoffs/7eccd61f65c66fd5f3d70f8893e23aa01abe246caa719256e96e004d59753a7a/`에 있다. `apply.json`은 complete, 취소 요청 0개, 8개 timer 시작 요청 성공이다. 이 완료는 구성 인계 완료다.

최종 설치 객체 **5,523개**의 내용·링크·root 소유권·모드를 대조했다. 구성 13개는 승인 after와 같고 기존 boot closed/보정 이력은 유지했으며 구성 이력만 추가했다. T05 근거를 재등록하지 않았다.

8개 서비스는 고정 variant/UID 1000/`marketdata-jobs-clock-ready.service`를 사용하며 이전 strict waiter/600초 ExecStartPre가 업무 앞에 남아 있지 않다. 8개 system timer는 active, 이전 user 유닛 16개는 masked·비활성이다. Chrony PID 1245, depth supervisor PID 224785, aggTrade supervisor PID 224784와 invocation은 같다. Chrony/RTC/polling·수집 초기/회복 정책·4개 정리 mask는 유지했다.

## 첫 실제 실행

[요청·저장 참조·결과](operating-evidence/initial-jobs/result.json), [실제 종료 상태](operating-evidence/initial-system-show.txt), [원본 journal](operating-evidence/initial-services-journal.jsonl)을 연결한다. mutable request가 진행 중이면 이미 종결한 별도 attempt와 구분한다.

| 업무 | 확인한 결과 | 남은 구분 |
|---|---|---|
| OI 갱신 | sync_run·실제 revision 연결, 요청 종결 | 기존 10/15 공백과 partial/exit 2 유지 |
| OI 공개 관측 | fetch 행·probe ID를 남긴 반복 완료 | 새 pending은 진행 중인 다음 invocation일 수 있음 |
| OI 품질 비교 | 품질 보고서 저장 후 wrapper 기록 실패 | 아래 결함의 복구·수정 전환 필요 |
| T13 daily | 처리 0건·정확한 daily_run, runner_ok=true | 새 실제 자료 처리는 아직 미확인 |
| pack | 22:28:52–22:30:29 단계/요청 완료 | 새 pack·제거 대상 0개. 물리적 재배치 효과의 증거 아님 |
| backup | 새 wrapper로 아직 실행하지 않음 | 다음 정기 예약 10/17 13:10 KST. 완료 보장 시각 아님 |
| restic | 실제 system timer 출처·새 wrapper receipt | processed=0. 새 checkpoint 저장·전체 검사 증거 아님 |
| 네트워크 관측 | 실제 JSONL 고정 구간/해시로 종결 대조 | 모든 네트워크 상태의 항시 정상이라는 뜻 아님 |

자연스럽게 reference age가 600초를 넘은 관측에서도 OI·OI probe·daily·network가 실행됐고 restic은 실행 중 해당 조건의 qualification을 통과했다. 표본 최대 age **772.635478초**, 최대 U 약 **0.140115초**다. daily 22:25 근거는 age **665.889685초**, U **0.134161초**다. reference 원값은 보존하며 모든 업무의 모든 경계를 이 표본으로 검증했다고 확대하지 않는다.

346개 complete·1개 needs_review·1개 planned는 [적용 전 22:20 daily](operating-evidence/initial-jobs/daily-last-before-apply.json)에도 있었다. T16이 기존 OI 점검 상태를 만들거나 해제했다고 표현하지 않는다. 냉각·예산·품질 이력도 유지했다.

## OI 품질 기록 실패

보고서 `4257e5803d8d3fe4f8e57aad801b840cb5503440359d18399ee4390cc14248cc`는 저장됐다. 이후 실제 비교 행의 Decimal에 `isoformat()`을 호출하는 encoder 결함으로 결과 seal과 최종 request/attempt 저장이 실패했다. `request_contract.py`와 `run_job.py` 모두 같은 가정이었다.

- 22:24:01 systemd exit 1, Result=exit-code.
- decisions JSONL에는 finalization_failed와 원래 오류/감시 종료 결과가 남았다.
- 디스크 request `a702240f54774dacab107dc28fa2a776`는 원래 pending·고정 날짜 2026-10-16을 유지한다. 최종 실패 상태까지 request에 저장됐다고 말하면 안 된다.
- request SHA-256: `c69363e7038590756e2461b26079f9a7789ca5abe98da365cb8423c3bf199e11`.
- 보고서 SHA-256: `52058f8c256a26d34fbfc8d2b05f61d7857c2e352613cbeeb11c82a8cd90de1e`.

[별도 보완 소스·검증](../scheduled-clock-result-fix-2026-10-16/summary.md)에서 실제 OI 자료가 있는 CLI로 재현하고 두 encoder만 수정했다. **123개(기존 116+새 7) 통과**, 기존 후보·설치본 무변경이다. 보고서 자체 ID와 정확한 관측이 같은 request/attempt/invocation의 JSONL에 일치하고 현재 scope도 동일함을 [읽기 전용 대조](../scheduled-clock-result-fix-2026-10-16/recovery-review.json)했다. 이것은 운영 요청 복구가 아니다. 날짜만 보고 완료를 추정하거나 request를 지워 재실행하지 않는다.

## 수집기 유지와 다음 child

22:34:53/54 기존 child가 complete로 종료했고 **22:35:09 aggTrade, 22:35:10 depth**의 새 child가 같은 supervisor run 아래 시작됐다. 종료→시작 표본 간격은 각각 **16.217초·15.674초**이며 새 collection·segment도 저장됐다.

[원본 collection·시각 조회](operating-evidence/collector-boundary/result.json): depth `6d57f9fa…` → `9cf97066…`, aggTrade `75d80534…` → `0d41cc05…`. 이전 collection은 각각 14개 chunk, 3,563/3,567개 interval, evidence_state=available, unknown 사유 0개다. available은 근거 존재이며 과거 시각 경고가 없었다는 뜻이 아니다. depth의 기존 hard-fault 이력과 strict 회복 조건도 유지했다.

## 다음 작업

**정확한 요청 복구와 수정본 전환 계획 `8ce6ba51…`은 10/17 별도 승인·실제 적용을 마쳤다.** [현재 수정본 상태·근거](../scheduled-clock-repair-deploy-2026-10-16/status.md)를 확인한다. 이전 설치본을 직접 고치거나 원래 요청 identity를 새 값으로 바꾸지 않았다.

이어 첫 실제 daily 자료 처리, 다음 정기 backup → T05 완료/순서 근거 → 같은 checkpoint의 restic 저장·전체 검사 연결을 확인한다. 확인 목적으로 추가 전체 백업·운영 장애 주입은 하지 않는다. 현재 시각 미확인 진단·수집/부팅의 남은 600초 의존성·polling 복원·보조 request/JSONL 전체 백업·회전은 기존 별도 후속이다. T16 전체 완료나 T05 삭제/반복 승인을 주장하지 않는다.
