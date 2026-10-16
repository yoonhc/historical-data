# T16 정확한 요청 복구·Decimal 수정본 전환 준비

**10/17 00:17:06 KST 승인된 한 요청 복구와 수정본 전환을 완료했다. 원래 근거 보존·업무 재호출 없는 종결, 설치/유닛/ledger, timer 재개 뒤 새 품질 보고서부터 요청 종료까지 실제 확인했다. [운영 결과·정확한 근거·잔여 범위](status.md)를 따른다. T16은 실제 daily 자료 처리와 다음 정기 backup/T05/restic 연결이 남아 있다.**

| 식별자 | 값 |
|---|---|
| 수정 실행본 | `scheduled-clock-policy-20261016-686bb70c9ee4ef11` |
| 실행 소스 SHA-256 | `686bb70c9ee4ef11d9845e8e41cc31a4d846fb2dc8881e9d9640d7f5fbd4ce60` |
| 인계 도구 | `scheduled-clock-handoff-20261016-1437b0f0212d4c6c` |
| 운영 계획 | `8ce6ba51659556e5901e85c81694a9d585c0817dbc9a0c34c990547467217f31` |
| 구성 계획 | `78d7c634bced73af83e765e8f0c9e2a1a4b2029d5d0f0e3375e98e654f4dbd77` |
| 한 요청 복구 계획 | `9f7ebc5e78621b68ae278b1829037bd9a3119e82464eb689a9f59adff499407c` |

[정확한 운영 계획](host-plan.json), [도구 파일·전체 해시](tool-seal.json), [구성 변경 전후](configuration-plan.json), [검토·적용·원복 절차](deployment-review.md)를 함께 따른다. 기존 승인 계획은 이 계획의 적용 승인이 아니다.

## 변경과 복구

실행본의 변경은 앞서 검증한 `policy/run_job.py`·`policy/request_contract.py` 두 encoder뿐이다. Decimal을 정확한 문자열로 보존하며 시각 정책·업무 처리 기준은 그대로다. 소스 291개 중 289개는 동일하다. [기존 수정 근거·123개 검사](../scheduled-clock-result-fix-2026-10-16/summary.md)를 재사용한다.

새 [recover_request.py](recover_request.py)는 업무 본문 호출 경로 없이, request `a702240f54774dacab107dc28fa2a776`의 실제 저장 보고서만 연결한다. 원래 request·보고서·실패 JSONL prefix의 해시, request/attempt/invocation, 보고서 자체 ID와 정확한 시각 관측을 대조한다. 실제 request 잠금 안에서 identity·pins를 보존하고, 원래 계약으로 저장 결과를 검증한 뒤 새 감시·종료 확인을 거쳐 요청을 종결한다.

원래 request와 실패 기록은 별도 복구 경로에 보존한다. 과거 프로세스 exit 1, 보고서의 품질 경고, 복구가 도출한 코드 2를 구분한다. `observed_work_exit_code=null`·업무 호출 0회이며, 보고서를 다시 계산하지 않는다. `request.tmp` 등 원래 실패 산출물도 제거하지 않는다. 현재 상태·근거가 계획과 다르면 덮어쓰지 않는다.

인계는 고정 실행본 설치·UID 1000 import 확인 → 8개 timer 보류 → 원래 scope의 한 요청 복구 → 정상 종료 대기·잠긴 미완료 목록 확인 → 구성/ledger 전환 → timer 재개 순서다. 복구 중 coordinator **공유** 잠금만 유지하여 현재 구성은 고정하고, 시각 조회의 공유 잠금은 허용한다. 정상 업무 종료 대기 중 배타 잠금을 잡지 않는 기존 계약도 유지한다.

기존 13개 구성 경로를 전환한다. 9개 service의 변경은 기존 `/opt` 실행 경로의 정확한 치환이며, 나머지 옵션·업무 인자는 byte 단위로 동일하다. topology·bootstrap guard와 두 binding을 연결하고 boot `closed`·보정 이력·T05 baseline/완료/생성 순서는 보존한다. 8개 timer 내용·16개 user mask·4개 정리 mask는 유지한다.

## 검증과 한계

[최종 검사 원본](final-results.xml), [검증 범위](validation.json), [실행본 복사 검사](relocated-validation.json)를 연결한다.

| 범위 | 검사 수 |
|---|---:|
| 기존 설치·인계·중단·재개·원복을 이번 계획으로 확인 | 76 |
| 정확한 요청 복구·원본 거부·잠금·감시·반복 실행 | 22 |
| 복구 후 구성 전환·중단·원복 및 실행 환경 연결 | 15 |
| 합계 | **113** |

113개는 같은 검사의 중간 실행을 중복 합산하지 않는다. 이전 실행 소스 검사 123개와 별개다. 실제 운영 request·보고서·JSONL의 **시험 사본**, 실제 파일·flock·thread를 사용했다. 별도 프로세스를 여섯 저장 경계에서 SIGKILL한 뒤 복구했고, 업무 재호출·중복 종결·원래 날짜 변경 없이 이어졌다. 시각 provider와 manager는 격리 대역이며 운영 요청 복구 시험이 아니다.

실제 인계 계획의 5,523개 설치 객체를 임시 경로에 복사해 base/daily/preserve의 interpreter·import 해시와 두 encoder를 대조했다. 임시 실행본은 시험 종료 때 제거했고 결과를 남겼다. 최종 `/opt`에서의 import는 승인된 적용 시 timer 보류 전에 다시 확인한다.

기존 실제 관리자 사전 점검의 보호 환경·접근·timer 출처·감시 연결 근거는 [변경 범위와 해시](preflight-reuse.json)를 명시해 재사용한다. 수정 실행본으로 실제 systemd 시험을 새로 했다고 표현하지 않는다. 실제 호스트의 소유권·실효 유닛·ledger는 [읽기 전용 캡처](host-before.json)로 대조했다. 종료 상태 하나를 데이터 품질 정상으로 취급하지 않는다.

23:50 KST [최종 읽기 전용 대조](final-readonly-review.json)에서도 원래 scope, request/report 해시, 적용 전 구성·ledger와 고정 도구가 일치했다. request는 여전히 원래 pending이며 운영 복구·설치는 수행하지 않았다.

10/17 00:14:09 KST [승인 후 적용 직전 대조](preapply-readonly-review-20261017.json)에서 같은 boot, 구성 13개·ledger·보호 파일/소유권/mask, 원래 요청·보고서·journal prefix·scope가 모두 일치했다. 설치 원본 5,511개 파일·12개 링크와 도구 해시도 대조했다. 8개 업무는 유휴, timer는 enabled/active/waiting, Chrony·두 supervisor 신원은 유지됐으며 읽기 전용 현재 시각 준비 검사도 통과했다. 실행기는 실제 적용 중 이 조건들을 다시 확인한다. 도구 세션의 `sudo -n` 실행은 인증 부족으로 시작되지 않았고, 승인된 정확한 명령의 터미널 실행을 요청했다.

## 남은 단계

요청 복구·수정본 적용, 복구 receipt·원래 보고서/실패 보존과 새 품질 요청의 결과 종결은 [실제 확인](status.md)했다. 첫 실제 daily 자료 처리, 다음 정기 backup → T05 근거 → 같은 backup ID의 restic 저장·전체 검사 연결을 이어간다.

원복은 수정 전 T16 실행본으로 돌아간다. 복구 완료 request·새 자료·근거는 유지하지만, **이전 실행본의 Decimal 결함도 돌아오므로 이후 OI 품질 실행의 정상 동작을 보장하지 않는다.** 원복 중 다른 미완료 요청이 있으면 기존 정확한 보존 검토가 필요하다. Chrony/RTC·collector·polling·T05 삭제 정책 변경 및 자동 정리 재개는 이 계획에 없다.
