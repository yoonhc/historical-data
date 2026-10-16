# T16 Decimal 보완: 한 요청 복구와 수정본 적용 검토

**상태: 10/17 사용자의 “오케이 운영 적용하자”로 별도 승인받아 00:17:06 KST 요청 복구·수정본 적용 완료. [실제 결과와 잔여 검증](status.md)을 확인한다.** [summary](summary.md)의 범위와 [host-plan.json](host-plan.json)의 정확한 변경에 한정했다. 아래 적용 명령은 이미 실행했으며 확인을 위해 반복 실행하지 않는다. 기존 T16 승인을 자동 승계한 것이 아니다.

## 승인 범위

- 운영 계획 `8ce6ba51659556e5901e85c81694a9d585c0817dbc9a0c34c990547467217f31`.
- 도구 `scheduled-clock-handoff-20261016-1437b0f0212d4c6c`; [정확한 파일·해시](tool-seal.json), [이전 도구와의 차이](tool-changes.json).
- 수정 실행본 `scheduled-clock-policy-20261016-686bb70c9ee4ef11`; 전체 소스 해시 `686bb70c9ee4ef11d9845e8e41cc31a4d846fb2dc8881e9d9640d7f5fbd4ce60`.
- 같은 boot `eb163bf2-6642-4336-b4d6-6cd58771b13d`. 부팅·구성·ledger·근거가 달라지면 이 계획으로 덮어쓰지 않는다.
- 기존 OI 품질 요청 **한 건**의 저장 보고서 연결·종결, 수정본 설치, 8개 timer 일시 보류/재개, 13개 구성 경로와 기존 ledger 전환 기록.
- 두 collector·Chrony 재시작, 시각 변경, 자료/카탈로그 원복, checkpoint 삭제, 정리 mask 해제는 범위에 없다.

## 요청 복구의 정확한 대상

| 항목 | 식별값 |
|---|---|
| request | `a702240f54774dacab107dc28fa2a776` |
| 원래 request SHA-256 | `c69363e7038590756e2461b26079f9a7789ca5abe98da365cb8423c3bf199e11` |
| 원래 attempt | `96c32c8fb0eb4a6ca96bee82b21de38b` |
| 원래 system invocation | `8623ef805cac477faacc3c72a50e5e21` |
| 보고서 SHA-256 | `52058f8c256a26d34fbfc8d2b05f61d7857c2e352613cbeeb11c82a8cd90de1e` |
| 고정 비교 종료 날짜 | `2026-10-16` |

[recovery-plan.json](recovery-plan.json)에 경로·scope·보고서 ID·journal prefix 해시까지 고정했다. 날짜만 같은 다른 보고서나 최신 보고서를 대신 사용하지 않는다. 초기 pending에는 결과 포인터가 없으므로 원래 scope에서 명시적으로 연결·종결한다. identity를 새 값으로 덮어쓰거나 pending을 지우지 않는다.

실제 복구 직전 quality service가 실행/대기 중이면 보류하고, 원래 `request.lock`의 배타 잠금을 확보한다. root 인계 도구는 coordinator 공유 잠금을 유지하며 UID 1000 복구 프로세스를 실행한다. 기존 Chrony 읽기 전용 조회·감시와 원래 결과 계약을 사용한다. 업무 CLI·품질 계산은 호출하지 않는다.

복구 결과는 원래 프로세스 exit 1과 실패 기록을 보존한다. 보고서의 `needs_review`도 유지하며, 코드 2는 복구가 보고서에서 도출한 값으로 기록한다. 과거 CLI가 코드 2를 반환했다고 기록하지 않는다. 복구 시도에는 별도의 ID와 `execution=approved-manual-result-reconciliation`, `invocation_id=null`을 사용한다. systemd의 새 업무 invocation이었다고 가장하지 않는다.

## 적용 순서와 명령

운영 승인 후 아래 명령을 **한 번** 실행한다. sudo 인증은 사용자 터미널에서 진행하며 암호를 전달하지 않는다.

```sh
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/scheduled-clock-repair-deploy-2026-10-16/handoff.py --action apply --approve-plan 8ce6ba51659556e5901e85c81694a9d585c0817dbc9a0c34c990547467217f31
```

1. 도구를 root 소유 고정 경로에 설치·재검증하고 실행본 5,523개 객체를 설치한다. 실제 `/opt` interpreter·세 variant import를 UID 1000으로 확인한다. 이 단계에서 실패하면 timer를 보류하지 않는다.
2. 8개 system timer를 보류한다. 실행 중인 업무는 강제 종료하지 않는다.
3. 원래 scope의 quality 요청 한 건을 복구한다. 원래 구성·closed ledger 아래에서 수행하고, 원본/실패/보고서 복사본·관측·새 attempt·terminal request·복구 receipt를 순차 보존한다. 실패하면 구성 전환에 들어가지 않는다.
4. 기존 업무 정상 종료를 기다린 뒤, coordinator와 request 잠금으로 정합성을 재확인한다. 미해결 요청이 남으면 전환을 거부한다. 정상 종료 대기의 600초는 강제 종료 허용 시간이 아니다.
5. 검증된 pending barrier·파일/ledger transaction을 적용하고, 실효 유닛·기존 보호 옵션을 확인한다. 취소한 no-PID 요청만 정확히 인계한 뒤 timer를 재개한다.

최종 receipt의 `configuration=apply_committed`는 구성과 기동 요청 완료다. 모든 자료 처리·백업 검증 완료를 의미하지 않는다.

## 중단 후 재개

오류가 나면 root attempt·commands·apply 상태와 아래 복구 경로를 먼저 읽는다. 잘못된 파일이나 미완료 요청을 삭제해 통과시키지 않는다.

```text
/var/lib/marketdata-clock/scheduled-policy-handoffs/8ce6ba51659556e5901e85c81694a9d585c0817dbc9a0c34c990547467217f31/
state/scheduled-clock-policy/oi-quality/reconciliations/9f7ebc5e78621b68ae278b1829037bd9a3119e82464eb689a9f59adff499407c/
```

동일 계획·근거와 허용된 중간 상태가 유지된 경우에만 같은 apply 명령으로 이어간다. 복구가 request 확정 후 receipt 저장 전에 끊겼으면 정확한 terminal request·attempt를 확인해 receipt만 마무리한다. 현재 request가 다른 요청으로 바뀌었다면 자동으로 덮어쓰지 않는다. 감시·기록 실패는 성공으로 처리하지 않는다. 원래 실패의 임시 파일도 보존한다.

## 원복

동일한 승인 계획의 원복 명령:

```sh
sudo /usr/bin/python3 -B -I /opt/marketdata/tools/scheduled-clock-handoff-20261016-1437b0f0212d4c6c/handoff.py --action rollback --approve-plan 8ce6ba51659556e5901e85c81694a9d585c0817dbc9a0c34c990547467217f31
```

구성 전환 전의 중단이면 기존 설정·ledger를 유지한 채 보류한 timer를 복귀시킨다. 전환 후에는 수정 전 T16 실행 경로·guard/binding을 원복하되 완료한 요청 복구와 새 기록은 취소하지 않는다. 원래 quality 보고서·request ID·pins·새 receipt·자료·원장·T05 근거는 보존한다.

**원복 대상 `544c61aca49f1355`에는 Decimal 결함이 남아 있다.** 원복은 실행 환경 복귀이며 이후 OI 품질 작업을 정상화하는 해결책이 아니다. 복구 전 중단을 원복한 경우 원래 pending도 계속 남는다. 이런 상태에서 quality 요청을 무작정 다시 시작하지 않는다.

새 구성에서 미완료 요청이 생겼으면 기존 `preserve-for-reviewed-rollback` 절차로 정확한 요청 해시·pins·근거와 보존 사유를 검토한 파일을 만들고, 그 SHA-256을 `--request-review`·`--approve-review-hash`로 명시해야 한다. 단순히 현재 파일을 읽어 자동 승인하지 않는다. 취소/발행/실행 이력이 모호한 요청은 추가 시작하지 않고 검토 상태로 남긴다. 원복 후 다시 적용할 때는 변경된 configuration history에 맞는 새 계획을 만든다.

## 적용 후 확인

복구 receipt와 원래 실패/보고서의 해시, 동일 identity·pins, 업무 호출 0회, 새 실행본·실효 유닛, 보호된 세 프로세스의 신원을 확인한다. 이후 정상 예약에서 새 요청이 새 identity로 실행되는지 확인한다. 아직 남은 실제 daily 자료 처리와 정기 backup/T05/restic 검증을 이어가며, 이를 위해 추가 전체 백업이나 운영 장애 주입을 하지 않는다.

보조 request·복구 기록 전체의 자동 백업·독립 복구는 기존 후속 범위다. 이번 호스트 안에서의 중단 복구를 그 보장으로 확대하지 않는다. 자동 정리 mask와 공개 관측기 비활성은 유지한다.
