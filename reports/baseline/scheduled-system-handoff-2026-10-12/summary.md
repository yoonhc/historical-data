# T15 관리자 사전 점검과 운영 인계 준비

2026-10-12 23:24:53 KST. **두 추가 연결·관리자 사전 점검 완료, 운영 인계 실행기와 최종 검토 계획 준비. 운영 이전 미실시.**
업무별 시각 정책·Chrony·collector·T05 삭제 권한은 이번 범위에 추가하지 않았다.

후속: **10/13 00:32:58 별도 승인 후 실제 이전 완료.** 이 문서의 미적용 표시는 위 10/12 시험 종료 시점이다.
[실제 적용·초기 운영 확인](../scheduled-system-deploy-2026-10-13/status.md) 이후
**10/13 22:12 정오 실제 처리·정기 backup/T05/system restic 연결까지 확인해 T15 1단계 운영 검증을 완료**했다.
[최종 결과·별도 후속](../scheduled-system-operating-completion-2026-10-13/summary.md)을 따른다.

## 고정 범위

| 대상 | 식별자 |
|---|---|
| 기존 업무 소스 배포본 — 변경 없음 | `scheduled-manager-20261012-255d64b78ffa8328` |
| 수정된 구성 계획 | `0c9b85223c2ad851434857b4eaa86f11e94595b4663134f98881ede02fefd8fe` |
| 관리자 시험·인계 도구 | `scheduled-handoff-20261012-b48b6beb0b9b72b9` |
| 사전 점검 전 인계 검토 계획 — 실행 불가 | `a53d36e50abf9a962c1cd9b787ce1d91d97615b117e34b8c01ab6f509159fbd6` |
| 관리자 통과 근거·실제 소유권을 포함한 최종 인계 계획 | `6a71f295ec760f442edf0f88b04c6cf9378d9383fb9c45c0dc7c8c140eac3f2b` |
| 같은 부팅 전제 | `eb163bf2-6642-4336-b4d6-6cd58771b13d` |

정확한 파일별 해시는 [tool-seal.json](tool-seal.json), 구성 before/after는
[configuration-plan.json](configuration-plan.json), 소유권·실효 유닛·추가 링크·보호 대상은
[host-plan-preflight-passed.json](host-plan-preflight-passed.json)에 있다. 승인 template은 `enabled=false`다.
root 시험 통과 보고서와 실제 소유권을 연결해 **별도 최종 인계 계획 ID**를 만들었다.
현재 검토 계획이나 과거 후보 검토를 운영 승인으로 재사용하지 않는다.

## 추가한 두 연결

1. **새 기록이 있는 상태의 원복:** 실제 설치된 T05의 checkpoint 생성·완료/순서 근거와
   작은 격리 restic 저장소를 사용했다. system adapter의 실행 기록을 만든 뒤 구성만 원복하고,
   기존 user 경로로 추가 checkpoint·restic 저장·전체 읽기 검사를 수행했다.
   원래 system receipt 바이트, snapshot 연결, baseline과 선행 근거가 보존되고 새 사본이 후행으로 연결됐다.
   systemctl/busctl 응답만 대역이며 실제 system timer 출처는 별도 관리자 시험 대상이다.
2. **수집 supervisor와 전환 상태:** 설치된 수집 supervisor/gate를 사용해 depth·aggTrade 각각
   apply/rollback을 확인했다. 같은 supervisor는 pending 중 reference 지연이 있어도 다음 child를 시작했고,
   독립된 새 시작은 pending을 거부했다. 구성 확정/원복 뒤 엄격한 준비와 closed/보정 증거를 다시 확인했다.
   시계·child·파일 경로는 격리 입력이며 운영 수집기를 종료하거나 장애를 주입하지 않았다.

## 발견·수정한 경계

**후보 유닛의 실제 CLI 진입점:** OI 3종과 pack의 `python -m marketdata`는
`marketdata.__main__` 부재로 실패했다. 기존 CLI 모듈인 `marketdata.cli`로 네 명령만 수정했다.
관련 topology·bootstrap guard·T05 binding 해시를 함께 갱신했다. 업무 소스는 바꾸지 않았다.
[정확한 7개 구성 차이](entrypoint-correction.json). 원래 계획 `70c1723f…`는 당시 근거로 보존하며 적용에 사용하지 않는다.

**첫 관리자 시험 중단:** [e4b453a62d8342d7 결과](system-preflight-result-e4b453a62d8342d7.json)는
첫 OI 시험 유닛의 시작 단계에서 실패했다. 시험 코드가 빈 `Requires=`/`After=`로 의존성을 제거하려 했지만,
미설치 시스템 `marketdata-clock-ready.service` 참조가 남았다. 업무 본문/접근 worker는 실행되지 않았다.
운영 파일·ledger·서비스 신원·목록은 동일했고 시험 유닛/경로 정리도 완료됐다.

시험 유닛을 nonce 전용 준비 유닛에 명시적으로 연결하도록 수정했다. 운영용 strict gate는 그대로다.
실패 시 등록된 시험 유닛의 job·종료 상태·bounded journal을 정리 전에 보존한다.
수정된 준비 유닛과 접근 유닛 **9개 native 검사**, 당시 **인계/시험 63개 검사**가 통과했다.
이는 아래 실제 관리자 시험과 별도 근거다. 과거 시험 도구는 `tool-history/`에 보존했다.

**두 번째 관리자 시험:** [97f22b03bbf940c3](system-preflight-result-97f22b03bbf940c3.json)에서
8개 최종 환경·system timer 출처·수동 실행 구분·Persistent 따라잡기·양 manager wants 링크 확인까지 통과했다.
사용자 시험 유닛에 불필요한 `User=yoonhc`를 지정해 보조 그룹 설정 권한 오류 `216/GROUP`으로
잠금 시험이 멈췄다. 운영 파일과 프로세스는 유지됐고 19개 시험 유닛과 경로 정리는 완료됐다.

**잔여 관리자 시험 통과:** 사용자 manager가 이미 실행하는 계정을 그대로 쓰도록 시험 유닛만 수정하고,
`--scenario lifecycle`로 양 manager의 정상 종료·대기/재시작 취소·잠금 충돌·mask/원복을 확인했다.
[a6d642d447dc4c69 통과 원본](system-preflight-result-a6d642d447dc4c69.json)은
앞서 통과한 부분의 원본 해시·동일 운영 상태·변경되지 않은 공통 코드를 연결한다. 이미 통과한 8개 환경/timer를 반복하지 않았다.
실패한 두 보고서를 성공으로 고쳐 쓰지 않았다. 마지막 10개 시험 유닛·경로 정리 완료,
**운영 node 94개·boot ledger·Chrony/두 supervisor 신원·유닛 목록 동일**, 정리 mask 유지다.

## 검증과 남은 범위

서로 다른 추가 검사는 **72개**다: 인계 61개 + 시험 렌더링/진단/기존 근거 재사용 5개 + collector 연결 4개 + 실제 자료 원복 1개 + 부분 원복 요청 대조 1개.
수정된 구성 계획으로 기존 전환 **152개를 재검증**했다. 기존 240개·별도 경로 152개와 중복 합산하지 않는다.
[검증 범위·원본 결과](validation.md).

운영 인계 실행기는 고정 파일 복사, 정확한 persistent mask, 양 manager 정상 종료 대기,
대기 요청 기록/취소, 원래 transaction, 실효 유닛·소유권 확인, timer 링크·활성화와 재개/원복을 연결한다.
실행 중 업무를 강제 종료하는 명령은 없으며, 모호한 시작 요청은 자동 반복하지 않는다.
[정확한 적용·중단·원복 절차](deployment-review.md).
부분 적용을 원복할 때 **이전 apply에서 취소하고 아직 전달하지 못한 요청은 별도 수동 대조·재요청 단계**다.
이 실행기는 원복 실행에서 취소한 요청만 자동 재요청하므로 두 기록을 합쳐 확인해야 한다.
이를 무인 원복으로 표현하지 않는다. 원래 요청은 보존되며 기존 `systemctl --user start <정확한 service>`로 재평가한다.

**10/13 적용 전 코드 대조:** 전환 의도 기록의 coordinator 배타 잠금은 반환 전에 해제하며,
`drain()`은 별도 handoff lock만 유지한다. 설치된 T05의 완료 후 관측은 같은 coordinator의 공유 잠금을
사용하므로, 인계가 배타 잠금을 잡고 백업 종료를 기다리는 순환은 없다.
설정 변경·확정 때는 종료 대기 후 coordinator를 다시 잡는다. 짧은 의도 기록과의 일시 경합 가능성은
별도로 명시했고, 운영 동시 실행 시험을 추가했다고 보고하지 않는다.
[잠금 범위·설치 전 조회·원복 완료 기준](deployment-review.md#종료-대기-중-coordinator-잠금--1013-코드-대조)을 보완했다.
실행 코드·도구 해시·최종 계획은 그대로다.

**아직 남음:** 최종 계획의 별도 운영 승인, 실제 이전과 첫 업무 실행·restic 출처·
다음 정기 backup의 T05 연결·자연스러운 collector 경계 확인.
최종 `/opt/marketdata/releases/...` 경로의 설치·실행은 적용 단계에서 다시 확인한다.
시험용 `/opt/md-t15-*` 실행을 최종 운영 설치 성공으로 표시하지 않는다.

8개 strict waiter/600초 정책은 유지한다. 이전 뒤 reference age 대기가 남을 수 있다.
업무별 시각 요구 분리·`reference_stale` 제거·polling 복원은 [TODO의 후속](../../../TODO.md)을 따른다.
공개 HEAD 관측기 비활성과 사용자/시스템 자동 정리 mask 4개도 유지한다.
