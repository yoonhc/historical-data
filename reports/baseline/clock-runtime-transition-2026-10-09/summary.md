# T04·T11 전환·원복 검증 완료

2026-10-09, 최종 운영 상태 대조 **18:49 KST**.
후보의 중단·재개·원복, 기존 유지보수 실행본과의 호환, 실제 격리 systemd 종료·재시작 검증을 완료했다.
**운영 적용은 하지 않았다.** 고정 배포본과 정확한 전환 범위를 별도 승인용으로 제출한다.

## 결과

| 검증 | 실제 결과 |
|---|---|
| 최종 수집 후보 | 관련 305건 통과, skip 0 |
| 전환 도구 | 58건 통과 + tar를 깨끗한 별도 경로에 추출한 같은 58건 통과 |
| 실제 버전 조합 | 새 runtime → 기존 workspace pack/backup/restic → 이전 `/opt` runtime으로 추가 수집 → 다시 backup/restic 성공 |
| 복원·조회 | 두 backup ID 각각 명시적 restic ID 추출·프로그램 복원·독립 재백업; 시각 조회 20개 일치 |
| 정상 systemd 종료 | 같은 supervisor의 다음 child 시작, child 저장 후 등록 worker를 기다리고 종료 |
| SIGKILL 이후 | 이전 PID 소멸, 새 invocation/child, 미완료 spool 복구, 이후 정상 종료 |
| 정리·운영 유지 | 시험 유닛/경로 모두 제거, 정리 오류 0; 운영 설정·ledger·Chrony·정리 mask 유지 |

수집 후보와 전환 도구의 검사 수는 서로 다른 범위다. 깨끗한 경로에서 다시 실행한 58건을 새 테스트
58건으로 더하지 않았다. 운영 예약 성공이나 실제 NTP 품질을 이 숫자로 대신하지 않는다.
[기계 판독 결과](validation.json), [검증 범위·실패 이력](validation-notes.md), [재현 절차](reproduce.md).

## 실제 root 시험에서 보완한 한 가지

정상 종료 때 provider를 조회하기 **전에** 시각 표본을 얻던 순서 때문에 새 관측을 평가하면서
`provider_observation_expired`라는 잘못된 최종 실패가 생겼다.
조회 완료 **후** 표본을 얻도록 수정했다. 10ms 조회 지연 입력에서 수정 전 실패·수정 후 통과를 확인하고
최종 후보로 305건을 실행했다. 정책값·Book 계산·시간원·부팅 보정 권한은 변경하지 않았다.

원래 후보 `26e7fd4bbd0a68a5`는 그대로 남기고 수정본을 새로 고정했다.
[최소 패치](collector-correction/correction.patch), [수정 전 회귀](shutdown-order-before.xml),
[수정 후 회귀](shutdown-order-after.xml), [최종 305건](collector-correction/regression.xml).

관리자 실행을 여러 번 요청한 이력도 구분했다. 첫 실행은 시험 저장소의 `data/` 누락,
두 번째는 빈 보호 옵션을 생략한 조회 출력의 판정 문제였다. 두 번째 기록에서 위 실제 후보 결함도 찾았다.
세 번째는 정상 종료가 통과했으나 강제 종료 명령의 오류 반환에서 시험이 중단됐다.
네 번째는 남은 강제 종료 경로만 실행해 통과했다.

`systemctl kill`의 종료 코드 1과 보조 프로세스 오류는 마지막에도 기록했다. 이를 명령 성공으로 바꿔 세지 않고,
기존 두 PID의 소멸·새 실행·복구된 데이터·후속 정상 종료를 확인했다.
강제 종료 collection의 끝부분은 `collection_end_unknown`으로 남았다.
[정상 종료 성공 근거](system-preflight-result-2e3a2ee7012c4920.json),
[강제 종료·재시작 성공 근거](system-preflight-result-08b7a916b7fc4a46.json).

## 배포본과 승인 범위

| 항목 | 고정 식별자 |
|---|---|
| 수집 후보 | `clock-evidence-policy-20261009-65296619a374c325` |
| 전환 도구 | `collector-transition-20261009-1c7c4120369fd893` |
| 전체 계획 ID | `22706e571f3a46a8745803b18bca2efacc3318c3e05b2309df6f3c7c37c621e6` |

[적용·중단·원복 절차와 전체 해시](deployment-review.md)에 정확한 경로와 변경을 정리했다.
승인되면 새 root 소유 배포본을 설치하고 두 collector를 정상 종료한 뒤,
runtime 프로필·collector 유닛·bootstrap의 연결 해시 및 전환 ledger를 함께 처리한다.
기존 boot ID·closed·보정 증거·과거 품질 기록은 유지한다. 불완전한 조합에서는 업무 시작을 거부한다.
현재 계획은 같은 부팅에 한정되며 [승인 양식](approval.template.json)은 **false**다.

원복은 코드/설정의 실행본만 되돌린다. 이미 생성한 artifact·collection·인덱스·spool을 지우거나 catalog를
과거 백업으로 덮어쓰지 않는다. 이전 collector의 엄격한 시작 정책도 돌아오며, 새 근거 조회에는 보존한 새 CLI를 쓴다.
[실제 새 자료 이후 원복·백업 결과](mixed-versions-result.json).

## 아직 운영에서 확인하지 않은 것

- 실제 운영 경로에 설치·전환한 뒤 새 프로세스의 수집·저장·collection 연결·닫힌 시각 근거 확인.
- 첫 실제 child 종료/다음 시작, 새 근거의 예약 checkpoint/restic 편입·복원 조회.
- 기존 v1 로그의 백업 편입, 다른 8개 업무의 정책 분리, T05 삭제 시각 검사.

root 시험은 최종과 같은 보호/종료 설정을 적용했지만 경로·Chrony 관측·child 입력은 시험용이다.
짧게 구성한 child 전환을 실제 한 시간의 장기 운영 관찰로 세지 않는다.
Chrony/RTC/시간원·기존 원문·백업 정책·정리 grant를 변경하지 않았고 자동 정리는 계속 masked다.

별도로, **거래소 ID·내용·로컬 수신 시각까지 완전히 같은** 합성 event segment를 다른 collection에
재사용하면 연결이 누락되는 기존 코드의 경계 사례를 발견했다. 이전 코드끼리도 재현됐으며 운영 발생은
미확인이다. 이번 후보에 수정을 추가하지 않고 [TODO T12](../../../TODO.md)에 후속으로 남겼다.
T03의 aggTrade 보강분 운영 백업 편입은 이미 완료한 결과를 유지하며 다시 시험하지 않았다.
