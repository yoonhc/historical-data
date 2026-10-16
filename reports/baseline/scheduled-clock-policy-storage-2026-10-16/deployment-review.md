# T16 저장 복구 보완 후보의 적용 전 검토

대상은 `scheduled-clock-policy-20261016-544c61aca49f1355`와 [manifest](manifest.json)의 정확한 해시다. 후보 구현·격리 검증까지만 완료했으며 운영 적용 계획/승인이 아니다.

[이전 후보의 전환 계약](../scheduled-clock-policy-recovery-2026-10-15/deployment-review.md)을 유지한다. 8개 업무 실행 명령·전용 waiter·topology/bootstrap guard/binding 연결의 전환 후보이고, timer 예약·UID·데이터 경로·기존 업무 잠금과 T05 정책은 유지한다. 두 수집기·Chrony·RTC·polling·부팅 보정 권한·자동 삭제 권한을 이번 범위에 추가하지 않는다.

## 이번에 해결한 경계

SQLite 연결 수명과 pending failure의 오류 분류 보존을 수정했다. OI/network의 결과 근거는 latest 대신 원래 fetch 행/JSONL 구간에 연결한다. 실제 이전 runtime이 같은 시험 데이터에 후속 관측을 남긴 뒤 새 wrapper의 원래 요청을 종결하는 경로까지 검증했다. [구현·시험](summary.md).

수정 전 후보 `5b02fc74867e5bab`의 request를 새 후보로 자동 승계하지 않는다. 아직 운영에 적용된 T16 request는 없고, 후보의 release 경로와 코드 해시도 입력 identity에 포함된다. 다른 설정/코드의 pending 요청은 기존처럼 명시적 대조 대상으로 남는다. 이번 원복 시험은 **같은 새 후보의 미완료 request를 보존하고, 이전 운영 runtime이 B를 기록한 뒤 동일 후보를 재적용하는 경로**다.

## 여전히 남은 관리자·전환 검증

1. 고정된 세 실행본과 wrapper를 전용 시험 경로에 설치해 UID 1000·최종 보호 옵션에서 실제 import·설정/저장·공통 잠금·원래 이력 읽기를 확인한다. 현재 17개 유닛 검사는 구문/순서 검사이며 실제 시스템 실행 검사가 아니다.
2. 시험 유닛에서 보류, 감시/기록 실패, 종료 제한과 마지막 request 저장, 다음 invocation의 복구를 연결한다. 운영 시간이나 네트워크를 바꿔 장애를 만들지 않는다.
3. 실제 현재 boot·해시·소유권·유닛·대기 job을 기준으로 새 구성/ledger 인계 도구와 중단·재개·원복 계획을 고정한다. coordinator 배타 잠금은 짧은 구성 전환에만 사용하고 실행 중 backup의 완료 후처리와 종료 대기가 서로 막히지 않게 한다.
4. 불완전한 구성에서는 새 시작을 보류한다. 기존 collector supervisor 유지와 pending 중 새 시작 거부, 같은 boot/closed/보정 증거·T05 baseline/완료 상한/생성 순서 보존을 확인한다. T15 승인이나 과거 T16 해시를 새 운영 승인으로 사용하지 않는다.

## 원복·재적용 시 요청 처리

구버전 runtime은 새 wrapper request를 자동 해석하지 않는다. 아직 필요한 고정 날짜/범위, 이미 저장된 결과, 발행/실행/대기 요청을 대조해 정확히 인계한다. 데이터·카탈로그·원장·request/attempt·실패 이력·T05 근거는 지우거나 과거 상태로 되돌리지 않는다. 구버전으로 원복하면 기존 strict/reference age 대기도 돌아온다.

같은 후보로 재적용할 때 OI의 원래 fetch 행과 network의 원래 JSONL 구간이 보존돼 있으면 latest가 B를 표시해도 A를 검증해 요청을 종결할 수 있다. sealed 결과뿐 아니라 결과 seal 실패로 남은 unsealed payload도 해당 원래 이력에 대조한다.

원래 이력이 없거나 해시가 다르거나 OI receipt가 모호하면 `finalization_failed`를 유지한다. 현재 latest를 복사하거나 request를 수동 complete로 바꾸거나 새 관측으로 옛 요청을 대신 완료시키지 않는다. 정확한 과거 기록을 확보할 수 있는지 점검하고, 없으면 근거 부족과 원래 범위를 보존한 명시적 처리 결정이 필요하다. 이번에 범용 승인/복구 UI는 추가하지 않았다.

보조 request·attempt·JSONL 전체의 자동 백업/회전·독립 복구는 여전히 후속이다. JSONL 보관을 정할 때 미완료 요청이 참조하는 구간을 삭제하지 않는 조건을 반영해야 한다. 이번 확인은 같은 호스트에서 보존된 근거를 사용하는 중단/원복 복구이며, 백업에서 요청 자체를 복원하는 보장으로 확대하지 않는다.

관리자·전환 검증 후 별도 운영 승인 단계에서 첫 처리/보류/재개, 다음 정기 backup/T05/system restic 연결과 같은 supervisor의 자연스러운 child 경계를 확인한다. T05 네 자동 정리 mask는 유지하며 삭제/반복 자동화는 별도 범위다.
