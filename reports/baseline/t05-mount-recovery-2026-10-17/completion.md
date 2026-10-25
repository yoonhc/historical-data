# T05 첫 수동 정리 완료

승인한 원래 checkpoint 10개를 **10/17 20:32:33 KST**까지 정리했고, root 종료 처리는 20:32:36에 완료했다.
실제 호스트의 후속 읽기 전용 대조에서도 원본 run·journal·경로·보호 입력·승인 비활성 상태가 일치했다.
**첫 수동 적용과 결과 확인 완료**이며 반복 자동화·T05 전체 완료는 아직 아니다.

## 정확한 실행과 결과

| 항목 | 확인 결과 |
|---|---|
| 재개 operation | `821367d24e43bd2423da568dfbca8cab47ac9c40fe405f09feadea567bec4824` |
| 원래 삭제 plan | `31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08` |
| 실제 run | `f57f334be39e4137a4b7785836b6d2bd` |
| worker | 시스템 manager, UID 1000, PID `2614637`, invocation `832b2920c8c84a4a81625c5ee410879b` |
| 실행 시간 | 19:58:42.298–20:32:33.634 KST, **33분 51.335초** |
| 결과 | `complete`, exit 0, 이번 삭제 10개, 이전 완료 0개, `already_complete=false` |
| 실제 경로 | 승인한 원본 10개·각 staging 대상 모두 없음, 계획별 staging 부모는 비어 있음 |
| 남은 사본 | 최신 보호 3개(10/15·10/16·10/17) + 등록 이전 제외 3개(9/25·9/26), 합계 6개 |
| 파일 제거 | journal의 `unlinked_files` 합계 **558,143개** |
| 여유 공간 | 관측상 **28,197,318,656 bytes 증가**, 약 28.20GB. 동시 작업을 포함하는 관측값이며 정리만의 정확한 회수량은 아님 |
| 구성 변경 | 이번 재개에서 0개, daemon-reload 없음 |

[root 종료 원본](completion-evidence/root-result.json), [업무 run](completion-evidence/run.json),
[완료 journal](completion-evidence/journal.json), [systemd 종료 로그](completion-evidence/manager-journal.jsonl).
서비스 현재 조회에서는 종료 후 invocation이 비어 있지만, run에 저장된 시작 job/PID/invocation과
manager journal의 정상 종료를 연결했다. 자동 timer 실행으로 분류하지 않는다.

## 구성 수정과 재개 승인의 연결

`operation_plan_id`는 구성 변경·서비스 요청을 수행할 작업의 식별자이고,
`retirement_plan_id`는 삭제할 정확한 사본 집합의 식별자다. 다음 두 operation은 같은 삭제 plan을 사용한다.

| 단계 | 승인·실행 근거 | 실제 결과 |
|---|---|---|
| mount 수정 승인 | operation `eced87a4…`, [계획](plan.json)·[사용자 승인 기록](approval-received.json) | canonical 수동/자동 유닛, 실제 수동 template, 비활성 recurring의 네 파일 변경과 같은 계획 한 번 재개 승인 |
| 구성 적용 | 10/17 **18:47 KST**, [root 결과](postapply-busy-evidence/root-result.json)·[18:52 대조](postapply-busy-audit.json) | 네 파일은 after로 적용됨. 이어 요청한 run `2d9f7b8b…`는 잠금 경합으로 실패·삭제 0개. 전체 결과의 `passed=false`는 구성 원복을 뜻하지 않음 |
| 재개 전용 계획·승인 | operation `821367d2…`, [계획](resume-only-plan.json)의 `based_on_operation=eced87a4…`, [별도 사용자 승인 기록](resume-approval-received.json) | 이미 적용된 after를 유지하며 Busy run `2d9f7b8b…`를 검토하고 같은 삭제 plan을 한 번 재개 |
| 실제 재개·완료 | [root 결과](completion-evidence/root-result.json) → [run `f57f334b…`](completion-evidence/run.json) | 원래 plan `31766af4…`의 10개 완료. 구성 변경 0개·daemon-reload 없음 |

네 구성 경로는 `/etc/marketdata/checkpoint-clock-retirement/units/`의 수동·자동 service,
`/etc/systemd/system/marketdata-checkpoint-retirement-apply@.service`,
`/etc/marketdata/checkpoint-clock-retirement/recurring.json`이다.
구성 수정과 실제 삭제가 서로 다른 시도에서 완료됐으므로 두 operation의 ID와 변경 개수가 다르다.
이 연결은 [기존 기록의 ID·해시 대조](../t05-recurring-preparation-2026-10-18/approval-chain.json)로 확인했다.
원래 plan·journal·실패 기록·동결된 승인 문서는 수정하지 않았다.

## 보존 조건과 실패 이력

원래 plan 파일 SHA-256 `c928892ace0bb7ddd5cc4751467408d9bd8b10c33ebaeee74194a5e40cd2a25d`와
inventory 10개의 기존 해시가 유지됐다. 원래 EXDEV 실패 시도는 바이트에 해당하는 객체 내용 그대로 남았고,
동일 journal에 성공 시도 1개만 추가됐다. journal 전체 해시는 진행에 따라
`596bece55e5b4a36fdb357f6733d5cb0dab0297310eae9cf91585c1805994502`로 바뀌었다.
과거 적용 run 4개도 원래 해시를 유지하며 새 성공 run 1개만 추가됐다.

보호 3개와 제외 3개의 실제 경로·manifest 해시를 대조했다. 유지 사본의 전체 바이트 검사는
설치된 `retire_checkpoints.py:575`의 순서대로 잠금 안에서 삭제 전에 수행된다.
이번 후속 감사는 그 성공 실행과 고정 코드·현재 manifest를 연결하며, 같은 전체 자료 검사를 다시 실행하지 않았다.
원래 inventory를 덮어쓰거나 사본 목록·완료 상한·생성 순서를 재등록하지 않았다.

당일 backup `9b916470eca7e5da1462dff9c39380869242bf6711861eaea754c010ccf00963`와
snapshot `0911b25d3a270fad604eeb222748e950ea45383b68d9b451e6d785b92672c58f`의
저장·전체 검사 완료 근거는 실제 run의 `daily_evidence`에 연결됐다.
이번 업무가 실행한 archive 명령은 `dump` 112회·`cat` 1회·`snapshots` 1회로 모두 성공했으며,
snapshot 삭제나 새로운 전체 검사를 실행한 것으로 표시하지 않는다.

시각 관측 204건은 모두 유효했고 U는 **0.132003062–0.230851261초**였다.
기존 U≤5초, 보수적인 72시간+60초, 후행 검증 사본 3개, 실제 변경 전 시각 재확인·감시 조건을 유지했다.
새 정책이나 시각 보정으로 통과시킨 작업이 아니다.

## 현재 운영 상태와 감사 범위

- `manual.json`: 원래 비활성 바이트 `fe599e7332281a1a4b601833912c67606b3dd877d3ad274fcb764143b077be4e`, root:root/0644로 복원.
- `recurring.json`: 현재 after scope `84a0b92ae6f8bf0e38ac10ecb4a2615a32af72aebf1c8c28d591c02087cf4983`, `enabled=false`, 승인 필드 null.
- 사용자·시스템 자동 정리 service/timer **네 mask 유지**.
- 네 설치 파일의 after 바이트/권한·UID 1000 guard, 88개 보호 입력·기존 근거 15개 유지.
- Chrony 및 두 수집 supervisor의 PID/invocation 유지. 수집·Chrony·예약 제어를 추가로 하지 않았다.

[독립 감사 결과](completion-audit.json)는 10/17 22:51:12 KST 실제 호스트에서 확인했으며,
운영 변경 없이 작은 기록 사본과 결과만 이 보고서 폴더에 저장했다.
감사 원본 도구는 [audit_completion.py](audit_completion.py), native 실행 chunk는 `e7e614`, exit 0이다.
각 입력의 원본 경로·해시·크기는 감사 결과의 `source_evidence`에 있다.

## 남은 범위

이 계획의 apply·resume 명령은 완료됐으므로 다시 실행하지 않는다.
반복 자동화는 현재 구성에 맞는 자동 service 설치 형식·guard·timer 실행 출처와 승인 범위를 검토한 뒤
**별도 반복 승인과 시스템 timer 활성화**로 진행한다. 기존 사용자 자동 유닛은 계속 차단한다.
고정 renderer의 공통 부모 mount 반영은 다음 유닛 재생성/새 배포의 후속이며,
현재 /opt 소스를 임의로 수정하거나 옛 renderer로 설치 구성을 덮어쓰지 않는다.
성능 계측 T14와 수동/자동의 당일 조건 분리는 기존 후속 범위를 유지한다.

10/18 [자동 경로 준비 검토](../t05-recurring-preparation-2026-10-18/review.md)에서
현재 scope·canonical mount·비활성 상태를 확인했다. 적용 진입 후 잠금 경합의 다음 예약 처리에는
합의와 설치 코드 사이의 차이가 있어, 반복 승인안을 고정하기 전에 좁게 보완할 항목으로 남겼다.
