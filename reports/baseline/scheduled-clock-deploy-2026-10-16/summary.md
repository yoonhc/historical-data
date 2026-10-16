# T16 관리자 사전 점검 완료와 운영 인계 승인안

실제 관리자 점검·인계 검증과 별도 승인을 거쳐 **10/16 22:23:51 KST 아래 고정 계획을 운영 적용했다.** 설치/구성·초기 업무와 다음 수집 child를 확인했으나, OI 품질 결과의 Decimal 기록 결함이 발견돼 **T16 운영 검증은 미완료**다. [실제 적용·결함·남은 범위](status.md)와 [운영 미적용 보완 소스](../scheduled-clock-result-fix-2026-10-16/summary.md)를 따른다. 아래 사전 검증은 당시 근거로 보존한다.

| 식별자 | 고정 값 |
|---|---|
| 업무 후보 | `scheduled-clock-policy-20261016-544c61aca49f1355` |
| 업무 source SHA-256 | `544c61aca49f13559861902bcd49bc28a503b18b7d2718c48c2139c152f56c65` |
| 운영 인계 도구 | `scheduled-clock-handoff-20261016-778dca2b72c6e867` |
| 최종 운영 계획 | `7eccd61f65c66fd5f3d70f8893e23aa01abe246caa719256e96e004d59753a7a` |
| 내부 구성 계획 | `5e7473d9d5c349c5ae31a0da1f1589391ef3507000e358d3ba74ccbc3be5f0cb` |
| 한정 boot ID | `eb163bf2-6642-4336-b4d6-6cd58771b13d` |

[최종 적용·원복 검토안](deployment-review.md), [정확한 계획](host-plan.json), [도구 파일별 해시](tool-seal.json), [검증 범위](validation.json), [비활성 승인 양식](approval.template.json).

## 확인한 결과

사용자가 실행한 [원본 관리자 결과](../scheduled-clock-handoff-2026-10-16/system-preflight-result-48167a9e9eb24fdd.json)의 SHA-256은 `8206c0462c2d4049f4007e7d06b20b82ee471c609a2c6b8d11c0caa7c9580b28`이다. 10/16 **21:32:28–21:32:49 KST** 실행에서 아래를 통과했다.

- 최종 보호 옵션과 UID 1000 아래 8개 업무의 고정 variant·실제 진입점·시험 저장 결과 확인.
- 현재 호스트의 읽기 전용 Chrony 조회와 실제 준비 waiter 연결. 업무별 HTTP/provider 오류 입력은 격리 대역이며 운영 장애를 만들지 않았다.
- 감시 종료 실패로 남은 같은 request를 서로 다른 실제 system invocation에서 복구. 업무 재호출 없이 원래 결과를 종결했다.
- 새 restic wrapper의 실제 system timer 출처 인정, 수동 실행과 잘못된 unit 출처의 비인정.
- 운영 구성·ledger·4개 정리 mask·Chrony/두 supervisor 신원 전후 동일. 시험 유닛 12개와 임시 경로 정리 완료, 정리 오류 0개.

새 [인계 실행기](handoff.py)와 [설치/운영 경계](deployment.py)는 **76개 격리 검사**를 통과했다. [JUnit 원본](host-tests.xml). 실제 `flock`·임시 파일·원자적 저장을 쓰고, manager 상태/명령은 대역이다. 실제 운영 서비스를 전환한 시험으로 표시하지 않는다.

검사에는 정상 본문 종료 중 coordinator 공유 잠금 획득과 기존 guard 보존, no-PID 대기 요청 인계, timer/mask/pending/파일/receipt 경계 중단 후 재개·원복, 모호한 시작의 중복 발행 거부, 미완료 request의 정확한 원복 검토, 설치 중단·권한/해시·승인 범위 거부가 포함된다. mask reload로 대기 job이 사라져도 pending 확정 전 기록한 원래 요청을 유지한다. pending 이전 중단에서는 기존 업무가 정상 완료할 수 있으므로 재개 때 다시 관찰한다.

기존 48개 구성 전환·2개 실제 이력을 남긴 원복/재적용·11개 시험 도구 검사와 정책/복구 119개는 **무변경 부분의 기존 근거를 재사용**했다. 이번 76개에 합산하거나 다시 실행했다고 표시하지 않는다. T15 engine·안전 파일/manager helper·T16 adapter는 바이트 그대로다. 후보 runtime 291개도 변경하지 않았다.

[22:00:36 KST 실제 호스트 재대조](native-review.json)에서는 기존 13개 경로·실효 명령/보호 옵션·소유권·동일 boot/ledger·보호 파일이 계획과 일치했고, T16 request는 0개였다. 이 조회는 실제 작업 종료를 기다리거나 업무를 중지한 것이 아니다. 적용 시 동적 상태를 다시 읽는다. 같은 날 새 읽기 전용 readiness adapter도 기존 binding으로 정상 관측을 얻었다. [기록](readiness-review.json).

## 인계 순서에서 확정한 접점

현재 8개 system service 파일 자체가 bootstrap guard에 포함된다. 따라서 **본문이 끝나기 전에 service 파일을 mask로 바꾸지 않는다.** 그 변경은 실행 중 backup의 T05 완료 후처리 검사에 영향을 줄 수 있다.

인계는 별도 handoff lock → 최종 경로 설치/import → timer만 보류 → 기존 파일/closed ledger를 유지한 정상 종료 대기 → 짧은 coordinator 잠금 아래 상태/request 재확인·pending 확정 → 임시 service mask → no-PID 요청 취소 → 13개 구성 stage/reload/commit → 취소 요청 한 번 인계 → 같은 timer 재개 순서다. 종료 대기 중 coordinator 배타 잠금을 잡지 않는다. 600초는 보류 상한이며 강제 종료 시간이 아니다.

기존 strict waiter·collector·Chrony·T05 정리 유닛은 manager 변경 명령의 허용 목록에 없다. 새 8개 업무 waiter만 이번 정책으로 연결한다. 이전 bootstrap 준비/보정 증거, T05 baseline·완료 상한·선후 관계, 모든 데이터·원장·request는 보존한다.

## 다음 단계와 남은 한계

위 **고정 계획은 10/16 별도 승인 후 설치·적용했다.** 현재 우선순위는 OI 품질 미완료 요청의 저장 결과 연결·복구와 수정본의 새 전환 계획이다. 원래 승인은 새 소스에 자동 승계하지 않으며 [실제 적용 상태](status.md)를 따른다.

운영 후 확인은 8개 업무의 기존 완료/대기 계약, 실제 요청/판정 기록, 새 system restic 출처와 다음 정기 backup/T05 연결, 같은 supervisor의 자연스러운 다음 child에 한정한다. 과거 전체 복원 시험이나 장애 주입을 반복하지 않는다. 새 구성의 실제 재부팅은 미실시다.

현재 시각 근거가 전혀 없는 진단, collector 최초 시작/회복 및 부팅 준비의 600초 의존성, polling 복원, 보조 request/attempt/JSONL 전체 백업·회전·독립 복구는 기존 후속이다. 미완료 요청이 참조하는 OI fetch/JSONL 구간을 먼저 삭제하면 안 된다. T05 자동 정리 4개 mask와 공개 관측기 비활성을 유지한다.
