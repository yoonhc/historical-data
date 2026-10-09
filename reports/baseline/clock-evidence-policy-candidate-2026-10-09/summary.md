# T04·T11 최소 후보: 시각 근거 보존과 reference 지연의 수집 허용 분리

2026-10-09. **후보 구현·격리 검증 완료, 운영 미반영.**
최종 후보는 `clock-evidence-policy-20261009-26e7fd4bbd0a68a5`다.
현재 운영은 여전히 `system-bootstrap-20261007-f85eb8321a19c1ee`다.

먼저 T03을 실제 결과로 완료했다. aggTrade REST 보강분의 원문 265개·표준 265개가
14:33:29 완료 checkpoint에 포함됐고 같은 사본의 예약 restic `check --read-data`가
14:50:55 KST에 성공했다. [별도 편입 보고서](../aggtrade-gap-repair-2026-10-09/backup-inclusion.md).
이후 아래 후보만 구현했으며 시간원 설정·시계·서비스는 바꾸지 않았다.

## 바뀌는 판단과 그대로 남는 판단

같은 부팅에서 실제 초기 준비를 통과하고, 현재 Chrony 조회·provider·연속 감시가 유효하며,
기존 보정량 100ms·조건부 오차 500ms·skew 등의 조건을 통과할 때만 좁은 예외를 허용한다.
그 상황의 **양의 reference age 600초 초과**는 `reference_stale=true`로 기록하면서
다음 원문 수집 child의 시작을 허용한다. 600초를 늘리거나 미래 reference를 함께 허용하지 않는다.

| 상황 | 후보의 다음 child 시작 |
|---|---|
| 현재 시각 추정 정상, reference만 600초 초과, 초기 준비·감시 연속성 유지 | 경고를 기록하고 허용 |
| 최초 준비 미확인, provider 조회 실패·관측 만료, 미래 reference, 오차·skew 초과 | 거부 |
| 시각 점프·관측 공백·suspend/provider 변경을 관측 | 이력 보존, stale 단독 예외 중지 |
| 과거 hard fault 뒤 현재 엄격한 준비 조건을 다시 만족 | 새로운 child 시작 가능. 과거 이력은 유지 |
| supervisor 자체가 종료·재시작됨 | 엄격한 현재 준비부터 다시 확인. 미관측 공백을 연속 감시로 간주하지 않음 |
| 시각 근거 기록·보관 실패, 공간/대기열 한도 초과 | child를 종료하고 오류 보고, staging 보존 |

현재 추정 품질 `current_estimate_usable`, 신선도 `reference_stale`, 시작 허용
`raw_start_allowed`, 누적 `history`를 나눴다. 기존 정책의 `clock_usable=false`와
과거 위반은 정상으로 덮어쓰지 않는다. 원래 실행 중 수집을 유지하며 시각 이상을 기록하던
동작과, **새 child 시작 허용**을 구분한다. 모든 시각 오류에 원문 수집 시작을 허용한 변경은 아니다.

## 한 시간 경계에서 감시를 유지하는 방법

새 `capture_runtime.py` supervisor가 살아 있는 동안 기존 3,600초 collector child를
순서대로 실행하고 사이의 15초도 감시한다. 기존처럼 한 시간마다 supervisor까지 종료해
다음 서비스 시작에 엄격한 gate를 다시 적용하던 경계만 바꿨다.
실제 수집 CLI의 옵션·호가 계산·범위 교체·요청 예산·raw 형식·재생 판정은 그대로다.

정상 종료 코드 0 외에, **같은 run·boot·child ID의 저장된 최종 보고서가 `partial`인 종료 코드 2**도
다음 child로 이어간다. 수집 실패, 저장 실패, 실행 중 보고서, 다른 child의 보고서는 이 근거가 아니다.
partial과 손실 기록을 지워 정상으로 바꾸지 않는다. 이 구분은 초기 후보 검토에서 추가한 보완이다.

최초 시작 및 supervisor 재시작은 기존 `clock_gate.py`와 `wait-ready`를 그대로 통과해야 한다.
기존 부팅 준비·step 제한과 다른 예약 업무의 엄격한 gate는 변경하지 않았다.

## T04: 근거 보존과 조회 계약

신규 WS collection 보고서에 boot ID·supervisor run ID·child ID·stream·정책 버전·
시작/종료 wall/monotonic/boottime 표본을 연결한다. REST 보강에 WS 시각 근거를 붙이지 않는다.
시각 관측에는 원래 Chrony 응답·provider·판정·누적 이력·sampling span을 보존한다.

시각 JSONL은 300초 또는 1MiB에 닫고, gzip의 불변 원문 artifact와 fetch 기록으로 등록한다.
기존 카탈로그·raw 파일 검증·packing·checkpoint·restic·restore 경로를 그대로 사용한다.
시장 메시지 schema나 기존 backup manifest/source_root는 변경하지 않는다.
카탈로그에는 URL 조회 인덱스 하나를 추가한다.

- 각 append를 fsync하고 닫힌 chunk를 순서대로 보관한다. DB commit 뒤 바이트가 같은 staging
  중복만 제거한다. 기존 v1 로그·운영 데이터·백업·시험 사본의 정리는 아니다.
- worker 대기열 4개 + 처리 중 1개 + 열린 chunk 1개를 한도로 둔다. 대략 6MiB 수준의
  정상 staging 상한이며 레코드/헤더 여유는 별도다. 실패하면 중단하고 남은 파일을 보존한다.
- 10GiB 여유 공간과 다음 chunk 여유를 요구한다. 닫힌 artifact에는 기간 삭제를 적용하지 않는다.
  로그도 계속 누적되므로 전체 저장 공간이 고정되는 것은 아니다.
- 일반 보관 경합 동안 감시 thread는 계속 기록한다. 시작·종료 경계에서는 보관 완료를 기다리고,
  시작 직전 현재 Chrony와 감시 간격을 다시 확인한다. 보관 대기 때문에 오래된 준비를 재사용하지 않는다.
- 강제 종료 뒤 남은 staging은 다음 child 이전에 재등록한다. DB commit 후 unlink 전 종료도
  같은 artifact로 처리한다. 손상되거나 헤더만 남은 파일은 삭제하지 않고 시작을 보류한다.

`marketdata --root <저장소 또는 checkpoint> clock-evidence --collection-id <ID>`는
읽기 전용 카탈로그·기존 잠금으로 관련 표본 구간을 반환한다. 닫힌 근거가 충분하면
`evidence_state=available`이며 **시각 또는 시장 데이터가 정상이라는 의미가 아니다.**
관련 구간 양 끝의 판정을 함께 제공하고, 나중의 정상 관측으로 직전 불확실 구간을 정상화하지 않는다.
긴 supervisor 실행에서도 관련 chunk와 인접 경계만 읽는다.

다음은 명시적인 한계다.

- 기존 v1 로그/과거 collection에는 새 연결을 소급 생성하지 않았다. 파일은 보존하지만
  **그 로그의 자동 백업 편입은 아직 미완료**다. 해당 조회는 `unknown`이다.
- 열려 있거나 아직 commit하지 않은 끝부분은 checkpoint에 없는 근거다. 나중에 운영 저장소에
  commit돼도 과거 checkpoint가 소급 완전해지지 않는다. 5분 회전은 최대 유실 시간 보장이 아니다.
- 표본 사이의 시각 품질 근거이며 모든 시장 이벤트의 실제 도착 시각 정확도를 인증하지 않는다.
  monotonic의 진행이 UTC 정확도를 입증하는 것도 아니다. `received_at`은 수정하지 않는다.
- supervisor 장애 후 stale 상태에서 자동 재개하는 예외는 없다. 엄격한 재준비를 요구한다.

## 실제 수행한 격리 검증

최종 archive를 별도 경로에 추출하고 frozen lock으로 non-editable 설치했다.
31개 package Python 파일이 설치본과 일치하고 실제 import는 해당 venv 내부였다.
최종 소스 175개 해시를 확인했다. **304 passed, 0 failed, 0 skipped**:
신규 42건 + 기존 관련 회귀 262건. 전체 프로젝트의 모든 테스트를 실행했다는 뜻은 아니다.

| 검증 | 근거 |
|---|---|
| stale-only 허용·미래 reference·현재 조회 실패·다른 오차 거부, 과거 이력 유지 | 신규 정책 검사 |
| 두 stream 각각 실제 child 프로세스 2개 연속 실행, 마지막 partial/다음 세션/근거 연결 | provider는 합성, 파일·프로세스·DB는 실제 |
| 기록 오류 시 child 종료, publication 경합·실패, 실제 SIGKILL 전/후 재개 | staging 보존과 commit 중복 방지 |
| checkpoint→restore→조회 대조→독립 backup/verify | 두 stream 모두 |
| restic init→backup→전체 읽기 검사→명시적 ID 추출→프로그램 restore→조회→독립 backup/verify | 운영과 분리한 실제 restic 0.19.1 저장소 |
| 기존 호가 실패 공개·독립 두 사건·aggTrade·backup·bootstrap | 기존 판정 유지 |
| systemd 구문 | 실제 실행파일이 있는 clean 경로로 release 경로만 치환한 유닛 검증. root 운영 환경 검증을 대신하지 않음 |

격리 restic snapshot은 `9d4f49d74b4cd56cc9b40991a2a1b9fe69a9079c6c2a06c65582c6e7525875d6`이다.
추출·복원·재백업 조회가 동일했으며 원래와 독립 backup의 source_root는 각 실제 생성 위치다.
암호는 보고서에 복사하지 않았다. [시험 결과·조회·manifest](isolated-restic-evidence/result.json).

초기 시험 실패도 보존했다. 첫 전체 검사 2건은 후보 venv 미설치 때문이었고, 이후 299건을 통과했다.
후속 query 성능 검사 1건은 non-editable 설치본을 재설치하지 않아 이전 모듈을 읽은 문제였다.
명시적 재설치·source/import 해시 대조 뒤 해결했다. 최종 clean 시험에는 이 문제가 없다.
중간 선택 검사에서는 restic 환경변수 누락으로 1건을 건너뛰었지만 최종 304건에는 skip이 없다.
기존 읽기 전용 `release/`를 이동하지 못한 정리 시도는 중단하고, 그대로 보존한 채 `release-final/`을 만들었다.
초기 `a2cddb95dd879544` 후보와 그 결과는 최종 배포 근거가 아니다.

## 고정 산출물과 다음 경계

- [최종 source manifest](release-final.json), [최종 archive](source-final.tar.gz), [부모 대비 patch](candidate-final.patch).
- archive SHA256: `7296234b12dc09fbfdc1b90b0efdb54d65f7bf8b3742a432b3c1248f8d7ccfd1`.
- [304건 clean 결과](clean-final-regression.xml), [실제 import 대조](clean-final-imports.json), [재현 절차](reproduce.md).
- [운영 전후 불변 대조](operating-after.json): 운영 설정·ledger·기존 runtime 및 설치 소스 변경 없음.
  checkpoint 정리 service/timer와 기존 사용자 collector의 `/dev/null` mask 유지.
- [정확한 적용 대상과 되돌리기](deployment-review.md). 준비한 것은 **실행 불가 검토 계획**이며 승인 값은 false다.

후보 로직 검증과 운영 반영은 별개다. 운영 전에는 이 runtime 범위의 해시 연결을 안전하게 바꾸는
전환 실행 절차의 중단·재개·원복 검증과 최종 root/systemd 접근 확인이 필요하다.
T10의 시간원 전환 실행기를 재사용해 통과시킨 것으로 보고하지 않는다.
실제 새 collection의 근거 생성·예약 checkpoint/restic 편입은 승인된 적용 이후의 관찰이다.
T05 자동 정리와 T10 시간원 교체는 이번에 적용하지 않았다.
