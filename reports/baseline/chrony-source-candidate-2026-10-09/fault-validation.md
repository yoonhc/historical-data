# Chrony 4.5 무응답과 간헐 응답 격리 검증

**다른 세 시간원이 정상 응답하더라도 600초 안의 reference 갱신은 보장되지 않았다.**
이를 자동 전환 검사 통과로 처리하지 않는다. 기준 완화·`prefer`·`reselect`·추가 강제 burst는 사용하지 않았다.

## 시험 범위

실제 설치된 Chrony 4.5 바이너리와 같은 SHA-256
`7a834e478d8a904c39a348606a5d0ac58fd1ccbca24f333a8170c786af8ca508`의 별도 실행 파일을 사용했다.
rootless user/network namespace 안에서 `chronyd -x -U`로 실행하고 capabilities를 모두 제거했다.
[namespace 기록](fault-observation-attempt-3/namespace.json)은 host/test 네트워크 namespace 차이와
namespace UID 0 → host UID 1000 매핑을 보여 준다. 두 daemon의 `CapEff=0`도 기록했다.
운영 시각·네트워크·시간원 트래픽은 바꾸지 않았다.

합성 NTP 서버 네 개를 TEST-NET 주소에 만들었다. 같은 호스트 시각을 기준으로 응답하므로
이것은 UTC 정확도 인증이나 실제 네 시간원의 독립성 시험이 아니다.
초기에 자연스럽게 선택되는 서버의 선언 root delay를 2ms, 다른 세 서버를 40·50·60ms로 설정했다.
이 값은 선택 알고리즘에 주는 고정 시험 입력이며 현 사건의 실측 네트워크 지연을 재현한 것이 아니다.
전체 구성은 각 시험의 `chrony.conf`, 응답·누락은 `source-*.jsonl`에 보존했다.

`iburst minpoll 6 maxpoll 8`을 유지하고 12분 정상 수집으로 시작했다.
선택과 기존 준비 조건 통과를 확인한 뒤 30분 동안 선택 시간원만 차단했다.
완전 무응답은 모든 후속 응답을 버리고, 간헐 응답은 요청 네 개를 버린 뒤 두 개에 응답하는 순환이다.
두 시나리오를 동시에 실행했고 5초마다 실제 tracking/sources/selectdata/ntpdata를 기록했다.

준비 판정은 수정하지 않은 운영 `assess_clock()`과 실제 Chrony tracking 응답으로 계산했다.
전체 운영 `probe()`는 `-x`를 거부하므로 **실제 운영 provider gate 통과를 주장하지 않는다.**
관측 시각은 KST이며 5초 샘플 간의 정확한 전환 순간까지 측정한 것은 아니다.

## 완전 무응답

| 단계 | 실제 결과 |
|---|---|
| 장애 전 마지막 유효 reference | 10:54:32.868, `192.0.2.11` |
| 장애 시작 관측 | 10:54:34.431 |
| 처음 600초 초과로 거부한 관측 | 11:04:34.431, age 601.579초 |
| 다른 시간원 선택 | 30분 장애 창 안에서 관측하지 못함 |
| 새 reference 갱신 | 장애 시작 이후 관측하지 못함 |
| 거부 이후 준비 회복 | 관측하지 못함 |
| 마지막 관측 | 11:24:29.431, age 1,796.569초, ready=false |

선택 서버는 장애 이후 17회 요청을 받아 모두 버렸다. 나머지 서버의 전체 시험 요청/응답 수는
31/31, 28/28, 27/27이다. 마지막에도 선택 서버는 `*`였고 다른 세 곳은 `selectdata`의 `D`였다.
이는 세 보조 서버가 응답하지 않았다는 뜻이 아니다. Chrony 문서에서 `D`는 다른 시간원과
결합하기에 root distance가 크거나 최근에 컸던 상태다.
[Chrony 4.5 selectdata](https://chrony-project.org/doc/4.5/chronyc.html#selectdata).

근거: [원본 기록](fault-observation-attempt-3/silent/observations.jsonl),
[시험 결과](fault-observation-attempt-3/silent/result.json).

## 간헐 응답

| 단계 | 실제 결과 |
|---|---|
| 장애 전 마지막 유효 reference | 10:54:31.310, `192.0.2.21` |
| 장애 시작 관측 | 10:54:34 부근, 기존 선택 `192.0.2.21` |
| 장애 후 첫 새 reference 관측 | 10:59:59.431, 같은 서버의 reference 10:59:56.485 |
| 다른 시간원 선택 | 30분 장애 창 안에서 관측하지 못함 |
| 처음 600초 초과로 거부한 관측 | 11:11:04.431, age 603.358초 |
| 그 거부 이후 첫 새 reference·준비 회복 | 11:11:54.431, 같은 서버의 reference 11:11:49.769, ready=true |
| 관측 최대 reference age | 648.354초 |
| 마지막 관측 | 11:24:29.431, age 630.216초, ready=false로 재발 |

선택 서버는 장애 창의 요청 15개 중 11개를 버리고 4개에 응답했다.
다른 세 서버는 전체 시험에서 각각 28/28, 37/37, 27/27 요청에 응답했다.
회복은 다른 서버로의 전환이 아니라 원래 서버의 새 응답에 의한 reference 갱신이었다.

근거: [원본 기록](fault-observation-attempt-3/intermittent/observations.jsonl),
[시험 결과](fault-observation-attempt-3/intermittent/result.json).
마지막 유효 reference와 모든 선택·갱신·거부·회복 관측은
[기계 판독 집계](observation-analysis.json)의 `fault.intermittent` 항목에 있다.

## 해석과 초기 시험 실패

이번 결과는 설치 버전·고정된 입력·관측 창에 대한 결과다.
무응답에서 영원히 전환하지 않는다고 일반화하거나 두 시험을 실제 10/9 사건의 재현이라고 부르지 않는다.
요청 주기를 줄이거나 시간원의 우선순위를 강제하면 나아지는지 이번 범위에서 검증하지 않았다.
운영 600초 기준도 그대로 유지했다.

초기 namespace 시험에서는 권한이 없는 `/proc/1/ns/net` 조회, 그다음 sandbox의 netlink 제한으로 진행하지 못했다.
launcher가 진입 전 자신의 namespace ID를 전달하도록 하고, 승인된 sandbox 밖 실행에서
동일한 rootless 격리를 사용해 완료했다. 운영 root 권한이나 시각 제어 권한을 추가하지 않았다.
완료된 두 시험의 daemon 종료 코드는 0이며 rootless namespace는 시험 프로세스 종료로 사라졌다.
