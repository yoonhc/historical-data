# T16 후보 적용 검토

상태: **구현·격리 시험·native unit 구문 확인 완료, 운영 적용 미승인/미실시**.
대상 `scheduled-clock-policy-20261015-90f97df305c662c2`의 전체 파일 해시는 [manifest](manifest.json)을 따른다.
이 문서는 실행 승인이나 root 인계 실행기가 아니다.

**10/15 후속 검토로 현재 후보의 진행 보류:** 감시 thread 종료 후 무감시 재개, 기록 오류의 시각 보류 오분류,
close 실패 시 최종 request 저장 누락을 재현했다. 업무 설정 identity와 종료 코드별 종결 근거도 보완해야 한다.
[코드 대조·재현·좁은 보완 범위](summary.md#1015-wrapper-장애-경로-추가-검토).
아래 순서는 해당 보완과 새 후보 고정 이후의 단계다. 기존 성공 검사나 native unit verify로 이 결함을 상쇄하지 않는다.

## 정확한 변경 접점

- 기존 8개 system 서비스의 실행 명령을 후보 wrapper로 바꾼다. 프로세스는 계속 `User=yoonhc`다.
  T13/daily, T05/preserve, 나머지/base 세 실행본 조합을 유지한다.
- 8개 업무의 기존 strict waiter/ExecStartPre를 새 업무 waiter + 실행 안의 현재 관측/판단으로 대체한다.
  새 waiter `marketdata-jobs-clock-ready.service`는 실제 부팅 준비 증거와 현재 관측만 확인한다.
  실제 업무에는 매 결정의 새 확인이 있으므로 waiter의 이전 성공을 영구 준비로 사용하지 않는다.
- 기존 `marketdata-clock-ready.service`, 두 collector, bootstrap 보정 구현·runtime 정책은 그대로다.
  새로운 waiter는 bootstrap의 관리 목록에서 waiter로 구분한다. 실제 업무 PID가 동작 중이면 초기 step 전 점검은 여전히 거부한다.
- 8개 timer의 파일·예약·Persistent/OnActive는 바이트 그대로 유지한다.
  서비스 timeout/restart·작업 잠금·데이터 루트·UID·server 재시도 설정도 그대로다.
- `scheduled-system/topology.json`, `clock-bootstrap.json`의 guard 파일 목록/해시, 새 업무 binding과 기존 T05 binding을 함께 바꿔야 한다.
  새 bootstrap profile을 ledger의 승인된 구성 이력과 연결하는 전환이 필요하다. 파일 해시만 고쳐 통과시키지 않는다.
- T05 baseline·completion/sequence·registry·기존 grant·checkpoint를 재등록하거나 수정하지 않는다.
  구성 변경에 따른 새 삭제 승인 scope는 나중에 별도 평가한다. 네 자동 정리 mask는 유지한다.

변경 전후 파일 해시는 manifest의 `before`/`after`, 최종 파일 내용은 [rendered](rendered/)에 있다.
원본 설치/전환 자료의 실패 기록은 보존한다. 이전 승인 ID가 새 코드를 자동 승인하지 않는다.

## 운영 전 남은 순서

1. 고정 후보와 의존성을 임시 root 소유 경로에 설치하고 UID 1000·실제 서비스 옵션·최종 명령 인자의 import/설정/잠금/저장 경로를 확인한다.
   이 후보의 unit verify는 실행 경로를 치환한 구문 시험이며 이 단계를 대신하지 않는다.
2. 새 waiter의 닫힌 준비/현재 관측 확인, pending 시 거부, 시각 보류 후 같은 날짜·작업 재개를 임시 system 유닛에서 확인한다.
   실제 Chrony 응답을 끊거나 시각을 바꾸지 않고 주입 가능한 시험 provider/시험 저장소만 사용한다.
3. T15의 검증된 transaction 구조를 재사용하되 **새 waiter 추가·8개 유닛·profile/binding·ledger 연결**을 포함하는 새 계획을 만든다.
   실제 현재 boot와 해시, 승인된 작업 날짜/원장, 실행 중 업무 및 waiter 뒤 job을 다시 고정한다.
   별도 handoff 잠금은 인계 전체, coordinator 배타 잠금은 짧은 구성 commit 구간만 점유한다.
4. 실행 중 보존 업무는 정상 종료를 기다린다. 종료 대기 동안 T05 완료 후처리의 coordinator 공유 잠금을 막지 않는다.
   중간 불일치에서는 새 업무/collector 재시작을 보류하며 기존 실행 collector를 중지하지 않는다.
5. 중단·재개·원복과 새 기록이 남은 원복을 최종 계획으로 검증한 다음 정확한 승인 범위를 제출한다.
6. 승인 후 적용에서는 구성 commit과 현재 시각 준비, 시작 요청과 실제 자료 완료를 분리한다.
   첫 8개 업무·T13 냉각/완료·다음 정기 backup/T05/restic·동일 collector supervisor의 자연스러운 경계를 확인한다.
   자연스럽게 reference 지연이 발생하지 않아도 운영 장애를 주입하거나 검증 범위를 늘리지 않는다.

## 원복 계약

이전 `/opt` 실행본·8개 system 유닛·topology·binding을 보존한 새 계획의 rollback으로 되돌린다.
같은 boot ID·closed·step 시도/결과·과거 품질·구성 이력은 유지한다. 새 waiter 제거 시에도 진행 중 업무와 의존 job을 먼저 분류한다.

새 원장·자료·receipt·T05 근거·보조 시각 로그·고정 날짜 request는 삭제하거나 과거 DB로 덮어쓰지 않는다.
새 기록을 읽고 backup/restore를 수행하는 이전 코드 조합은 격리 시험했다.
보류된 backup/quality의 고정 날짜는 원복 완료 전에 현재 원장·완료 사본과 대조한다.
구버전은 새 request 파일을 자동 재개하는 코드가 아니므로 무조건 timer만 켜거나 작업을 두 번 발행하지 않는다.
완료 확인/미전달 요청을 기록하고 정확한 재요청 또는 명시적 보류로 인계한다. 이것은 다음 실제 인계 시험의 확인 항목이다.

원복하면 이전 reference age 600초의 공통 strict 대기도 돌아온다.
T16의 새 JSON 필드를 지우거나 구버전 strict policy를 몰래 완화하는 방식으로 원복하지 않는다.

## 적용 범위 밖

미동기화 부팅 시작, 조회 불가 상태의 진단 시작, collector 초기/복구 gate 변경, Chrony polling·시간원·RTC/step,
T05 삭제/자동화, 원장·관측기의 신규 기능, 성능 최적화는 포함하지 않는다.
새 보조 로그 전부의 백업/회전·상태 조회 진입점 확대는 별도 후속으로 표시한다.
