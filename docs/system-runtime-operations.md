# 시스템 수집 서비스 운영

2026-10-09 전환 후 적용된 구성을 설명한다. [실제 부팅·저장 검증](../reports/baseline/system-cutover-2026-10-08/post-reboot-summary.md)과
[남은 작업](../TODO.md)을 함께 확인한다. 과거 후보 문서의 미설치·미검증 표시는 해당 시험 당시의 상태다.

## 실행 코드와 관리 주체

| 대상 | 현재 구성 |
|---|---|
| 고정 배포본 | `/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee` |
| 설치 방식 | root 소유 코드·비 editable venv, `python -I`, 수집 프로세스는 `yoonhc` |
| 시스템 수집 유닛 | `marketdata-collector@depth.service`, `marketdata-collector@aggtrade.service` |
| 시각 준비 | `chrony.service` → `marketdata-clock-bootstrap.service` → `chrony-wait.service`·수집 시작 검사 |
| 호스트 설정 | `/etc/marketdata/runtime.json`, `/etc/marketdata/clock-bootstrap.json`, `/etc/chrony/marketdata.conf` |
| 기존 사용자 업무 | OI·daily·pack·backup·restic·관측 등 8개 업무는 사용자 manager 유지, 공통 시각 대기와 시작 검사 연결 |
| 이전 사용자 수집 유닛 | `marketdata-depth.service`, `marketdata-aggtrade.service`는 masked·비활성 |
| 자동 checkpoint 정리 | service/timer 모두 persistent masked. 상시 시각 검사 검증·승인 전까지 보류 |

개발 폴더 변경은 이 배포본에 자동 반영되지 않는다. 새 배포는 별도 경로에서 후보를 검증·고정하고
검토한 종료/기동 경계에서 적용한다. 설치된 venv나 고정 후보의 파일을 직접 덮어쓰지 않는다.
재현 가능한 원본은 [고정 소스·해시](../reports/baseline/system-runtime-bootstrap-revision-2026-10-07/release.json),
[재현 절차](../reports/baseline/system-runtime-bootstrap-revision-2026-10-07/reproduce.md)에 있다.

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

marketdata_release=/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee
"$marketdata_release/.venv/bin/python" -I "$marketdata_release/integrations/runtime/clock_gate.py" status --profile /etc/marketdata/clock-bootstrap.json
"$marketdata_release/.venv/bin/python" -I "$marketdata_release/integrations/runtime/clock_gate.py" check --profile /etc/marketdata/clock-bootstrap.json
"$marketdata_release/.venv/bin/python" -I "$marketdata_release/integrations/runtime/marketdata_runtime.py" status --profile /etc/marketdata/runtime.json --stream depth
"$marketdata_release/.venv/bin/python" -I "$marketdata_release/integrations/runtime/marketdata_runtime.py" status --profile /etc/marketdata/runtime.json --stream aggtrade
```

시장 데이터 상태와 현재 시각도 함께 확인한다.

```bash
"$marketdata_release/.venv/bin/marketdata" depth-status
"$marketdata_release/.venv/bin/marketdata" aggtrade-status
"$marketdata_release/.venv/bin/marketdata" maintenance-status
chronyc tracking
timedatectl
```

`ledger.closed`는 이번 부팅의 자동 보정 권한이 닫혔다는 뜻이다. 현재 시각의 정상 여부는 `check`와
runtime 상태로 따로 판단한다. `clock_usable=true`도 거래 ID·호가 범위·저장 품질의 정상 판정을 대신하지 않는다.
복구한 호가가 현재 동기화됐어도 같은 collection의 손실 이력과 `partial`은 남는다.
DB를 읽는 `depth-status`를 상태 조회의 기준으로 삼으며, `depth-latest.json`은 쓰기 실패 시 오래된 보조 사본일 수 있다.

공통 waiter에서 대기하는 것은 본 업무 성공이 아니다. 준비가 회복되면 대기한 업무를 시작하지만,
실제 업무 오류의 기존 재시도·시작 제한은 유지한다. 600초 장기 준비 대기는 journal 경고로 남으며 외부 알림은 없다.
waiter만 임의로 stop하면 의존 업무의 요청도 취소될 수 있으므로 업무 재시도나 되돌리기 수단으로 쓰지 않는다.

## 종료·배포·되돌리기

중지나 재시작에는 수집 공백이 생긴다. 승인된 작업창에서 해당 스트림만 system manager로 제어하고,
SIGINT 뒤 저장 건수·종료 보고서·child/cgroup·잠금 해제를 확인한다. 최종 유닛의 `TimeoutStopSec`는 150초다.
정상 세션 종료와 오류 뒤에는 15초 간격의 재기동 정책이 적용되며, 시작할 때 현재 준비 상태를 다시 확인한다.

새 배포 적용 뒤에는 실제 PID의 코드 경로·해시, 스트림별 단독 실행, 원문·표준 저장과 첫 세션 종료를 확인한다.
시각 보정이나 호가 부족을 일부러 발생시킬 필요는 없다. 상세 순서와 rollback 범위는
[검증한 전환·되돌리기 절차](../reports/baseline/system-runtime-bootstrap-revision-2026-10-07/deployment-review.md#상태-조회중지되돌리기)를 따른다.
현재 설치 과정에서 확인한 패키지·alias·권한 조건은 [실제 설치 기록](../reports/baseline/system-cutover-2026-10-08/summary.md)에 남아 있다.

수집 코드 rollback과 시간 서비스/RTC rollback은 별개다. 이전 사용자 수집을 다시 사용한다면 현재 시각 검사와
다음 부팅 조건까지 정하고, system collector와 중복 실행하지 않는다. Windows가 UTC RTC를 사용하는 상태에서
Linux만 LOCAL로 되돌리거나 ledger를 지워 시작 검사를 통과시키지 않는다.

## 백업·시각 기록·정리의 경계

데이터 갱신 `marketdata-daily.service`와 checkpoint 생성 `marketdata-backup.service`를 구분한다.
후자는 13:10 KST 예약이며 완료 시각은 실제 결과로 확인한다. 동일 backup ID의 restic 저장·전체 읽기 검사까지
연결해야 새 복구 지점 검증을 완료한 것이다. `processed=0` 탐색은 새 저장이나 전체 검사 성공으로 세지 않는다.

`state/runtime-clock`의 JSONL은 아직 기존 데이터 백업·복원·연구 조회에 편입되지 않았다.
로그가 없으면 시각 품질 근거가 미확인인 것이며, 정상으로 추정하지 않는다. 보존·collection 연결과
정리 계획/적용 시점의 상시 시각 검사는 [TODO](../TODO.md)의 별도 필수 작업이다.
그 전에는 자동 checkpoint 정리를 재개하지 않는다. 기존 grant·원본·백업·시험 공간을 이 문서 정리로 변경하지 않았다.
