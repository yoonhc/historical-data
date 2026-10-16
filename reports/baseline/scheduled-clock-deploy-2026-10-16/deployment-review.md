# T16 고정 운영 적용·재개·원복 검토안

**10/16 별도 승인한 아래 계획을 22:23:51 KST 운영 적용했다.** 초기 관찰에서 OI 품질 결과의 Decimal 기록 오류를 발견했고 요청은 pending으로 보존돼 있다. [실제 적용·현재 결함](status.md)을 먼저 읽는다. 원래 계획의 승인·해시·원복 절차를 보존하며 별도 보완 소스의 적용 승인으로 확대하지 않는다.

- 후보: `scheduled-clock-policy-20261016-544c61aca49f1355`
- 인계 도구: `scheduled-clock-handoff-20261016-778dca2b72c6e867`
- 운영 계획: `7eccd61f65c66fd5f3d70f8893e23aa01abe246caa719256e96e004d59753a7a`
- 같은 boot: `eb163bf2-6642-4336-b4d6-6cd58771b13d`

## 정확한 변경 범위

[host-plan.json](host-plan.json)의 `inner.operations`가 최종 13개 파일의 내용·모드·before/after 해시다. 기존 8개 system service와 topology/bootstrap guard/T05 binding을 교체하고 새 업무 waiter·binding을 추가한다. 모두 root 소유 0644다.

| 경로/행위 | 범위 |
|---|---|
| `/opt/marketdata/releases/scheduled-clock-policy-20261016-544c61aca49f1355` | 관리자 시험에서 사용한 3개 variant·정책·가상환경의 고정 파일을 오프라인 설치. resolver/패키지 갱신 없음 |
| `/opt/marketdata/tools/scheduled-clock-handoff-20261016-778dca2b72c6e867` | 고정 실행기/입력 사본 설치 후 이 root 소유 경로에서 실행 |
| `/etc/systemd/system/marketdata-{oi,oi-probe,oi-quality,daily,pack,backup,restic-archive,network-observer}.service` | 8개 기존 업무의 새 wrapper·전용 waiter 연결. UID 1000·보호/종료 옵션 유지 |
| `/etc/systemd/system/marketdata-jobs-clock-ready.service` | 8개 업무 전용 새 준비 waiter |
| `/etc/marketdata/scheduled-system/topology.json` | 새 실효 명령/의존 관계 명세 |
| `/etc/marketdata/clock-bootstrap.json` | guard 해시만 연결. 부팅 초기 정책·관리 목록·기존 보정 권한 유지 |
| `/etc/marketdata/scheduled-clock-policy/clock-binding.json` | 새 업무와 bootstrap 구성 연결 |
| `/etc/marketdata/checkpoint-clock-retirement/clock-binding.json` | 현재 bootstrap 해시만 연결. 완료 상한·baseline·순서 재등록 없음 |
| `/var/lib/marketdata-clock/boot-state.json` | pending 중 기존 closed 증거를 보존하고 commit 시 profile 식별자/구성 이력만 추가 |
| root 실행 기록 | `scheduled-policy-handoffs/<운영 계획 ID>/`, `scheduled-transitions/<내부 계획 ID>/` 및 별도 `scheduled-policy-handoff.lock` |
| manager 명령 | 8개 system timer stop/start, quiescent 상태의 8개 service 임시 mask/reload, 확인한 no-PID 대기 요청만 stop, 그 요청만 start, 새 waiter의 필요시 stop |

8개 timer 내용·enable 링크·기존 사용자 16개 mask·기존 strict waiter·수집기/Chrony 관련 파일·설정은 보호 대상이다. **기존 strict waiter의 stop 명령도 허용하지 않는다.** 수집기/Chrony/RTC/polling 변경·checkpoint 삭제·자동 정리 mask 해제는 범위에 없다.

`payload.json`은 관리자 시험의 파일 목록 그대로다. 최종 release에는 그중 `candidate/` 아래 객체를 같은 상대 구조로 설치한다. 실행 전 source/대상 내용·소유권·모드를 대조하며 기존 파일의 불명확한 손상을 덮어 고치지 않는다. 최종 경로의 Python과 import를 UID 1000으로 확인한 뒤에만 예약 보류를 시작한다. 최종 경로 설치 실행 자체는 운영 승인 이후다.

## 적용 및 같은 계획 재개

아래 명령은 **별도 운영 승인 이후에만** 실행한다. `--action inspect`는 파일/계획 검사이고 운영 작업을 수행하지 않는다.

```sh
/usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/scheduled-clock-deploy-2026-10-16/handoff.py --action inspect
```

승인한 적용 명령:

```sh
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/scheduled-clock-deploy-2026-10-16/handoff.py --action apply --approve-plan 7eccd61f65c66fd5f3d70f8893e23aa01abe246caa719256e96e004d59753a7a
```

1. 같은 boot·고정 도구/후보/업무 설정·보호 파일/소유권을 확인한다. root 소유 도구로 재실행한다. 별도 handoff lock이 중복 인계를 막는다.
2. 최종 release를 설치하고 세 Python 환경의 UID·prefix·모듈 경로/해시를 확인한다. 이 단계 실패 때 기존 예약은 유지된다.
3. timer의 새 기동만 보류한다. `MainPID`, `ControlPID`, 실제 job과 재시작 상태로 본문 실행·no-PID 대기·불명확 상태를 구분한다. **기존 service 파일·closed ledger를 유지하고 coordinator EX 없이 최대 600초 정상 종료를 기다린다.** 시간이 지나면 보류하며 본문은 종료시키지 않는다. 재실행 시 이어서 확인하거나 아래 원복으로 timer를 복구한다.
4. 짧은 coordinator EX 안에서 다시 quiescence/T05 유휴를 확인하고, 기존 request.lock 아래 요청 identity/pins/원문 해시를 읽는다. 첫 적용에 미완료 request가 있으면 진행하지 않는다. 현재 조회에서 0개였다는 사실을 미래 적용에 대신 사용하지 않는다.
5. 원래 no-PID 대기를 기록하고 pending을 확정한 뒤 service 임시 mask를 설치한다. 중간 재시작은 guard에서 거부된다. mask reload로 이미 사라진 대기도 cutoff 기록에서 유지한다. PID가 있는 실행은 stop하지 않는다. 새 waiter만 필요시 정리한다.
6. pending 아래 임시 mask 파일을 되돌리고 기존 transaction의 13개 stage → daemon-reload → 정확한 실효 명령/보호 옵션/소유권 검사 → commit을 수행한다. 모든 임시 mask가 해제되기 전에 manager에는 mask가 계속 로드되어 있고, 그 뒤에는 pending이 시작을 막는다.
7. coordinator EX 밖에서 새 정책의 읽기 전용 준비 상태를 확인한다. 명시적 시각 보류는 `ready=false`로 기록하며 재시도 waiter가 담당한다. 알 수 없는 설정/프로그램 오류를 시각 보류로 바꾸지 않는다.
8. 인계가 취소한 요청을 한 번만 발행한 뒤 원래 timer를 시작한다. 각 명령 전/후 기록을 남긴다. 타이머의 catch-up은 기존 업무 원장/탐색 계약을 따른다. 임의의 전체 daily/backup 추가 실행은 하지 않는다.

중단 뒤 **동일 명령**은 저장된 phase·ledger·파일과 시작 기록을 대조한다. 이미 완료한 시작 요청을 반복하지 않는다. 명령 반환 직후 중단은 새 invocation/기동 시점이 확인되는 경우만 자동 인정한다. 발행 여부가 모호하면 `held_for_review`로 멈추고 확인 또는 원복하며, 재실행 횟수로 그 모호함을 없애지 않는다.

구성 commit 시점에는 pending의 읽기 전용 gate가 의도적으로 거부되므로 `clock_ready_at_commit=null`이다. 이를 정상 시각으로 표현하지 않는다. commit 후 `post_commit_clock`이 실제 준비 판단이다. `passed=true`/`phase=complete`는 인계 실행기 범위의 구성·요청 인계 결과이고, 8개 업무의 자료 완료나 운영 관찰 완료가 아니다.

## 원복

원복도 위 고정 계획과 같은 boot를 사용한다. 새 자료·request·원장·receipt·T05 근거를 유지하고 **실행 경로와 구성 연결만** 되돌린다. 이전 reference age/strict 대기가 돌아온다. 설치한 새 호환 도구는 삭제하지 않는다.

```sh
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/scheduled-clock-deploy-2026-10-16/handoff.py --action rollback --approve-plan 7eccd61f65c66fd5f3d70f8893e23aa01abe246caa719256e96e004d59753a7a
```

pending 이전에 중단한 경우 구성·ledger가 여전히 원본인지 확인하고 timer만 복구한다. pending 이후에는 같은 drain·시작 차단·정확한 파일/ledger 원복·reload/commit 검사를 사용한다. 이전 apply에서 취소했으나 발행 확인이 없는 요청과 원복이 새로 취소한 요청을 구분한다.

**미완료 wrapper request가 있으면 기본 원복은 보류한다.** 원래 작업·저장 결과·고정 날짜를 점검하고 보존 원복을 선택할 때는 다음 구조의 별도 검토 파일과 그 SHA-256을 명시해야 한다. `requests`에는 현재 미완료 요청의 job→정확한 request.json 해시 전체 집합을 넣는다.

```json
{
  "plan_id": "7eccd61f65c66fd5f3d70f8893e23aa01abe246caa719256e96e004d59753a7a",
  "action": "preserve-for-reviewed-rollback",
  "requests": {"backup": "검토한 request.json의 SHA-256"},
  "reason": "저장 결과·pins 대조 결과와 이후 처리 범위를 구체적으로 기록"
}
```

검토한 경우에만 원복 명령에 `--request-review /절대/검토파일.json --approve-review-hash 정확한_SHA256`을 추가한다. 이 검토는 요청 삭제·완료 처리·고정 범위의 legacy 재실행을 승인하지 않는다. 검토 파일과 요청 해시가 달라지면 거부한다.

원복 후 `request_reconciliation_required=true`라면 아직 전체 복귀 완료가 아니다. `unreconciled_requests`, `preserved_unfinished_requests`, apply/rollback의 `cancelled`·`starts`, 실제 양 manager job·invocation과 업무 원장/보고서를 대조한다. 이미 실행/대기/저장 완료한 것은 재요청하지 않는다. 아직 필요한 것으로 확인한 정확한 service만 운영자가 요청하고 그 결과를 root 실행 기록에 연결한다. 고정 날짜가 있는 요청을 단순한 현재 날짜 legacy 실행으로 해결했다고 기록하지 않는다. 모호하면 점검 상태로 남기고 request/DB를 편집하지 않는다.

원복 후 같은 원래 계획으로 apply하는 것은 거부한다. 원복 후 ledger의 구성 이력과 현재 상태를 기준으로 새 계획·승인을 만든다. 같은 후보/binding의 원래 request는 보존한 정확한 fetch/JSONL 등으로 복구할 수 있고, 설정이 달라졌다면 별도 identity 대조가 필요하다.

## 적용 이후의 확인 범위

최종 실행 경로·UID·중복 없음, 8개 업무의 실제 처리/보류 및 요청 기록, 실제 timer 출처 restic receipt와 다음 정기 backup/T05의 기존 baseline·완료/후행 근거 연결, 같은 collector supervisor의 자연스러운 다음 child를 확인한다. 실제 reference 지연이 관찰되지 않으면 격리 근거와 구분해 기록한다. 운영 장애 주입·과거 전체 복원 반복·새 부팅 시험을 추가 완료 조건으로 만들지 않는다.

보조 request/attempt/JSONL의 독립 백업·회전은 미완료다. 원래 기록을 참조하는 미완료 요청이 있는 동안 기록을 먼저 삭제하지 않는다. 현재 시각 미확인 진단·collector 초기 gate·polling·T05 실제 삭제/자동화는 각각 기존 후속·승인 범위를 유지한다.
