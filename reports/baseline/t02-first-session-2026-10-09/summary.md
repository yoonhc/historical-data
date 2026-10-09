# T02 첫 세션 종료와 다음 세션 시작 확인

**10/9 00:11에 시작한 두 수집기의 첫 종료·저장·다음 시작을 확인했다.**
이번 조회는 10/9 **21:10:51 KST**에 마쳤다. 첫 24시간 관찰은 아직 끝나지 않았으며
**10/10 00:20 KST 이후** 실제 기록으로 확인한다.

[결과](result.json), [당시 collection·segment 목록](collections.json),
[파일 검사](stored-files.json), [세션 경계와 과거 이력](boundary-and-history.json),
[호가 재생](depth-replay.json), [시각 관측](clock-observation.json),
[systemd 시작·종료 원문](service-boundary-journal.json)을 보존했다.

## 첫 종료와 다음 시작

| 항목 KST | depth | aggTrade |
|---|---|---|
| 첫 collection | `bc6bc9d2cb6149ba864f0d2003adf3af` | `c36431bac84d4092b5f1ca33b4740af4` |
| 첫 수신 | 00:11:19.299452 | 00:11:23.302467 |
| 마지막 수신 | 01:11:18.998871 | 01:11:23.045122 |
| 최종 보고서 저장 | 01:11:19.048173 | 01:11:28.077075 |
| 수신·저장 메시지 | 33,175 / 33,175 | 339,991 / 339,991 |
| 최종 품질·종료 코드 | partial, complete=false, exit 2 | partial, complete=false, exit 2 |
| 다음 collection | `f8cddef376fe42a9af10c052b254a375` | `bd9cf50dffeb4322a6f63d0f6e9a45a2` |
| 다음 첫 수신 | 01:11:36.080818 | 01:11:45.193979 |

당시 고정 실행본은 `system-bootstrap-20261007-f85eb8321a19c1ee`다.
첫 supervisor/child는 depth `2237/2289`, aggTrade `2239/2290`이며,
다음 실행은 각각 `19220/19230`, `19253/19262`다. invocation도 바뀌었다.
systemd가 exit 2를 실패로 기록한 뒤 약 15초 후 예약 재시작한 원문과 새 수집 파일이 일치한다.
이 코드의 exit 2는 저장된 partial과 대응하며 인자 오류라고 해석하지 않는다.

이는 **이전 runtime의 한 시간 종료·systemd 재시작** 확인이다.
19:33 적용한 T11의 같은 supervisor 아래 다음 child 전환을 확인한 결과로 대신하지 않는다.
두 WS 수신 경계 차이는 약 17.08초·22.15초다. 실제 호가 재구성 완료 시간이나 무손실 구간이라는 의미는 아니다.

## 저장과 품질 이력

첫 두 collection의 canonical **1,960개 파일·373,623행** 전체를 읽어 해시·건수·sequence/ID 연속성과
수신 시각 역행을 검사했다. 불일치·내부 ID/sequence 누락·수신 시각 역행은 없었다.
호가 33,632행은 증분 33,175 + anchor 5 + check 452다.
다음 collection은 처음/마지막 각 2개 segment만 읽었다. 다음 세션 전체 파일 재검사는 아니다.
선택한 원문 파일과 실패 sequence 5580 근처 원문 해시도 대조했다.

호가는 당시 설치본의 재생 함수로 첫 collection 33,632행을 재생했다.
`replay_matches_live=true`, **`valid=false`**, `continuous=false`였다.
완료된 REST 대조 450개는 일치했고 대기 중 대조 2개가 남아 `verification_complete=false`다.
이 결과를 호가의 모든 구간이 정상이라는 판정으로 바꾸지 않는다.

00:20:43의 `coverage_exhausted`, sequence 5580, `sync_losses=1`과 `failure_notice`가 최종 보고서에 남았다.
notice는 초기 후속 관측의 값과 동일하다. 실패한 epoch의 이전 Book과 오류가 재생에서도 일치했고,
이후 epoch에서는 동기화·top 20을 회복했다. 마지막 epoch는 `duration`으로 끝났지만 과거 손실은 유지됐다.
당시 실패 공개의 실제 외부 조회 지연이나 범위 소진의 근본 원인을 새로 측정·독립 계산한 검사는 아니다.

aggTrade의 첫 세션은 전환 당시 미복구 264,069개 ID를 이유로 partial이었다.
오전 별도 보강과 백업 편입은 이미 T01·T03에서 완료했지만, 이 과거 보고서의
`unrepaired_gaps=1`·기존 20페이지 제한 실패를 정상으로 덮어쓰지 않았음을 확인했다.

첫 세션과 다음 세션 사이에는 다음 연결이 확인됐다.

```text
직전 WS 마지막 ID 3479490809
  → 자동 REST 보강 3479490810–3479491585, 776개, 1페이지
  → 다음 WS 첫 ID 3479491586
```

776개 보강분도 원문·canonical 해시와 전체 ID를 읽어 확인했다.
해당 REST 수신 시각은 나중에 조회한 시각이며 당시 WS 수신 시각으로 해석하지 않는다.
검사 도구의 초기 출력에 이 REST 항목의 source/수신 필드가 websocket으로 표시돼 명칭을 바로잡고
해당 776행만 다시 확인했다. 실제 데이터·ID·해시·호가 재생 결과는 바뀌지 않았다.

## 시각 관측과 남은 범위

첫 세션과 바로 다음 세션의 실제 child가 있는 v1 로그 4개를 확인했다.
모두 `finished`, `session_clock_clean=true`, `violations=0`이며 실행 중 unusable 표본은 0이었다.
관측 최대 reference age는 약 258.36초, 남은 보정량 절댓값은 약 19.70ms,
조건부 오차 상한은 약 164.85ms다. 표본 사이와 모든 메시지의 UTC 정확도를 인증하는 값은 아니다.
준비 검사만 실행한 ready-only 로그를 추가 수집기로 세지 않았다.

종료 표본의 `clock_usable=false`는 이전 runtime이 `finished`에 고정해서 쓰는 값이다.
실행 구간의 시각 품질은 별도 `session_clock_clean`·위반 기록으로 확인했다.
v1 로그의 원본 해시와 선택한 원문 관측을 이 보고서에 남겼지만,
이 작업이 전체 v1 로그의 자동 백업·collection 연결을 구현한 것은 아니다.

카탈로그는 `mode=ro`·`query_only`로 읽었고 파일은 기존 공유 layout 잠금을 사용했다.
운영 수집·시각·설정을 변경하거나 추가 거래소 요청을 하지 않았다.
조회 전후 boot ID·네 설정 파일의 해시·자동 정리 service/timer mask가 동일했다.

T02에는 **첫 24시간 실제 관찰만 남는다**. 해당 기간 중 19:33에 수집 runtime이 교체됐으므로,
이전/이후 정책·예정된 전환 공백을 나누어 확인하고 한 버전의 24시간 무중단 검증이라고 표현하지 않는다.
T11의 신규 supervisor 경계와 T04의 새 시각 근거 백업·복원은 각각 별도 후속이다.
