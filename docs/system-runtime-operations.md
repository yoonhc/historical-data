# 시스템 수집 서비스 운영

2026-10-09 19:33 KST의 T04·T11 적용 이후 구성을 설명한다.
[실제 적용·실패/재개·초기 저장 확인](../reports/baseline/clock-runtime-deploy-2026-10-09/summary.md)과
[남은 작업](../TODO.md)을 함께 확인한다. 확보한 운영 관측은 20:17 KST까지이며,
첫 child 전환과 새 시각 근거의 운영 백업·복원은 관찰 대기다. 과거 후보의 미적용 표시는 당시 상태다.

## 실행 코드와 관리 주체

| 대상 | 현재 구성 |
|---|---|
| 수집 고정 배포본 | `/opt/marketdata/releases/clock-evidence-policy-20261009-65296619a374c325` |
| 부팅 준비·기존 업무의 배포본 | `/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee` 유지 |
| 설치 방식 | root 소유 코드·비 editable venv, `python -I`, 수집 프로세스는 `yoonhc` |
| 시스템 수집 유닛 | `marketdata-collector@depth.service`, `marketdata-collector@aggtrade.service` |
| 수집 수명 | 지속 supervisor 아래 3,600초 child, child 사이 15초도 시각 감시 유지 |
| 시각 준비 | `chrony.service` → `marketdata-clock-bootstrap.service` → `chrony-wait.service`·수집 시작 검사 |
| 호스트 설정 | `/etc/marketdata/runtime.json`, `/etc/marketdata/clock-bootstrap.json`, `/etc/chrony/marketdata.conf` |
| 기존 사용자 업무 | OI·daily·pack·backup·restic·관측 등 8개 업무는 사용자 manager 유지, 공통 시각 대기와 시작 검사 연결 |
| 이전 사용자 수집 유닛 | `marketdata-depth.service`, `marketdata-aggtrade.service`는 masked·비활성 |
| 자동 checkpoint 정리 | service/timer 모두 persistent masked. 상시 시각 검사 검증·승인 전까지 보류 |

개발 폴더 변경은 이 배포본에 자동 반영되지 않는다. 새 배포는 별도 경로에서 후보를 검증·고정하고
검토한 종료/기동 경계에서 적용한다. 설치된 venv나 고정 후보의 파일을 직접 덮어쓰지 않는다.
새 수집본의 [소스·해시](../reports/baseline/clock-runtime-transition-2026-10-09/collector-correction/release.json)와
[재현 절차](../reports/baseline/clock-runtime-transition-2026-10-09/reproduce.md)를 보존한다.
이전 부팅 준비본의 [소스·해시](../reports/baseline/system-runtime-bootstrap-revision-2026-10-07/release.json)도 유지한다.
기존 workspace의 pack·backup·restic과 새 수집본의 호환은 격리 저장소에서 검증했다.
workspace의 패키지와 설치된 collector가 항상 같은 버전이라고 가정하지 않는다.

## 일상 조회

아래 명령은 현재 프로젝트 루트에서 실행한다. 시스템 수집에는 `--user`를 쓰지 않으며,
기존 예약 업무는 `--user`로 조회한다.

```bash
systemctl status marketdata-clock-bootstrap.service marketdata-collector@depth.service marketdata-collector@aggtrade.service
journalctl -b -u marketdata-clock-bootstrap.service -u chrony.service --no-pager
journalctl -b -u marketdata-collector@depth.service -u marketdata-collector@aggtrade.service -n 100 --no-pager
systemctl --user list-timers --all 'marketdata-*'
systemctl --user status marketdata-clock-ready.service
systemctl --user list-jobs

marketdata_bootstrap_release=/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee
marketdata_collector_release=/opt/marketdata/releases/clock-evidence-policy-20261009-65296619a374c325
marketdata_root=/home/yoonhc/projects/algoTrade/historical_data
"$marketdata_bootstrap_release/.venv/bin/python" -B -I "$marketdata_bootstrap_release/integrations/runtime/clock_gate.py" status --profile /etc/marketdata/clock-bootstrap.json
"$marketdata_bootstrap_release/.venv/bin/python" -B -I "$marketdata_bootstrap_release/integrations/runtime/clock_gate.py" check --profile /etc/marketdata/clock-bootstrap.json
"$marketdata_collector_release/.venv/bin/python" -B -I "$marketdata_collector_release/integrations/runtime/capture_runtime.py" --profile /etc/marketdata/runtime.json --bootstrap-profile /etc/marketdata/clock-bootstrap.json --stream depth --status
"$marketdata_collector_release/.venv/bin/python" -B -I "$marketdata_collector_release/integrations/runtime/capture_runtime.py" --profile /etc/marketdata/runtime.json --bootstrap-profile /etc/marketdata/clock-bootstrap.json --stream aggtrade --status
```

시장 데이터 상태와 현재 시각도 함께 확인한다.

```bash
"$marketdata_collector_release/.venv/bin/python" -B -I -m marketdata.cli --root "$marketdata_root" depth-status
"$marketdata_collector_release/.venv/bin/python" -B -I -m marketdata.cli --root "$marketdata_root" aggtrade-status
"$marketdata_collector_release/.venv/bin/python" -B -I -m marketdata.cli --root "$marketdata_root" maintenance-status
chronyc tracking
timedatectl
```

`ledger.closed`는 이번 부팅의 자동 보정 권한이 닫혔다는 뜻이다. 최초 시작의 엄격한 준비는 `check`와
`wait-ready`, 실행 중 시각 상태는 새 supervisor 상태로 확인한다.
서비스 활성 상태, 실제 child 수집 여부, 마지막 collection의 시장 품질을 각각 확인한다.
복구한 호가가 현재 동기화됐어도 같은 collection의 손실 이력과 `partial`은 남는다.
DB를 읽는 `depth-status`를 상태 조회의 기준으로 삼으며, `depth-latest.json`은 쓰기 실패 시 오래된 보조 사본일 수 있다.

## 신선도와 다음 child 시작

| 표시·조건 | 의미 |
|---|---|
| `current_estimate_usable` | 현재 provider·추정 오차 등의 유효성. 양의 reference 지연과 분리한 판정 |
| `reference_stale` | 양의 reference age가 프로젝트의 600초 신선도 기준을 넘었는지 |
| `raw_start_allowed` | 초기 준비·현재 관측·감시 이력을 함께 고려한 다음 child 시작 허용 |
| `history` | 과거 stale·hard fault 등 누적 관측. 현재 회복으로 지우지 않음 |

같은 supervisor가 계속 감시하고 다른 안전 조건을 만족할 때 reference 지연만으로 다음 child를 차단하지 않는다.
남은 보정량 100ms·조건부 오차 500ms·skew 등 기존 한계를 유지하며 미래 reference·조회 실패·관측 만료는
이 예외로 통과시키지 않는다. hard fault 후에는 엄격한 현재 준비 회복이 필요하고, 해당 실행의 stale 예외는 다시 열리지 않는다.
supervisor 자체가 재시작되면 엄격한 준비부터 다시 확인한다. 600초 초과를 곧 시각 오류나 시간원 교체 필요로 해석하지 않는다.
이 판단은 메시지별 UTC 정확도 인증이 아니며 과거 `clock_usable=false`·수신 시각을 고쳐 쓰지 않는다.

기존 8개 예약 업무의 엄격한 gate는 이번에 변경하지 않았다.
공통 waiter에서 대기하는 것은 본 업무 성공이 아니다. 준비가 회복되면 대기한 업무를 시작하지만,
실제 업무 오류의 기존 재시도·시작 제한은 유지한다. 600초 장기 준비 대기는 journal 경고로 남으며 외부 알림은 없다.
waiter만 임의로 stop하면 의존 업무의 요청도 취소될 수 있으므로 업무 재시도나 되돌리기 수단으로 쓰지 않는다.

## 종료·배포·되돌리기

중지나 재시작에는 수집 공백이 생긴다. 승인된 작업창에서 해당 스트림만 system manager로 제어하고,
SIGINT 뒤 child의 마지막 저장, 시각 근거 확정·등록 worker의 종료, supervisor/cgroup·잠금 해제를 확인한다.
실효 설정은 `RuntimeMaxSec=infinity`, `Restart=always`, `RestartSec=15s`, `KillMode=mixed`,
`KillSignal=SIGINT`, `TimeoutStopSec=330s`다. 한 시간마다 끝나는 것은 child이며 supervisor는 유지된다.
같은 run·boot·child의 저장된 최종 보고서가 `partial`이면 종료 코드 2도 이력을 보존하며 다음 child로 이어간다.
저장 실패나 다른 child의 보고서를 같은 정상 전환 근거로 사용하지 않는다.

새 배포 적용 뒤에는 실제 PID의 코드 경로·해시, 스트림별 단독 실행, 원문·표준 저장과 첫 세션 종료를 확인한다.
시각 보정이나 호가 부족을 일부러 발생시킬 필요는 없다. 상세 순서와 rollback 범위는
[실제 적용과 현재 원복 절차](../reports/baseline/clock-runtime-deploy-2026-10-09/summary.md#현재-상태-조회와-원복)를 따른다.
원래 전환 도구의 반복 `ExecStartPre` 출력 파서 결함은 별도 재개 코드로 보완했고,
원래 계획·도구와 보완 코드 해시를 receipt에 함께 남겼다. 원래 설치기를 그대로 재실행하지 않는다.
해당 원복 도구는 같은 부팅·현재 파일/ledger·준비 상태를 검증한다. 다른 부팅에 과거 계획을 재사용하지 않는다.

원복은 이전 실행 코드와 설정으로 돌아가는 절차다. 새 artifact·collection·인덱스·spool을 삭제하거나
카탈로그를 과거 백업으로 덮어쓰지 않는다. 이전 runtime의 엄격한 시작 정책도 돌아오며,
새 시각 근거 조회에는 보존한 새 CLI를 사용한다.

수집 코드 rollback과 시간 서비스/RTC rollback은 별개다. 이전 사용자 수집을 다시 사용한다면 현재 시각 검사와
다음 부팅 조건까지 정하고, system collector와 중복 실행하지 않는다. Windows가 UTC RTC를 사용하는 상태에서
Linux만 LOCAL로 되돌리거나 ledger를 지워 시작 검사를 통과시키지 않는다.

## 백업·시각 기록·정리의 경계

데이터 갱신 `marketdata-daily.service`와 checkpoint 생성 `marketdata-backup.service`를 구분한다.
후자는 13:10 KST 예약이며 완료 시각은 실제 결과로 확인한다. 동일 backup ID의 restic 저장·전체 읽기 검사까지
연결해야 새 복구 지점 검증을 완료한 것이다. `processed=0` 탐색은 새 저장이나 전체 검사 성공으로 세지 않는다.

신규 v2 시각 근거는 300초 또는 1MiB에 닫힌 JSONL을 불변 raw artifact로 등록하며,
collection·supervisor run·boot·child와 연결한다. 등록된 근거는 기존 pack·checkpoint·restic·restore 경로를 따른다.
격리 복원과 조회는 통과했으며, 운영에서 생성한 새 근거의 첫 예약 백업·복원 확인은 아직 남아 있다.
기존 v1 로그는 자동 편입하지 않았고, 근거가 없는 과거 구간을 정상으로 추정하지 않는다.

다음은 처음 적용한 depth collection의 읽기 전용 조회 예다. 다른 collection이나 복원 저장소는 해당 ID·root를 지정한다.

```bash
"$marketdata_collector_release/.venv/bin/python" -B -I -m marketdata.cli --root "$marketdata_root" clock-evidence --collection-id d2d18424592f4f549f5bda828e270759
```

`evidence_state=available`은 근거를 읽을 수 있다는 뜻이며 정상 시각·시장 품질 판정이 아니다.
열린 collection은 이미 닫힌 근거가 있어도 `collection_end_unknown`·`unknown`과 종료 코드 2를 반환할 수 있다.
다른 `unknown`도 모두 같은 원인이라고 가정하지 않고 실제 사유와 빠진 구간을 확인한다.

staging은 정상 상태에서 약 6MiB 수준으로 제한하지만 등록 artifact는 계속 누적된다.
기록·등록 실패나 한도 초과 때는 수집을 종료하고 spool을 보존한다. 5분 회전은 최대 유실 시간 보장이 아니다.
자동 checkpoint 정리는 상시 시각 검사 T05의 별도 검증·승인 전까지 masked 상태로 유지한다.
