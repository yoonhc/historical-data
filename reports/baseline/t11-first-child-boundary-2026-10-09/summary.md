# T11 첫 실제 child 경계 관찰

**2026-10-09 21:59 KST에 저장된 실제 기록을 대조해 완료했다.** 두 스트림 모두 같은 supervisor가
첫 child의 종료·대기·다음 시작을 처리했다. 다음 시작 당시 자연스럽게 `reference_stale=true`였고,
다른 시각 조건을 통과하여 수집을 시작했다. 장애 주입이나 시간원 변경은 하지 않았다.

대상은 운영 중인 `clock-evidence-policy-20261009-65296619a374c325`다.
[19:33 적용·파서 결함과 재개·초기 관찰](../clock-runtime-deploy-2026-10-09/summary.md)은 별도 원기록으로 보존한다.
이번 확인은 과거의 첫 경계를 읽은 것이며, 요청 이후 한 시간을 새로 기다리거나 서비스를 재시작한 시험이 아니다.

## 첫 종료와 다음 시작

아래 시각은 KST다. 종료 보고서의 경계, supervisor의 child 종료 확인, 다음 시작 결정을 구분했다.

| 항목 | Depth | aggTrade |
|---|---|---|
| 첫 collection | `d2d18424592f4f549f5bda828e270759` | `c5b36646466344d5b1672275ffb2faf8` |
| 첫 collection 시작 | 19:33:48.230 | 19:33:48.064 |
| 최종 collection 경계 | 20:33:48.295 | 20:33:53.393 |
| supervisor가 child 종료 확인 | 20:33:48.424 | 20:33:53.478 |
| 최종 상태·종료 코드 | `partial`, 2 | `complete`, 0 |
| 첫 child PID → 다음 PID | 224866 → 238899 | 224855 → 238922 |
| 다음 시작 결정 | 20:34:03.628 | 20:34:08.706 |
| 다음 collection 시작 | 20:34:03.921 | 20:34:08.947 |
| 다음 collection | `353238d9baf4406aa072eee7b1e5b4ea` | `558440612aa14970be0046d783997b89` |
| 종료 확인 → 다음 시작 결정 | 15.204초 | 15.227초 |
| supervisor PID | 224785 유지 | 224784 유지 |

boot·supervisor run·PID·start ticks·systemd invocation·프로필 해시가 각 스트림 안에서 유지됐다.
다음 child는 새 child ID와 collection ID를 사용했다. 첫 Depth의 exit 2는 **같은 child의 저장된
terminal partial**로 확인되어 다음 실행으로 이어졌고, `terminal_partial_confirmed=true`가 남았다.

시각 raw artifact는 각 16개, 총 32개의 해시·압축 해제 해시·chunk 연결·record 순서를 확인했다.
처음 준비부터 다음 child 시작 후 10초까지 대조한 표본은 Depth 3,584개, aggTrade 3,589개다.
경계 표본의 최대 간격은 각각 **1.028초·1.029초**였고, `between_sessions`에도 관측이 이어졌다.
이는 표본 기반 연속 감시의 근거이며 메시지별 UTC 정확도 인증이나 무중단 시장 수신을 뜻하지 않는다.

근거: [Depth 경계 원기록과 artifact 목록](depth-boundary.json),
[aggTrade 경계 원기록과 artifact 목록](aggtrade-boundary.json), [종합 결과](result.json).

## reference 지연 단독 사유의 실제 허용

| 다음 시작 직전 판정 | Depth | aggTrade |
|---|---:|---:|
| reference age | 1,626.457초 | 1,631.534초 |
| `reference_stale` | true | true |
| 현재 조건부 오차 추정 | 127.089ms | 127.096ms |
| 남은 보정량의 절댓값 | 0.791ms | 0.791ms |
| `current_estimate_usable` / `raw_start_allowed` | true / true | true / true |
| 엄격한 provider 검사 | `ready=false`, 사유 `reference_age`만 | 동일 |
| 누적 `hard_fault_seen` | false | false |

따라서 이번에는 격리 시험뿐 아니라 **실제 reference 지연 상태의 다음 child 시작**까지 확인했다.
기준 측정이 오래됐다는 경고와 과거 `reference_stale_seen`·기존 `clock_usable=false` 이력은 지워지지 않았다.
시간원 장애 원인이나 절대 UTC 오차를 이 결과로 추가 확정하지 않는다.

## 최종 저장과 시장 품질은 별도 판정

첫 Depth는 수신/저장 증분 35,228개와 스냅샷·대조 행 360개, 카탈로그 합계 **35,588행**이다.
20:32:11.523에 sequence `34626`, epoch 3의 `coverage_exhausted` 1건을 기록했고,
마지막 저장 증분은 epoch 4의 sequence `35587`이다. 종료 시 `partial`, `complete=false`, 손실 1건을 유지했다.
다음 collection의 정상 종료가 이전 손실을 정상으로 바꾸지는 않았다. 이 새 사건의 독립 원인 분석은 하지 않았으며 T07에 남긴다.

첫 aggTrade는 수신/저장 **45,096행**이다. 다음 세션의 경계는 다음처럼 저장돼 있다.

```text
이전 WS 마지막   3480371060
자동 REST 보강   3480371061–3480372326  (1,266개, 2페이지)
다음 WS 처음     3480372327
```

보강 collection `4ebe36bb51c14af68d20a3462b7ada07`은 `complete`이며,
두 표준 파일 전체 1,266행의 ID 연속성과 경계·중복 없음, 원문/표준 해시를 대조했다.
이번 점검은 이미 저장된 보강분을 읽었으며 추가 REST 요청을 보내지 않았다.
이전 WS 마지막과 다음 WS 첫 수신 시각 차이는 약 21.163초다. REST에 과거 WS 수신 시각을 만들어 넣지 않는다.
Depth는 마지막 증분과 다음 anchor 사이 약 16.100초의 수신 기록 간격이 있고, 그 사이 호가 증분을 복구했다는 뜻은 아니다.

시장 파일은 첫/다음 collection의 양 끝과 실패 증분을 담은 **11개 segment**를 골라 해시·행 경계를 읽었다.
첫 두 collection 전체 시장 데이터의 독립 재생/전수 무결성 검사를 새로 수행한 것은 아니다.
카탈로그 전체 행 수, 선택 파일 검사, 보강분 전체 검사를 구분한다. [파일별 근거](market-boundary-files.json).

## 종료 collection의 시각 근거와 다음 백업 검증 대상

운영에 설치된 CLI를 별도 프로세스로 실행해 첫 collection 두 개를 조회했다.

| 조회 결과 | Depth | aggTrade |
|---|---:|---:|
| CLI 종료 코드 | 0 | 0 |
| `evidence_state` / `unknown_reasons` | `available` / 없음 | `available` / 없음 |
| 시작부터 종료까지 연결된 관측 구간 | 3,553 | 3,558 |
| reference 지연을 포함한 구간 | 1,950 | 1,955 |
| 현재 추정 사용 불가 / 원문 시작 거부 구간 | 0 / 0 | 0 / 0 |
| 조회에 사용한 artifact | 14개 | 14개 |

열린 collection 때문에 발생했던 `collection_end_unknown`은 종료·근거 등록 뒤 해소됐다.
`available`은 근거가 존재한다는 뜻이며, Depth의 partial이나 신선도 경고를 정상 품질로 바꾸지 않는다.
구간 수는 장애 횟수가 아니다.

[Depth 조회](depth-clock-query.json), [aggTrade 조회](aggtrade-clock-query.json),
[다음 백업 대조 대상](backup-followup.json)에 같은 collection·종료 경계·artifact ID·해시·조회 결과를 고정했다.
T04는 **10/10 13:10 예약 checkpoint → 같은 backup ID의 restic snapshot → 전체 읽기 검사 →
명시적 snapshot의 별도 경로 복원 → 같은 collection 조회 대조**가 남아 있다.
15:00은 점검 예정 시각이며 실제 완료 기록으로 판단한다. 이번 로컬 조회를 운영 백업·복원 완료로 세지 않는다.

## 운영 상태 보존과 확인 한계

21:59 호스트 조회에서 두 supervisor와 Chrony의 PID·invocation은 초기 관찰과 같았다.
UID 1000, 고정 배포 경로, `RuntimeMax=infinity`, `Restart=always`, `KillMode=mixed`,
SIGINT·종료 제한 330초를 확인했다. 승인한 설정/유닛 해시가 일치하고,
ledger는 초기 운영 관찰과 동일한 `closed`·step 0회·기존 부팅 증거를 보존했다.
Chrony 설정·`/etc/adjtime` 해시와 이전 사용자 수집·자동 정리의 mask도 유지됐다.
[호스트 원기록](host-state.json), [배포본·보존 대조](verification.json).

관찰 도구 준비 중 문법 오류와 Depth 합계에 스냅샷 행을 더하지 않은 검사 가정을 수정했다.
샌드박스에서는 실제 PID가 보이지 않아 호스트 읽기 권한으로 프로세스 조회를 수행했다.
운영 collector·설정·시각·서비스에는 변경이 없으며 최종 관찰 도구는 [observe.py](observe.py)에 보존한다.
원문 artifact 사본과 전체 CLI/카탈로그 출력은 Git 제외 경로 `reports/t11-first-child-boundary-2026-10-09/`에,
대조 결과와 정확한 원문 ID/해시는 이 baseline에 남겼다.

T11의 이번 범위는 완료다. supervisor 자체 재시작은 여전히 엄격한 준비를 요구하고,
다른 8개 예약 업무의 gate·15초 child 간격·기존 v1 로그 편입·T05 자동 정리 검사는 별도다.
T02의 첫 24시간 관찰과 T04의 새 근거 백업/복원도 이번 결과로 완료 처리하지 않는다.
