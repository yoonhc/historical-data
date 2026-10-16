# T15 운영 인계 실행기 검토

**관리자 사전 점검·별도 승인 후 10/13 00:32:58 실제 이전, 22:12 합의한 T15 운영 검증 완료.**
[실제 적용·현재 상태](../scheduled-system-deploy-2026-10-13/status.md)를 함께 확인한다.
[정오 실제 처리·정기 backup/T05/system restic 연결](../scheduled-system-operating-completion-2026-10-13/summary.md)까지 확인했으며 업무별 시각 정책은 별도 후속이다.
`handoff.py` 기본 동작은 inspect이며, live apply/rollback에는 정확한 계획의 root 승인과 같은 도구의 통과한 preflight가 필요하다.
기존 후보의 fixture 전용 transaction CLI를 운영 쓰기로 열지 않고, 같은 엔진에 실제 파일/manager adapter를 연결했다.

## 변경 목록

[수정 구성 계획](configuration-plan.json)의 46개 before/after와
[최종 인계 계획](host-plan-preflight-passed.json)의 8개 시스템 timer wants 링크가 구성 변경 목록이다.
원래 유닛·link target·파일 바이트·mode·UID/GID와 실제 Exec 속성을 대조한다.

| 대상 | 수·범위 |
|---|---|
| 새 시스템 업무 service/timer | 16 |
| 새 시스템 strict waiter, 기존 bootstrap adapter 연결 | 2 |
| 기존 사용자 업무 service/timer persistent mask | 16 |
| 기존 사용자 timer wants 링크 제거/보존 | 8 |
| topology·OI 설정 사본·bootstrap profile·T05 binding | 4 |
| 추가 시스템 timer wants 링크 | 8 |
| 소스/환경 설치 | 고정 release의 manifest 41개 소스 + 검증된 non-editable venv, root 소유·쓰기 제한 |
| 전환 상태 | 기존 boot ledger의 pending/정합성 확정, 별도 plan/approval/progress/command/receipt |

46개에 boot ledger·실행 기록은 포함되지 않는다. 파일 수만으로 manager 명령까지 승인됐다고 취급하지 않는다.
기존 소스 release는 동일하다. 원래 계획의 OI 3종·pack 명령 오류만 수정한
[7개 구성 차이](entrypoint-correction.json)를 승인 검토에 포함한다.
과거 소스/도구/계획은 삭제하거나 같은 ID로 바꿔 쓰지 않는다.

T13/T05 실행본, 8개 시각 gate·예약·재시도·종료 정책·잠금·데이터·원장 경로는 유지한다.
공개 관측기를 켜지 않으며 네 자동 정리 mask를 유지한다. Chrony·collector restart/stop, RTC/clock 제어,
데이터/카탈로그 원복, checkpoint 삭제, T05 근거 재등록은 명령/쓰기 범위에 없다.

## 적용 순서

1. 같은 boot, 고정 도구/소스/부모 설치본, 운영 파일·외부 설정·mask·양 manager 목록·원래 실효 유닛을 확인한다.
   root preflight의 실제 소유권을 포함한 최종 계획과 root 소유 승인 파일을 대조한다.
2. 고정 파일만 새 `/opt/marketdata/releases/<release>`에 복사한다. 네트워크 설치/uv/cache hardlink를 사용하지 않는다.
   내용이 같은 승인된 부분 설치만 재개하며, UID/모드를 확정한 뒤 UID 1000으로 실제 CLI import를 확인한다.
   현재 자동 검사는 최종 경로의 `.venv/bin/python -B -I -m marketdata.cli --help`다.
   적용 때는 기존 `install_release()`로 설치만 먼저 수행하고, 업무 보류 전에 UID 1000의 읽기 전용 조회로
   `sys.executable`, `sys.prefix`, `marketdata.__file__`, `marketdata.cli.__file__`도 기록·대조한다.
   interpreter 진입 경로와 prefix는 승인된 최종 venv, 두 모듈은 그 venv의 고정 패키지 경로여야 한다.
   조회가 다르면 인계 apply로 진행하지 않는다. 일치하면 apply의 설치 단계는 같은 파일을 재검증한다.
3. **현재 strict 준비를 확인한 뒤** 전환 의도를 기존 coordinator 잠금 아래 기록한다.
   기존 T05 등에서 공유 잠금을 사용 중이면 강제 해제하지 않고 보류한다. 이때 ledger는 아직 원래 closed다.
4. source timer 8개의 새 요청을 멈추고 source base service/timer 16개를 persistent mask로 교체한다.
   `disable`로 임의 링크를 지우지 않는다. 원래 base와 wants node는 승인 계획에 보존한다.
5. 실제 main/control PID가 있으면 정상 종료를 기다린다. PID 0이어도 running job은 unknown으로 기다린다.
   waiting job/auto-restart는 원래 job·명령·invocation을 저장하고 mask와 상태를 다시 읽은 뒤 취소한다.
   모든 업무/job 종료 후 공통 waiter를 멈춘다. 기본 600초 대기 만료는 hold이며 kill이 아니다.
6. 양 manager의 유휴 상태와 T05 작업 부재를 확인한 뒤 기존 transaction으로 pending ledger와 구성 변경을 수행한다.
   reload 후 실제 시스템 Exec 명령 전체·보호/종료 설정·소유권·사용자 mask를 대조한다.
   closed/보정/과거 증거를 유지한 구성 receipt와 현재 시각 준비 결과를 구분한다.
7. 정확한 시스템 wants 링크 8개를 만들고 시스템 timer를 시작한다. 취소했던 업무 요청도 새 manager에서 재평가한다.
   Persistent/OnActive 따라잡기는 기존 T13 원장·backup 재사용·archiver 탐색이 처리한다.
   수동 daily 전체 반복이나 과거 timer 이벤트 재생 목록을 만들지 않는다.
8. 완료 receipt는 **구성 적용과 시작 요청 완료**다. 실제 자료 처리·보관 성공은 이후 운영 관찰로 확인한다.

전환 잠금은 기존 coordinator이며 전체 인계 중복 실행에는 별도 handoff lock을 사용한다.
실행 중 T05 완료 근거를 깨지 않도록 profile/ledger 변경은 업무가 끝난 뒤 수행한다.
수집기에 stop 명령을 보내지 않지만 예기치 않은 supervisor 재시작은 pending 동안 새 준비를 기다릴 수 있다.
모든 예외에서도 수집 공백이 없다고 보장하지 않는다.

### 종료 대기 중 coordinator 잠금 — 10/13 코드 대조

고정 인계 도구와 실제 설치된 T05 후처리를 대조했다. **업무 종료 대기 중 coordinator의 배타 잠금을
유지하지 않는다.** 아래는 코드의 획득·해제 범위이며 운영 업무를 새로 병행 실행한 시험 결과는 아니다.

| 단계 | 실제 잠금 범위 |
|---|---|
| 전체 인계 | [live_execution](handoff_inputs.py)의 별도 `scheduled-handoffs/<plan>/handoff.lock`을 실행 전체에서 유지 |
| 의도 기록 | [inner_intent](handoff.py)의 `with self.fs.lock()` 안에서 확인·기록하고, 반환 전에 coordinator 해제. boot ledger는 closed 유지 |
| 신규 요청 보류·업무 종료 대기 | `hold_source()`·`drain()`은 coordinator를 잡지 않음. 정상 업무 종료와 T05 완료 후 관측이 진행할 수 있음 |
| 설정 변경·확정 | 종료 대기 후 [Transaction.stage/commit](../scheduled-system-migration-2026-10-12/candidate/migration/transaction.py)이 각각 coordinator를 다시 잡고 정합성 확인. 원복의 `rollback_pending()`도 종료 대기 뒤 실행 |

[SafeFiles.lock](handoff_common.py)은 기존 coordinator inode에 `LOCK_EX | LOCK_NB`를 요청하며
이미 공유 잠금이 있으면 인계를 hold한다. 무기한 잠금 대기로 바꾸거나 실행 중 업무를 종료하지 않는다.

T05 설치본 `t05-completion-evidence-20261011-c9d413cfe11ec0e7`의 실제 호출은
`backup.py:create_backup()` → 완료 marker를 저장한 `_create_backup()` 반환 →
`checkpoint_evidence.Producer.complete()` → `scripts/checkpoint_evidence.py:fresh_observer()` →
`checkpoint_clock.Host.lock()`이다. 마지막 관측에서 같은 coordinator의 `LOCK_SH | LOCK_NB`를 사용한다.
따라서 **인계가 coordinator를 잡은 채 백업 종료를 기다리고, 백업이 그 잠금을 기다리는 순환은 없다.**

이는 모든 순간의 잠금 경합이나 시각 근거 실패가 없다는 보장은 아니다. 짧은 의도 기록 구간과 관측이
겹치면 T05는 잠금 실패를 근거 미확보로 기록할 수 있고, 정상 데이터 백업은 보존한다.
기존 방침대로 backup·pack·restic이 유휴인 시점에 적용하고 실제 완료 근거를 대조한다.
이번 확인으로 실행 코드·구성·도구·최종 계획은 변경하지 않았다.

## 중단 후 재개

외부 단계는 `new → installed → intent → held → drained → staged → committed → activated → complete`다.
각 단계·명령 intent/result를 `/var/lib/marketdata-clock/scheduled-handoffs/<최종-plan-id>/`에 기록한다.
기존 엔진의 receipt는 `/var/lib/marketdata-clock/scheduled-transitions/<구성-plan-id>/`에 남긴다.

- 같은 boot·계획·승인·파일 조합이면 같은 apply 명령으로 완료 단계부터 이어간다.
- 업무 종료 대기/공유 잠금 때문에 보류되면 원래 실행의 실제 종료를 확인한 뒤 이어간다. 강제 종료·reset-failed로 우회하지 않는다.
- start intent 뒤 응답 기록 전 중단되면 새 invocation/기동 근거를 확인한다.
  발행 여부가 모호하면 자동으로 다시 실행하지 않고 해당 unit/job/원장과 commands 기록을 검토한다.
  미발행이 확인된 정확한 요청만 운영자가 재발행한 뒤 같은 실행기로 관측·연결한다. 예산/원장을 직접 초기화하지 않는다.
- 계획에 없는 파일·ledger·boot 변경은 자동 덮어쓰기나 새 계획 자동 승인 대상이 아니다.
  특히 재부팅 뒤에는 이전 같은-boot 계획을 그대로 재실행하지 않는다. 현재 남은 단계로 복구안을 다시 검토한다.
- timer 활성화 후 서비스가 strict waiter를 기다리는 것은 이번 정책에 따른 상태일 수 있다. 업무 자체 성공으로 표시하지 않는다.

## 원복

같은 최종 계획의 rollback은 부분 적용과 완료 적용을 다룬다. source·target 양쪽 timer/재시작 장벽을 확인하고,
실행 중 본 업무는 정상 종료를 기다린다. pending을 확정한 뒤 이전 user base/wants·bootstrap·T05 binding을 복원한다.
새 시스템 wants 링크와 유닛을 제거하고 old user 실효 상태를 대조한 후 원래 timer와 취소 요청을 재개한다.
원복한 계획을 다시 apply하지 않는다. 다음 이전은 새 상태의 계획/승인으로 검토한다.

새 manager가 만든 자료·카탈로그·daily 작업/시도/냉각·receipt·checkpoint/restic·T05 baseline/완료 상한/생성 순서는 유지한다.
고정 소스 설치 경로와 전환 실패/원복 이력도 남긴다. 옛 receipt의 user를 system으로 고쳐 쓰지 않는다.
원복 후 이전 strict 시간 정책도 유지되므로 reference 지연 문제의 해결로 보고하지 않는다.

**부분 원복에는 취소 요청의 수동 대조가 포함된다.** 현재 실행기는 해당 방향의 `cancelled`만 자동으로 재요청한다.
apply 중 취소됐으나 system 기동 전에 원복한 요청까지 rollback 기록에 자동 합치지는 않는다.
특히 backup에는 활성화 직후 실행이 없으므로 timer 복원만으로 원래 대기 요청을 처리했다고 보지 않는다.

1. 같은 계획의 `apply.json`과 `rollback.json`에서 `cancelled`, `start_requests`를 함께 읽는다.
2. apply에서 취소한 user 서비스 중 system 시작이 발행·수행되지 않았고, rollback에서도 재요청하지 않은 대상을 고른다.
   발행 여부가 애매하면 command intent/result·실제 job/invocation·업무 완료 보고서/원장으로 확인한다.
3. 현재 user에서 실행·대기 중이지 않고 여전히 처리할 요청임을 확인한 **정확한 서비스**만
   UID 1000 터미널에서 `systemctl --user start --no-block <service>`로 재요청하고 명령과 결과를 복구 기록에 남긴다.
   8개 업무 전체를 일괄 시작하거나 `reset-failed`로 예산을 초기화하지 않는다. strict gate와 기존 자료 재평가는 그대로 적용된다.

**운영 원복 완료에는 이 대조 결과의 기록까지 포함한다.** 이미 실행·대기·완료된 요청은 그 근거를 연결하고,
필요한 미발행 요청은 정확히 재요청한다. 발행·처리 여부가 불명확하면 확인 필요 상태를 남기며
유닛/timer 복원이나 실행기의 `complete`만으로 운영 원복 전체를 완료 처리하지 않는다.

이 절차의 원래 취소 기록 보존·원복·정확한 미발행 요청 재개는
[격리 검증](rollback-runbook-tests.xml)했다. 이 경계를 포함하므로 완전한 무인 원복으로 보고하지 않는다.

T05의 당시 완료/순서 근거는 유효한 과거 기록으로 보존한다. 현재 binding 변화에 따라 삭제 승인 scope는 달라질 수 있다.
기존 비활성 grant를 덮어쓰거나 새 scope에 재사용하지 않으며 baseline 재등록/보호 기간 초기화도 하지 않는다.

## 최종 계획과 다음 단계

root preflight 통과 후 `build_host_plan.py --preflight <통과보고서>`로 실제 소유권·보고서 해시를 포함해 고정했다.

```text
source release: scheduled-manager-20261012-255d64b78ffa8328
configuration: 0c9b85223c2ad851434857b4eaa86f11e94595b4663134f98881ede02fefd8fe
handoff tool: scheduled-handoff-20261012-b48b6beb0b9b72b9
final plan: 6a71f295ec760f442edf0f88b04c6cf9378d9383fb9c45c0dc7c8c140eac3f2b
boot: eb163bf2-6642-4336-b4d6-6cd58771b13d
```

[최종 승인 template](host-plan-preflight-passed.approval.template.json)은 `enabled=false`다.
10/13 사용자가 위 변경 목록과 수동 복구 경계를 포함한 운영 이전을 승인했다. 과거 template은 그대로 보존한다.
[승인된 관리자 준비 진입점](../scheduled-system-deploy-2026-10-13/status.md)이 root-owned 승인·최종 import 조회를 먼저 수행한 뒤
고정 실행기의 apply를 호출한다. 원복은 같은 고정 계획/승인의 `--action rollback`과 위 취소 요청 대조 절차를 따른다.
**10/13 00:32:58 실제 적용 완료.** [적용 원본·현재 운영 확인과 남은 범위](../scheduled-system-deploy-2026-10-13/status.md)를 따른다.
사용자 유닛 16개 mask·시스템 timer 8개 활성·고정 파일/실효 구성·동일 수집 supervisor를 확인했다.
준비 진입점은 이미 사용했으므로 재실행하지 않는다. 위 고정 도구/계획은 변경하지 않았다.

실제 적용 뒤 확인 범위는 사용자/시스템 중복 부재, 8개 업무의 첫 실제 처리와 기존 계약에 따른 결과,
system timer 출처 restic receipt, 다음 정기 backup의 T05 연결, 기존 collector의 자연스러운 다음 child 경계다.
기존 계약의 OI 품질 경고·daily 공개 대기·strict 시각 대기를 구분하며 전체 `healthy=true`를 요구하지 않는다.
같은 부팅의 이전 확인을 새 구성의 실제 재부팅 시험으로 표시하지 않는다.
추가 대규모 복원·Chrony 장애 주입·T13 전체 운영 시험을 반복하지 않는다.
업무별 시각 정책 변경과 polling 복원은 별도 단계다.
