# 상시 스트림과 자동 유지보수

BTCUSDT USD-M 선물의 aggTrade와 비 RPI depth를 기존 캔들·OI 수집 및 Freqtrade와 별도로 실행한다.
원문·표준 데이터는 기간 제한 없이 보존한다.

**2026-10-09 현재 수집기는 고정 배포본의 시스템 서비스로 전환했다.** 현재 상태 조회·종료·되돌리기는
[시스템 수집 서비스 운영](system-runtime-operations.md)을 따른다. 기존 사용자 수집 두 개는 masked이며,
유지보수·OI·daily·restic 등 기존 사용자 예약 업무는 시각 준비 검사와 함께 유지한다.
아래는 9/23 사용자 배포의 설정과 연속성·유지보수 설명이다. 옛 사용자 수집의 설치/기동 명령을
현재 시스템 수집과 함께 실행하지 않는다.

## 기존 사용자 배포의 실행 구성

| 유닛 | 실행과 범위 |
|---|---|
| `marketdata-aggtrade.service` | 3,600초 세션, 5초 flush, 마지막 저장 ID부터 다음 세션 재개 |
| `marketdata-depth.service` | 3,600초 세션, 5초 flush, 900초 연결 회전, 10초마다 독립 top 20 대조 |
| `marketdata-pack.timer` | 매시 UTC 15분, 활성화 후 5분에도 실행. 종료 후 1시간 지난 수집만 묶음 |
| `marketdata-backup.timer` | 매일 UTC 04:10 / 한국 시각 13:10에 전체 검증·백업 |
| `marketdata-network-observer.timer` | 매분 20초에 링크·gateway·이름 해석·외부/거래소 접속 관측 |

스트림은 세션 종료 후 15초 뒤 새 프로세스로 재시작한다. `SESSION_SECONDS=3600`은 서비스
환경 변수다. 정확한 정시 분할이 아니라 **실행 시작부터 1시간**이며, 요청·flush·종료 처리로
실제 실행 시간이 조금 길어질 수 있다. 짧은 검증에서는 임시 runtime override로 60초를 사용했다.

서비스의 `Restart=always`가 정상 종료와 실패 모두 재시작한다. 중지는 `SIGINT`로 flush와
최종 보고서 작성을 요청하고 최대 120초 기다린다. 강제 종료까지 가면 다음 시작 시 이전
`running` 수집을 `interrupted`로 표시한다. 종료·실패 이력은 숨기거나 삭제하지 않는다.

WebSocket 429/418은 응답 헤더와 cooldown을 `state/*-ws-cooldown.json`에 기록한다.
각각 최소 5분/1시간과 더 긴 `Retry-After`를 적용한다. 서비스가 다시 시작돼도 대기 기간에는
접속하지 않는다. REST의 기존 cooldown도 유지한다. 일반 연결 장애는 수집기의 제한된
재시도 후 종료하며 systemd가 다시 시작한다. 디스크 여유 공간은 기본 10 GiB를 남긴다.

## 연속성의 범위

- aggTrade는 이전 WebSocket 세그먼트의 마지막 ID부터 이어받는다. 세션 사이 누락은 REST로
  최대 20페이지를 조회해 복구한다. 복구 한도를 넘기거나 조회가 실패하면 해당 범위를
  `unrepaired_gaps`와 별도 REST 수집 이력에 남긴다. 이후 세션이 정상이어도 과거 오류는 남는다.
- 최초 상시 수집 전에는 `--fresh-start`로 새 관측 범위를 명시적으로 시작했다. 지난 실험과
  시작점 사이의 미수집 ID 범위를 보고서에 보존했다. 서비스 설정에는 `--fresh-start`가 없다.
  장기 중단 뒤 이 옵션을 자동으로 사용해 공백을 없던 일로 처리하지 않는다.
- depth는 매 연결마다 새 REST snapshot과 WebSocket 증분을 연결한다. 15분 회전 및
  시간별 세션 전환·장애 사이의 호가 이벤트 공백은 복구할 수 없다. 저장 구간의 `pu` 연결과
  snapshot 대조를 검증한 것이며, 24시간 무손실 호가 기록을 의미하지 않는다.
  알려진 가격 범위가 소진되면 같은 소켓에서 새 epoch와 snapshot을 시작한다.
  실패한 epoch는 보존하며, REST가 뒤처져 ID를 연결할 수 없으면 계속 불완전으로 표시한다.
  이때 소켓의 미복원 증분은 계속 저장한다. 새 재시도 정책은 최초 1초 뒤, 연속 실패 시
  2·4·8·10초 간격으로 늘리며 한 세션의 최근 60초에 추가 재시도 8회를 넘기지 않는다.
  정상 동기화·새 소켓에서 실패 대기는 초기화하지만 요청 예산은 유지한다. 대기 중에도
  수신·저장은 계속하며, 재시도 직전 flush 이후 종료·회전 시각을 다시 확인한다.
  실제 요청 시각은 보고서에 남고 과거 실패·미복원 계수는 복구 후에도 보존한다.
  [재시도 정책과 기록](orderbook-depth.md#동기화와-공백)
  새 연결에서는 실제 첫 증분을 먼저 받아 보존한 뒤 즉시 첫 snapshot을 요청한다(수신 대기 최대 2초).
  초기 요청 순서 때문에 발생하던 실패를 줄이는 조치이며, 최초 snapshot 이전이나 연결
  교체 경계의 호가장을 복구한다는 뜻은 아니다. [초기 버퍼 정책](orderbook-depth.md#동기화와-공백)
  새 수집은 제한된 최근 이벤트 버퍼를 같은 소켓에서만 유지해 뒤처진 검증 snapshot과
  재동기화 anchor를 정렬한다. 검증 snapshot은 같은 ID의 상위 호가가 일치할 때만 갱신하고,
  새 연결에서는 버퍼를 비운다. 이전 실패 판정은 보존한다.
  [최근 이벤트 정책과 한도](orderbook-depth.md#검증과-저장)
  [과거 확장 시 점검한 복구 범위](history-extension.md)를 참고한다.
- 호스트 종료·절전 중에는 수집할 수 없다. 기존 linger 설정은 로그아웃 후 실행을 유지한다.

새 호가 수집은 가격 여유·호가 개수 감소를 감지해 정기 검증 전에 snapshot을 요청한다.
최소 1초 간격·수집 세션의 최근 60초 추가 요청 12회 상한을 적용하며, 상위 20호가가 부족해진
구간은 `partial`로 보존한 뒤 같은 소켓에서 재동기화한다.
[조기 갱신 정책과 범위](orderbook-depth.md#검증과-저장)를 참고한다.
별도 [네트워크 관측기](network-observer.md)는 1분 간격의 진단 자료만 남기며 수집 서비스나
DNS 설정을 바꾸지 않는다. 수집기 정상 여부와 네트워크 표본의 결과는 별개로 확인한다.

## 유지보수 순서와 보존

`marketdata maintenance --mode hourly`는 실제 변경을 수행한다. 계획만 확인할 때는 기존
`pack-events`·`pack-prune` 명령에서 `--apply`를 생략한다.

1. 유지보수 전용 잠금으로 hourly/daily 작업을 직렬화한다.
2. 이전 실행에서 묶음 등록 후 남긴 개별 사본을 최대 32개 묶음까지 검증·정리한다.
3. 종료 후 1시간이 지난 수집의 최대 20,000개 후보 파일을 64 MiB/1,024개 단위로 묶는다.
4. 새 묶음 전체와 멤버 및 개별 사본의 해시를 대조한 후 중복 사본만 정리한다.
5. daily 모드에서는 전체 저장 자료를 검증하고, 통과한 경우 일관된 카탈로그와 파일을 백업한다.

이미 정리를 마친 모든 과거 묶음을 매시간 다시 해시 검사하지 않는다. 전체 검사는 daily에서
수행한다. 캔들·OI 파일을 묶는 기능은 아직 없으며, 이번 자동화 대상은 aggTrade·depth다.
백업과 일반 수집은 동시에 가능하고, 파일 배치 변경은 기존 잠금으로 보호한다.

현재 자동 백업은 두 번째 SSD의
`/mnt/backup/marketdata-historical/automatic/<UTC 날짜>/<시도 ID>/`에 생성한다.
서비스는 `--incremental --backup-dir ... --backup-mount /mnt/backup`을 사용한다.
완료 표시와 파일·카탈로그 검증을
통과한 당일 사본이 있으면 재사용해 재시도마다 전체 사본을 만들지 않는다. 중단된 불완전 사본은
보존하고 새 시도로 진행한다. 성공한 사본의 손상은 오류로 보고한다. **백업도 자동 삭제하지 않는다.**
첫 사본은 전체 복사하고 이후 날짜에는 이전 백업의 동일 파일을 하드링크로 재사용한다.
카탈로그는 매번 독립적으로 복사하며 원본·복원본과는 하드링크를 공유하지 않는다.
기존 로컬 전체 사본은 보존한다. 목적지 미지정 CLI의 기본 경로는 `backups/automatic/`이며,
명시한 외부 목적지나 마운트가 없으면 로컬로 대체하지 않고 실패한다.
[공유 파일 손상의 범위·복원·이식 방법](backup-and-storage.md#증분-저장과-외부-디스크)을 참고한다.
별도 장비·원격 사본은 아직 설정하지 않았다.

두 유지보수 서비스는 `--wait-for-lock`으로 실행한다. 겹치면 후발 프로세스가
`waiting_for_maintenance_lock`을 journal에 남기고 같은 프로세스에서 잠금을 기다린다.
잠금을 얻은 뒤 실제 작업을 수행하며, 정상적인 대기는 실패 재시도 횟수를 소모하지 않는다.
대기 중에는 수집용 writer·파일 배치 잠금이나 DB 트랜잭션을 보유하지 않는다.
동일 서비스가 진행 중일 때 타이머가 다시 만료돼도 프로세스를 추가하지 않는다.

서비스의 `TimeoutStartSec=6h`는 대기와 실제 작업을 합친 제한이며 `SIGINT`로 대기를 중단할
수 있다. CLI에서 직접 이 옵션을 쓰면 자체 시간 제한은 없으며, 옵션을 생략하면 잠금 충돌을
즉시 오류로 보고한다. 검증 실패·디스크 오류·시간 초과 등 실제 실패는 기존대로 120초 후
재시도하며 1시간에 4회로 제한한다. 파일 복사 중에는 수집기의 writer를 놓는다.
공간/inode 부족 시 데이터 자동 삭제 대신 오류로 보고한다.
감시·경보는 로컬 상태와 journal 범위이며 외부 메시지를 보내지 않는다.

## 기존 사용자 배포의 확인과 중지

```bash
systemctl --user status marketdata-aggtrade.service marketdata-depth.service
systemctl --user list-timers --all 'marketdata-*'
.venv/bin/marketdata aggtrade-status
.venv/bin/marketdata depth-status
.venv/bin/marketdata maintenance-status
.venv/bin/marketdata status --table

# 수동 유지보수: 같은 서비스가 실행 중이면 중복 시작하지 않는다.
systemctl --user start marketdata-pack.service
systemctl --user start marketdata-backup.service

# 스트림과 유지보수 자동 실행 중지. 기존 OI·일별 타이머는 별도다.
systemctl --user disable --now marketdata-aggtrade.service marketdata-depth.service \
  marketdata-pack.timer marketdata-backup.timer
systemctl --user stop marketdata-pack.service marketdata-backup.service
```

`state/maintenance-{hourly,daily}-latest.json`은 최신 상태이고 전체 실행 기록은
`reports/maintenance/<UTC 날짜>/<실행 ID>.json`이다. 실패 지점과 오류도 남는다.
잠금 대기 중에는 이 파일들을 덮어쓰지 않는다. 따라서 대기는 journal과 systemd의
`activating` 상태에서 확인하고, 기존 최신 보고서를 대기 중인 작업의 완료로 해석하지 않는다.
잠금 획득 후 생성되는 보고서의 `requested_at`은 요청 시각, `started_at`은 작업 시작 시각,
`lock_wait_seconds`는 단조 시계로 측정한 잠금 획득 소요 시간이다.
`wait_for_lock`은 대기 옵션 사용 여부다.
`maintenance-status`는 마지막 성공 상태와 시작 시각 기준 hourly 3시간/daily 36시간 이내인지,
원본과 설정된 백업 목적지의 디스크 여유 10 GiB 및 free inode 5% 이상인지를 확인한다.
외부 마운트 가용성도 점검한다. 미실행·실행 중·오류·기한 초과는
종료 코드 2다. 스트림 상태와 일반 데이터의 누락 상태는 위의 별도 명령으로 함께 확인한다.

위 사용자 배포를 재현할 때는 [유닛 파일](../integrations/systemd/README.md)의 당시 구성을 참고한다.
새 VM 이전에는 현재의 고정 시스템 배포본·머신 설정과 실제 권한/시각 검증을 사용한다. 기존 검증 결과는
[2026-09-23 운영 전환 기록](../reports/baseline/automation-2026-09-23/summary.md)을 참고한다.
