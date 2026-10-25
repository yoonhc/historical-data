# T18 제한된 공간 확보와 현재 수집 복구

**최신 대조: 10/21 23:23 KST.** 가용 공간 **17,089,146,880바이트(17.09GB/15.92GiB)**이며, 두 supervisor는 10/19 자연 재개 당시 PID/invocation을 유지한다. 현재 child의 collection은 depth 5,253건/aggTrade 34,854건을 저장했고 최신 원문/segment·시각 chunk 표본도 다시 해시 대조했다. 이 숫자는 현재 child의 건수이며 10/19부터의 누계가 아니다. [현재 자료/시각 대조](recovery-observation-20261021-142325.json), [실제 관리자 상태](current-systemd-20261021.txt).

archiver는 유휴이고 timer는 계속 inactive/enable 유지다. **10/20·10/21 사본 두 개가 restic 미보관**이며 backup ID는 `a284b57f…`·`ee64631d…`다. T05의 10/20·21 예약은 기존 정책대로 `unarchived_checkpoint/deferred/75`, 삭제 0개로 끝났다. recurring·manual·Chrony·유닛·프로필·ledger를 포함한 고정 보호 파일 해시는 모두 유지된다. [정확한 사본 목록·T05 run 원본·보호 입력](current-preservation-20261021.json). timer 재개·후보 배포는 아직 승인되지 않았다.

## 완료한 운영 조치

사용자가 승인한 timer 일시 정지 후 별도로 operation `d8d3bf23bc43518e41928294779c9ee5f5f1de3955527937be262609ab80f650`의 공간 확보를 승인했다. 10/19 **22:59:27–58 KST**에 고정 도구가 보호 입력·같은 boot·timer 정지·archiver 유휴·기존 잠금을 확인하고 **정확한 1,952개**를 처리했다.

- 출력 1,956개의 바이트·해시·권한과 모든 원래 경로를 유지하고, 권한별 4개 본문을 공유한다. receipt·archive ledger·보호 입력은 불변이다.
- 가용 공간은 **7,530,500,096 → 22,980,296,704바이트**, 관측 증가 **15,449,796,608바이트(15.45GB)**다. 동시 수집이 있으므로 고정된 대상 할당량 15,450,640,384바이트와 가용량 변화는 구분한다.
- 서비스 제어·ledger 쓰기·timer 재개는 없었다. 이후에도 archiver inactive·job 없음, timer inactive/enable 유지다.
- [적용 원본](dedup-audit/433a07ca13df4f63b2172d1204fd4e0e.json), [경로별 감사](dedup-audit/433a07ca13df4f63b2172d1204fd4e0e.jsonl), [승인 범위·보존/변경 계약](review.md).

완료된 apply를 확인 목적으로 다시 실행하지 않는다. 전체 136GB의 정리 완료를 의미하지 않는다.

## 현재 자료 수집 — 자연 재개 및 실제 저장 확인

기존 `Restart=always`가 공간 확보 도중 aggTrade **22:59:48**, depth **22:59:53 KST**에 새 supervisor를 시작했다. 추가 start/reset-failed나 시각 정책 변경은 하지 않았다.

23:01:56 KST에는 다음을 연결했다. [읽기 전용 원본](recovery-observation-140156.json), [관찰 코드](observe_recovery.py).

| 대상 | depth | aggTrade |
|---|---|---|
| supervisor PID | 3530137 | 3529954 |
| invocation | `8f9f604f45fa44b3833aca627834f391` | `3fa620f0e63341b9af76ff35b030b0c6` |
| 새 collection | `b8e9a91742f445b2a8096795a61fb338` | `82cc8a68ebcf4e90b46500d4c791bfa9` |
| 새 시각 run | `4ac89ce47872461a8604f8898b188798` | `604ac513b84b4aaa9565cc1a58d3af10` |
| 당시 저장 메시지 | 1,151 | 6,102 |

각 collection의 최근 segment 2개와 원문 파일을 실제 읽어 catalog의 SHA-256과 대조했고, 새 시각 run의 게시된 chunk 2개씩도 읽어 해시를 확인했다. 최신 시각 표본은 monotonic 기준 약 0.95–1.60초 전이며 systemd PID/invocation 및 collection의 run 연결이 일치했다. 전체 무결성 검사나 대규모 복원은 하지 않았다.

당시 reference age 약 698–699초로 기존 `clock_usable=false/reference_stale` 표시가 있었지만, 기존 v2 정책의 `current_estimate_usable=true/raw_start_allowed=true` 및 신규 표본·저장은 이어졌다. 이를 모든 시각 품질 값이 정상인 것으로 바꾸어 기록하지 않는다. Chrony PID/invocation과 정책은 그대로다.

## 과거 공백·pack은 별도 확인

현재 스트림 재개는 중단 구간의 복구 완료가 아니다. aggTrade는 기존 자동 보강 collection `e911140165944bb29aa7f9806469024c`에서 **20페이지/20,000건**을 저장한 뒤 `page_budget_exhausted/partial`을 남겼다. 요청 ID 구간은 3489785614–3490096742이고 실제 보강 끝은 3489805613이다. 남은 구간을 강제로 완료시키거나 예산을 초기화하지 않았다. depth는 재연결 시 새 snapshot을 취득하며 과거 당시 호가 관측을 복원했다고 주장하지 않는다.

pack은 이전 용량 실패·시작 제한 기록을 유지한다. 현재 실행/job이 없고 기존 timer의 다음 예약은 **10/19 23:15 KST**다. 우선 자연 예약 상태를 관찰하고, 수동 시작이 실제 필요한 경우에만 정확한 서비스의 한정된 복구 조치를 별도로 승인받는다. 현재 수집을 pack 완료까지 미뤄 표시하지 않는다.

**후속 확인 완료:** pack은 실제 **10/19 23:15:01–23:17:23 KST** 자연 예약으로 완료됐다. 7,714개 파일을 검증된 8개 pack에 보존하고 개별 복사본을 정리했으며, `phase=finished/ok=true`, 요청 `0afa17f3… complete/0`, 감시 정상 종료와 보고서 해시를 연결했다. 수동 start/reset-failed는 없었다. [원본 요청/보고서 대조](pack-natural-recovery.json). 10/21 23:15 예약의 현재 요청도 complete/0이다. 과거 interrupted collection은 그대로 보존했고, [중단/재개 catalog 대조](interruption-and-gap-audit.json)에 별도로 연결했다. 이 대조의 집계 조회는 오래 걸렸으므로 추가 전량 집계를 반복하지 않으며, 현재 관찰은 시간/자료 인덱스를 사용하는 소량 표본과 조회 시간 제한으로 수행한다.

## Archiver와 재발 방지 후보

10/19 정지 당시 미보관 편입 checkpoint는 0개였고, **10/21 현재는 위 두 개가 지연 중**이다. 재개는 미승인이다. `OnActiveSec=5s`·`Persistent=true`에 따른 즉시 요청 가능성을 재개 계획에 포함해야 한다.

첫 수정은 성공한 manifest의 실제 조회·해시 검증을 매번 유지하고, 성공 본문만 해시/권한별 한 벌로 공유하는 후보다. 실행별 stdout 경로와 작은 receipt에 실제 조회 시각·snapshot·본문 해시를 유지한다. 실패·불일치·중단 출력과 전체 읽기 검사는 그대로다. 30분 주기·정책·운영 설치본은 변경하지 않았다. 현재 `/opt`의 T05 후처리를 포함한 archiver 원본에 좁은 패치를 적용한 별도 후보도 보존하며, 배포와 timer 재개는 별도 검토 대상이다.

[재발 방지 후보 구현/검증 결과](candidate-review.md)를 별도로 고정했다. **T18 전체 완료는 아니다.** 과거 공백의 남은 보강 검토·재발 방지 배포·archiver 재개 및 미보관 사본 처리·실제 출력 증가 확인이 남아 있다. 일반 로그 기간/총량 제한과 과거 전량 조회 빈도 변경도 별도 후속이다.
