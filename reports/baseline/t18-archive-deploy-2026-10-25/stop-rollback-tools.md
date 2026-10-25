# T18 활성화 후 중지·원복 도구 연결

2026-10-25 현재 운영은 `activated`다. 이 문서는 향후 필요한 경우의 **기존 고정 도구 선택과 경계**를 명확히 한다. 지금 stop/rollback을 실행하거나 원래 apply를 반복하지 않는다. 운영 원복 실험을 새로 수행한 결과가 아니다.

원래 operation은 `27dd5101bfa0b7005ce0a931b075bc9cccad5a5331e9350a30c4765cbf1ee225`, authority는 `/var/lib/marketdata-clock/t18-output-handoffs/27dd5101bfa0b7005ce0a931b075bc9cccad5a5331e9350a30c4765cbf1ee225/authority.json`이다. 원래 승인 시각·권한과 두 실패를 보존한다. 지금의 성공 복구가 옛 host adapter의 파일을 수정한 것은 아니다.

| 목적 | 사용할 고정 진입점 | manager 조회 보완과의 관계 |
|---|---|---|
| archiver 신규 예약 보류·정상 종료 대기 | 이 디렉터리의 원래 `handoff.py --action stop` | `stop`은 archiver timer 하나만 멈추고 archiver service만 drain한다. 실패했던 T05 전체 job JSON 분기, 구성 전환/CLI 검증의 `ExecStartPre` 검사, daemon-reload에 진입하지 않는다. 원래 adapter를 그대로 쓴다는 한계를 명시한다. |
| 구성·ledger·scope 원복 | `../t18-archive-manager-recovery-2026-10-25/repair_resume.py --action rollback` | 성공한 recovery `ab49fce3…`의 보완 adapter를 사용한다. 빈 성공 job 출력, 생략된 명령 속성의 D-Bus 확인, 최종 보호 프로세스 의존관계 순서 정규화를 포함한다. |
| 읽기 전용 상태 확인 | 현재 root receipt·transaction·grant·manager/요청 조회; 필요시 원래 `handoff.py --action check` | `check`는 실제 적용 결과 조회이며 `inspect`의 정적 미적용 표시는 현재 운영 상태가 아니다. 업무 재실행이나 상태 변경을 하지 않는다. |

`repair_resume.py` CLI에는 **stop action이 없다**. 없는 옵션을 제시하지 않는다. 원래 `handoff.py --action rollback`과 첫 recovery `a277ae71…`에는 위 조회 결함이 남으므로 향후 원복 진입점으로 사용하지 않는다.

## 고정 파일

| 파일 | SHA-256 |
|---|---|
| 원래 `handoff.py` | `d9cc290b7cefc52c7e35d5d9930da7dcc2829bc383691d4025729f97ec4032e9` |
| 원래 `native_host.py` | `d82b6fbd186b6189797a9fd81f34a78729afcee55265e94ba251210448137bd6` |
| 보완 `repair_resume.py` | `0a0b0ed9e15a68a1c3ff49c52802f1cd944443fc9fe15ec9b7ddcb454a78875d` |
| 보완 `native_host.py` | `0d4231f0a1d109c7fe7cc04b9862327e2cf4ded347da2e3d140030bded2c229d` |
| 공통 `operation.py` — 두 경로 동일 | `d284e2e3f4127fc2c4a2d8260acb5594d6f141cd6ed692a8b8714490fb847366` |

원래 tool ID `t18-output-operation-20261022-7bb526de6c7fbaef`; 보완 tool ID `t18-manager-query-recovery-20261025-2503f88208b70638`. 보완 계획 바이트 SHA-256 `8a139e1a2aa0c772d6b1e3d264407147d5f50a8e874d9ffd8236257d73545165`. workspace 진입점은 기존 root seal과 해시를 대조하며 root 기록을 덮어쓰지 않는다.

## 필요할 때의 정확한 명령과 종착점

**중지:** 원래 operation의 archiver timer만 보류한다.

```sh
sudo /usr/bin/python3 -B -I \
  /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t18-archive-deploy-2026-10-25/handoff.py \
  --action stop \
  --approve-operation 27dd5101bfa0b7005ce0a931b075bc9cccad5a5331e9350a30c4765cbf1ee225
```

`stop_intent`로 같은 apply의 재활성화를 먼저 닫는다. timer 정지 후 coordinator를 점유하지 않고 본문 정상 종료를 최대 600초 기다린다. 초과하면 timer 보류·본문 진행 상태이며 강제 종료나 구성 변경을 하지 않는다. 성공한 `archiver_timer_held`도 **구성/ledger/recurring 원복 완료가 아니다**. 다른 timer와 T05는 유지한다. 원래 도구의 최종 raw 의존관계 비교가 false라면 원본 결과를 보존하고 PID/invocation·집합을 대조하며 제어 명령을 자동 반복하지 않는다.

**원복:** 성공한 manager 보완 경로를 쓴다.

```sh
sudo /usr/bin/python3 -B -I \
  /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t18-archive-manager-recovery-2026-10-25/repair_resume.py \
  --action rollback \
  --approve-recovery ab49fce3b04087ceb44874b0fde1c452bd457e4b7b9e255dfda4aa7b8ad90eb6
```

이 경로는 같은 original operation/authority와 accepted recovery를 확인하고 9개 timer를 보류한다. 600초 정상 종료 대기 뒤, 요청 종결·알려진 before/after·ledger·잠금 검사를 통과해야 비활성 recurring 아래 구성·ledger·이전 grant를 복원한다. 이전 active였던 다른 8개 timer를 복원하되 archiver는 정지 상태로 남겨 구버전의 출력 누적을 막는다. `rolled_back_archiver_held`와 실제 파일/grant/ledger·timer 결과로 완료를 판단한다. 현재 요청이나 환경이 달라 보류되면 자동 초기화하지 않는다.

새 body·receipt·request·snapshot·자료·T05 근거를 삭제하지 않는다. stop/rollback 후 같은 apply 승인으로 재활성화하지 않으며, 모호한 timer 시작을 자동 재발행하지 않는다. 현재 boot·보호 입력·권한·프로세스 또는 구성의 변화가 있으면 도구의 보류를 따른다.

## 검증 근거와 한계

공통 engine의 원복·중단·600초 stop 경계는 [기존 전환 검사](../t18-archive-deploy-2026-10-22/tests/test_operation.py)의 `test_rollback_preserves_new_records_and_keeps_archiver_held`, `test_stop_timeout_closes_reactivation_without_force_or_config_changes`, `test_rollback_interruption_can_finish_without_reopening_archiver` 등을 재사용한다. manager 보완은 [31개 결과](../t18-archive-manager-recovery-2026-10-25/review.md)의 apply/rollback 매개변수 검사와 실제 읽기 전용 manager 대조를 연결한다. 이번에는 파일 해시와 호출 분기를 대조했으며 전체 검사·실제 원복을 반복하지 않았다. 미래의 모든 상태에서 무조건 원복할 수 있다는 보장은 아니다.
