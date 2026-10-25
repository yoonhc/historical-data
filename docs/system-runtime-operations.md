# 시스템 수집·예약 업무 운영

**최신 기록 기준은 2026-10-25 20:15 KST다.** 아래 이전 전환 이력과 현재 실행 경로를 구분한다.
[10/25 마일스톤](milestones/2026-10-25-retirement-and-archive-capacity-recovery.md)에 완료·잔여 범위를 모았다.
T05 반복 자동화와 archiver timer는 활성, 사용자 자동 정리 두 유닛은 masked, manual 승인은 비활성이다.
T16 1차 운영 검증과 T18의 장애 복구·출력 공유 효과 확인은 완료했다.
20:13 표본에서 수집 supervisor는 유지됐지만 다음 child는 기존 시각 정책으로 대기 중이었다.
이 문서 정리로 현재 호스트를 다시 점검하거나 완료한 적용을 재실행하지 않았다.

2026-10-09 19:33 KST의 T04·T11 적용 이후 구성을 설명한다.
[실제 적용·실패/재개·초기 저장 확인](../reports/baseline/clock-runtime-deploy-2026-10-09/summary.md)과
[남은 작업](../TODO.md)을 함께 확인한다. **21:59 KST의 후속 확인**에서
[첫 child 전환과 자연스러운 reference 지연 중 다음 시작](../reports/baseline/t11-first-child-boundary-2026-10-09/summary.md)을 확인했다.
신규 v2 시각 근거의 운영 백업·복원도 **10/10 15:24 KST 완료**했다.
[실제 checkpoint·restic 전체 검사·복원 조회](../reports/baseline/t04-operating-clock-restore-2026-10-10/summary.md)를 확인한다.
기존 v1 로그 편입은 별도 잔여 범위이며, 과거 후보의 미적용 표시는 당시 상태다.
**10/11 00:51 KST에는 T13 daily를 별도 고정 배포본으로 전환했고 00:53 타이머를 활성화했다.**
[설치·전환 중단과 재개, 첫 실행/처리 0건 확인](../reports/baseline/t13-daily-deploy-2026-10-11/summary.md)을 남겼다.
**10/12에는 다음 KST/UTC 경계·기존 완료 유지와 10/11 정오/저녁 26개 시도의 운영 백업·선택 복원까지 확인했다.**
[T13 최종 운영 검증](../reports/baseline/t13-operating-completion-2026-10-12/summary.md)을 완료했고,
공통 waiter의 reference_age 대기는 별도 업무별 시각 정책 후속으로 유지한다.
**10/11 18:20 점검**에서 첫 UTC 경계·정오 10건·저녁 16건의 실제 처리와 정오분의 checkpoint/restic 편입을 확인했다.
[운영 결과](../reports/baseline/t13-operating-review-2026-10-11/summary.md)와
[남은 고정 범위](../reports/baseline/t13-operating-review-2026-10-11/completion-plan.md)를 따른다.
당시 대기였던 다음 주기 구분·저녁분 후속 백업·독립 복원은 위 10/12 고정 범위 검증으로 완료했다.
**T05는 10/11 16:21 설치·기존 7개 사후 근거 등록, 17:22 backup/restic 실행 명령 전환을 마쳤다.**
[실제 적용·후속 완료](../reports/baseline/t05-evidence-deploy-2026-10-11/status.md)를 구분한다. 당시 자동 정리는 masked였으며,
이후 [10/18 활성화](../reports/baseline/t05-recurring-deploy-2026-10-18/status.md)와
[10/19 첫 자연 자동 삭제·보존](../reports/baseline/t05-recurring-operating-completion-2026-10-19/summary.md)을 완료했다.
**10/13 00:32:58 T15의 승인된 이전을 완료해 8개 예약 업무도 시스템 manager가 운영한다.**
[실제 적용·실효 구성·초기 실행·수집 경계](../reports/baseline/scheduled-system-deploy-2026-10-13/status.md)를 확인했다.
기존 사용자 업무/timer 16개는 masked이며, T15 단계에서는 strict 시각 정책을 유지했다.
**10/13 22:12 T15 1단계 운영 검증 완료:** 정오 실제 시도 10건·정기 checkpoint와 T05 완료/순서 근거,
system restic의 새 snapshot·전체 검사·지정 지점의 근거 bundle 조회까지 [대조](../reports/baseline/scheduled-system-operating-completion-2026-10-13/summary.md)했다.
당시 업무별 시각 요구 분리·600초 조건 변경·polling 복원은 별도 후속이었다. 10/16 변경 범위는 아래 T16 상태를 따른다.
원복에는 [고정 계획·부분 원복의 미전달 요청 대조](../reports/baseline/scheduled-system-handoff-2026-10-12/deployment-review.md)를 사용한다.

**10/16 22:23:51 KST T16을 별도 승인한 계획으로 적용했다.** 8개 업무는 새 전용 waiter와 판단 지점별 시각 구간을 사용한다.
[설치·실제 초기 결과·남은 범위](../reports/baseline/scheduled-clock-deploy-2026-10-16/status.md)를 확인한다.
OI 품질 보고서의 Decimal 기록 결함은 별도 보완했고, **10/17 00:17:06 KST 원래 한 요청의 근거 기반 종결과 수정본 전환을 완료**했다.
[실제 복구·새 품질 실행](../reports/baseline/scheduled-clock-repair-deploy-2026-10-16/status.md)에 이어
[10/18 운영 완료](../reports/baseline/scheduled-clock-operating-review-2026-10-18/summary.md)에서 실제 daily 처리와 정기 backup/T05/restic 연결을 확인했다.
T16 당시 [수정본 적용 계획](../reports/baseline/scheduled-clock-repair-deploy-2026-10-16/deployment-review.md)은 이력으로 보존한다.
10/25 T18 이후의 중지·원복은 [현재 고정 도구와 manager 조회 보완의 연결](../reports/baseline/t18-archive-deploy-2026-10-25/stop-rollback-tools.md)을 따른다.
원복 시 archiver timer는 보류하고 새 body·receipt·request·자료·근거를 보존한다. 원래 실패한 apply를 다시 실행하지 않는다.
미완료 요청의 해시·pins·저장 근거 검토를 생략하거나 요청 삭제·T05 재등록으로 우회하지 않는다.

## 실행 코드와 관리 주체

| 대상 | 현재 구성 |
|---|---|
| 수집 고정 배포본 | `/opt/marketdata/releases/clock-evidence-policy-20261009-65296619a374c325` |
| 부팅 준비·기존 업무 시각 검사 배포본 | `/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee` 유지 |
| 예약 업무 기본 배포본 | `/opt/marketdata/releases/scheduled-clock-policy-20261016-686bb70c9ee4ef11` (restic 소스만 아래 T18 실행본 사용) |
| daily variant | 위 배포본의 `daily/` — 기존 T13 원장·정책과 업무별 시각 연결 |
| backup variant | 위 배포본의 `preserve/` — 기존 T05 완료·순서 근거 유지 |
| restic 소스 | `/opt/marketdata/releases/restic-output-20261022-585c3e3686704e5c`의 `policy/run_job.py`·`preserve/` |
| restic Python | 기존 `scheduled-clock-policy-20261016-686bb70c9ee4ef11/preserve/.venv/bin/python` — 새 소스의 필수 의존성이므로 보존 |
| OI 3종·pack·network variant | 위 배포본의 `base/`, 공통 진입점은 `policy/run_job.py` |
| 설치 방식 | root 소유 코드·비 editable venv, `python -I`, 수집·예약 업무 프로세스는 `yoonhc` |
| 시스템 수집 유닛 | `marketdata-collector@depth.service`, `marketdata-collector@aggtrade.service` |
| 수집 수명 | 지속 supervisor 아래 3,600초 child, child 사이 15초도 시각 감시 유지 |
| child 내부 소켓 교체 | Depth `--rotate-seconds 900`, aggTrade `3600`. 소켓 교체와 파일 저장 경계는 같지 않음 |
| 시각 준비 | `chrony.service` → `marketdata-clock-bootstrap.service` → `chrony-wait.service`·수집 시작 검사 |
| 호스트 설정 | `/etc/marketdata/runtime.json`, `/etc/marketdata/clock-bootstrap.json`, `/etc/chrony/marketdata.conf` |
| 예약 업무 관리자·준비 | 8개 업무는 시스템 manager, `User=yoonhc`, `marketdata-jobs-clock-ready.service`. 600초 단독 차단 없이 유효한 관측·boot 준비와 실제 판단 경계 확인 |
| 이전 사용자 예약 유닛 | 8개 service·8개 timer는 persistent masked·비활성. user manager에서 다시 enable/start하지 않음 |
| 이전 사용자 수집 유닛 | `marketdata-depth.service`, `marketdata-aggtrade.service`는 masked·비활성 |
| 자동 checkpoint 정리 | `t05-recurring-retirement-20261018-e57a7995f03fb301` 고정 실행본. 시스템 timer·recurring 활성, 사용자 service/timer 두 mask·manual 비활성 |
| 현재 T05 scope | T18 승인 전환 후 `2875edd74ccef07cd4a1f09917d147ab0a73cde0a6921a5f3f7cd9930344ef83` |

개발 폴더 변경은 고정된 수집·daily·backup/restic 배포본에 자동 반영되지 않는다. 새 배포는 별도 경로에서 후보를 검증·고정하고
검토한 종료/기동 경계에서 적용한다. 설치된 venv나 고정 후보의 파일을 직접 덮어쓰지 않는다.
새 수집본의 [소스·해시](../reports/baseline/clock-runtime-transition-2026-10-09/collector-correction/release.json)와
[재현 절차](../reports/baseline/clock-runtime-transition-2026-10-09/reproduce.md)를 보존한다.
이전 부팅 준비본의 [소스·해시](../reports/baseline/system-runtime-bootstrap-revision-2026-10-07/release.json)도 유지한다.
기존 workspace의 pack·backup·restic과 새 수집본의 호환은 격리 저장소에서 검증했다.
workspace의 패키지와 설치된 collector가 항상 같은 버전이라고 가정하지 않는다.
daily도 별도 [고정 소스·검증 환경](../reports/baseline/t13-daily-review-2026-10-10/manifest.json)을 사용한다.
daily 전환에서는 다른 업무 경로를 유지했고, T05에서 backup/restic을 고정 근거 생성 wrapper로 전환했다.
T15에서는 T13/T05 실행본과 기존 시각 gate를 유지했다. T16은 그 업무 소스를 세 variant로 구분하고
새 공통 wrapper에서 판단 지점별 시각 근거·고정 날짜·요청 종결을 연결한다. 예약·자료 검증·잠금·보존 의미는 유지한다.
현재 bootstrap inventory/guard·T05 binding은 승인된 시스템 구성에 연결돼 있으며 기존 근거를 재등록하지 않았다.

T11은 supervisor 감시를 이어 다음 child의 reference 지연 대기를 줄였다.
WebSocket의 연속 수신은 아직 별도 과제다. Depth는 한 시간 child 경계 외에도 15분마다 기존 소켓을 닫고
새 소켓·snapshot epoch를 시작한다. 첫 child의 실제 보고서에도 소켓 4개와 예정 rotation 3개가 있다.
수신을 유지한 기록 구간 분리와 연결 교체 시 인계는 아직 구현하지 않았으며,
15초 대기를 0으로 바꾸거나 child의 수명만 늘리는 것을 연속 수집의 완료로 보지 않는다.

## 일상 조회

아래 명령은 현재 프로젝트 루트에서 실행한다. 현재 수집과 8개 예약 업무 모두 `--user` 없이 조회한다.
`--user`는 이전 유닛의 mask·중복 부재를 대조할 때만 구분해 사용한다.

```bash
systemctl status marketdata-clock-bootstrap.service marketdata-collector@depth.service marketdata-collector@aggtrade.service
journalctl -b -u marketdata-clock-bootstrap.service -u chrony.service --no-pager
journalctl -b -u marketdata-collector@depth.service -u marketdata-collector@aggtrade.service -n 100 --no-pager
systemctl list-timers --all 'marketdata-*'
systemctl status marketdata-clock-ready.service
systemctl status marketdata-jobs-clock-ready.service
systemctl list-jobs

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

T16은 8개 예약 업무의 공통 600초 gate를 전용 waiter와 업무별 구간 판단으로 바꿨다. 날짜·최초 실행 시각·냉각·선정 경계가
유효한 관측 구간 안에서 확정돼야 하며, 시각 대기와 기록/감시/프로그램 오류는 구분한다. 수집 supervisor 최초 시작·hard-fault 회복과
부팅 준비의 strict 정책은 변경하지 않았다. 어느 waiter든 임의로 stop해 의존 업무 요청을 취소하는 재시도 방법으로 쓰지 않는다.

업무 요청은 `state/scheduled-clock-policy/<job>/request.json`, attempt 이력과 decisions JSONL에 연결된다.
request의 complete는 해당 요청 종결이며 자료 품질 정상과 같지 않다. 파일 기록 실패 때 request가 이전 상태로 남을 수 있으므로
systemd 종료·JSONL·저장 보고서를 함께 확인한다. 10/16 OI 품질 결함은 이 방식으로 별도 승인 아래 복구했고 원래 실패를 보존했다. 보조 기록 전체의 백업/독립 복구는 별도 후속이다.

## 대상별 daily 운영

daily는 시스템 manager가 관리하며 T16의 전용 waiter·날짜/실행 가능/냉각 경계 판단을 사용한다.
`/etc/marketdata/daily-scheduler.toml`의 일정은 12:20 KST 현물 봉·펀딩·현물/선물 metadata,
18:00 선물 봉·마크·지수·OI 아카이브, 미완료만 20:00·22:00 재확인이다.
5분 timer는 실행 자격을 평가한다. 이미 완료된 acquire와 아직 예정 전인 작업에 HTTP 요청을 보내는 일정이 아니다.
refresh는 최근 3일 수정 확인으로 따로 식별하고, 미확보 UTC 날짜는 그 범위를 벗어나도 보존한다.

```bash
systemctl status marketdata-daily.service marketdata-daily.timer
journalctl -u marketdata-daily.service -n 100 --no-pager
marketdata_daily_release=/opt/marketdata/releases/scheduled-clock-policy-20261016-686bb70c9ee4ef11/daily
"$marketdata_daily_release/.venv/bin/python" -B -I -m marketdata.daily_schedule status --root "$marketdata_root"
```

최신 요약은 `state/daily-scheduler-latest.json`, 영속 작업·시도·냉각·재개 이력은 카탈로그의 daily 테이블이다.
기존 `daily-latest.json`을 새 scheduler의 최신 실행 결과로 읽지 않는다.
`runner_ok`는 실행기 정상 종료, `data_complete`는 계획된 자료의 완료이며 서로 다르다.
처리 0건과 oneshot의 `inactive/dead`는 직전 `Result=success`·종료 코드·원장과 함께 판단한다.
00:59 초기 관측은 실행 4회·기존 확보 156개·예정 전 20개·시도 0개였다. 이는 정오/저녁 자료 완료 근거가 아니다.
18:20 후속 확인에서는 실제 정오 10건·저녁 16건이 성공했고 188개 작업이 완료였다.
다음 KST 주기의 refresh/metadata와 UTC 날짜 변경의 신규 작업 때문에 `data_complete=false`가 될 수 있다.
기존 완료 acquisition의 취소와 구분하고, 작업·시도 ID와 해당 시점의 상태로 판단한다.

`needs_review` 재개는 [정확한 작업의 inspect → 요청 검토 → apply 절차](../reports/baseline/t13-daily-review-2026-10-10/deployment-review.md#실제-사용-절차)를 따른다.
이미 저장된 유효 근거와의 완료 연결 또는 추가 1–3회 시도 허용을 구분하며 과거 실패·냉각·검증 기준을 유지한다.
스케줄러 반복 실행 승인은 모든 점검 상태의 자동 해제 승인이 아니다. 현재 운영 수동 재개는 0건이다.
`plan()`은 운영 원장을 쓰므로 미리보기에는 [일관된 사본 절차](../reports/baseline/t13-daily-review-2026-10-10/deployment-review.md#미리보기)를 사용한다.

공개 HEAD 관측기는 `/etc/marketdata/daily-publication-probe.toml`의 `enabled=false`를 유지한다.
승인된 일시 보류에서 daily의 향후 예약만 멈추려면 `sudo systemctl stop marketdata-daily.timer`를 사용하고,
진행 중 daily는 정상 종료를 확인한다. 재부팅 뒤까지 예약을 바꾸는 disable/mask는 별도 구성 변경이다.
두 수집기·backup/restic·Chrony를 함께 중지하지 않는다.
현재 manager 원복은 [T15 고정 인계 계획·미전달 요청 대조](../reports/baseline/scheduled-system-handoff-2026-10-12/deployment-review.md)를 따른다.
과거 T13의 사용자 base 링크 복원 절차를 현재 시스템 구성에 곧바로 적용하지 않는다.
새 원장·자료·revision·실패를 보존하며, 더 이전 scheduler로 돌아가는 경우 냉각/needs_review 제한을 별도로 확인한다.

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
이는 10/9 수집 정책 전환 당시의 절차다. 10/13 T15 이후 bootstrap/연결 설정이 바뀌었으므로
과거 계획을 현재 구성에 바로 재사용하지 않고 T15 연결과 현재 파일/ledger를 함께 대조해야 한다.
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
격리 시험에 이어 10/10 예약 checkpoint `4381041b…` → restic snapshot `764f183c…` → 전체 읽기 검사 →
별도 경로 전체 추출·프로그램 복원을 완료했다. 첫 종료 collection 두 개의 artifact 32개와
조회 7,111개 구간이 고정 기준과 일치했다. 정확한 ID·해시·범위는 위 운영 검증 보고서를 따른다.
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
자동 checkpoint 정리는 별도 검증·승인한 시스템 timer에서 매번 새 계획으로 평가한다.
15:40 KST·Persistent=false·Restart=no와 기존 보존 조건을 유지하며 완료한 수동 계획은 재사용하지 않는다.
10/25 복원 직후 16:02의 시각 자격 검사 failed/1은 삭제 진입·삭제 0개로 보존한다.
10/26 자연 예약의 실제 결과는 별도 관찰이며 `Persistent=false`만으로 모든 재활성화 요청을 배제한다고 가정하지 않는다.

T18은 성공한 정확한 `/backup.json` dump를 매번 조회·검증한 뒤 같은 본문의 할당만 공유한다.
각 출력 경로·명령 receipt를 유지하며 실제 조회 시각은 공유 inode의 mtime 대신 receipt로 판단한다.
두 자연 no-op의 큰 본문 추가 할당은 0 B였지만 임시 쓰기·반복 조회·일반 기록 증가가 없어진 것은 아니다.
과거 출력의 정확한 두 승인 묶음 회수 외에는 일괄 삭제·압축·이동하지 않는다.

T05 근거 생성은 **실제 checkpoint 완료 표시 확정 뒤** 새 시각 관측으로 완료 상한을 기록하고,
backup ID·manifest·실제 생성 선후관계를 `checkpoint-evidence.json`과 연결한다.
유효한 데이터 백업 성공과 시각 근거 미확보는 별개이며, 근거가 없으면 삭제 판단에서 추가 보호한다.
기존 7개 사본은 원래 completed_at을 바꾸지 않고 10/11의 첫 유효 사후 상한을 등록했다.
10/12 첫 정기 사본 `fd4bf841…`의 완료 직후 상한 **14:21:46.296229 KST**와 생성 순서를 확인했다.
같은 restic snapshot `616ed4d6…`에서 baseline·기존 사본 7개·신규 1개의 근거를 복원하고,
원래 registry·선행 checkpoint에 의존하지 않는 조회를 [검증](../reports/baseline/t13-operating-completion-2026-10-12/summary.md#같은-복원의-t05-판정)했다.
10/13에는 첫 system 정기 checkpoint `10dd2182…`의 완료 직후 상한 **14:26:29.724714 KST**와
기존 baseline·10/12 사본의 후행 관계가 이어졌다. restic `a2dbcdd4…`의 전체 검사와
객체 10개의 근거 bundle 조회도 [확인](../reports/baseline/scheduled-system-operating-completion-2026-10-13/summary.md)했다.
위 10/13 확인 범위의 신규 후행 사본은 **2개**다. 이후 축적 상태와 기간·당일 보존·정확한 삭제 승인 여부는 [T05 상태](../reports/baseline/t05-evidence-deploy-2026-10-11/status.md)와 [활성 TODO](../TODO.md)를 따른다.

삭제 판단은 T05의 U 5초 상한과 `관측 하한−완료 상한 ≥ 72시간+60초`, 확실히 후행인 검증 사본 3개,
같은 사본의 restic 보존·검사·기존 잠금/승인 조건을 함께 따른다. reference age 600초는 T05 판단에 사용하지 않는다.
T16 적용 뒤 backup/restic의 **기동 전 전용 waiter**는 reference age 600초만으로 막지 않는다. 유효한 현재 관측·부팅 준비와 각 업무 판단 경계가 충족되지 않으면 계속 보류한다.
근거 생성 기능 설치를 자동 삭제 승인이나 다른 8개 업무의 시각 정책 변경으로 해석하지 않는다.
[정책 설계](t05-checkpoint-clock-safety-design.md)와
[T05 반복 자동화 중지·원복 계약](../reports/baseline/t05-recurring-deploy-2026-10-18/operation-review.md)을 보존한다.
T18에서 binding·scope가 바뀌었으므로 실제 변경 시에는 위 현재 중지·원복 연결과 현재 파일/ledger를 대조한다.
과거 설치 계획의 승인·scope를 현재 구성에 그대로 재사용하지 않는다.
