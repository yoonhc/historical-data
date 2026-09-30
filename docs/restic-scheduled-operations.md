# restic 병행 예약 운영과 날짜별 관찰

2026-09-27에 `marketdata-restic-archive.timer`를 사용자 서비스로 적용했다.
[적용 기록](../reports/baseline/restic-timer-2026-09-27/summary.md)과
[기존 도구의 저장·복원 계약](restic-archive.md)을 함께 참고한다.
기존 daily·수집 유닛은 그대로 두며 삭제·forget/prune·기존 백업 대체는 수행하지 않는다.

[9/28 중간점검](../reports/baseline/restic-observation-2026-09-28/summary.md)에서 반복 예약의 판별을
현재 Job 기반으로 수정하고 새 운영 스냅샷의 수동 복원 시험을 마쳤다. 해당 시점 날짜 집계는
0/3이며, 이후 진행은 아래 상태 조회로 확인한다.

## 설치 구성과 실행 주체

- [서비스](../integrations/systemd/marketdata-restic-archive.service)와
  [타이머](../integrations/systemd/marketdata-restic-archive.timer)는 `~/.config/systemd/user/`에 설치한다.
  타이머만 enable한다. oneshot 서비스는 실행이 끝나면 inactive로 돌아가는 것이 정상이다.
- 실행 주체는 사용자 서비스 관리자 소유자인 `yoonhc`, UID 1000이다. 유닛에 별도 `User=`를
  지정하지 않는다. 현재 `loginctl show-user yoonhc -p Linger`는 `Linger=yes`다.
- `Linger=yes`는 부팅 시 사용자 관리자를 띄우고 로그아웃 뒤에도 유지하는 구성이다.
  [systemd 255 공식 설명](https://github.com/systemd/systemd/blob/v255/man/loginctl.xml)
  재부팅·로그아웃 시험을 수행했다는 뜻은 아니다. 이번 적용 중 두 시험은 하지 않았다.
- 호스트별 Python 실행 파일·프로필·모듈 경로는
  [환경 파일 예시](../configs/restic-service.example.env) 형식으로
  `~/.config/marketdata/restic-service.env`에 0600으로 보관한다. 실제 암호 값은 넣지 않는다.
  작업 디렉터리는 사용자 홈이며 모든 저장 경로는 프로필의 절대 경로를 사용한다.
- 마운트는 호스트가 준비한다. 현재 `/etc/fstab`에 백업 볼륨 UUID가 등록되어 있으며 서비스는
  실행마다 필수 마운트·UUID·저장소 ID를 다시 검사한다. 빠진 마운트에서 로컬 폴더로 대체하지 않는다.

타이머는 활성화 5초 후 첫 탐색(`OnActiveSec=5s`), 이후 매시 **UTC 05분·35분**에 탐색한다.
KST도 매시 05분·35분이며 `Persistent=true`, `AccuracySec=1s`다. 활성화 직후 대기 사본을
한 번 찾고 이후 30분 주기를 따른다. `Persistent`는 놓친 탐색을 따라잡기 위한 설정이고, 활성화
탐색이나 처리 0개 실행을 새 백업 성공으로 계산하지 않는다. 실행 중인 서비스가 있으면 중복 기동하지 않는다.
[systemd 255 타이머 동작](https://github.com/systemd/systemd/blob/v255/man/systemd.timer.xml)

실행 우선순위는 nice 10·I/O best-effort 7, restic 동시성 2다. 작업 제한은 6시간이며 종료 신호는
SIGINT, 정리 대기는 120초다. 즉시 재시작 반복은 없고 다음 탐색 또는 명시적 수동 실행에서 재시도한다.
관찰 복원 실패는 아래 별도 규칙을 따른다.

## 실제 예약 성공의 집계

[restic_schedule.py](../scripts/restic_schedule.py)가 기존 `archive_backups.execute`를 호출하고
원래 백업 상태와 별도로 `observation.json`을 기록한다. 관찰 시작 시 이미 있던 사본 ID를 제외하며,
기준 시각보다 뒤에 생성된 checkpoint만 후보로 삼는다. 관찰 초기화는 한 번만 허용한다.

날짜 하나가 완료되려면 다음 증거가 모두 필요하다.

1. `INVOCATION_ID`·현재 PID·oneshot 서비스 유형을 관리자에서 대조한 뒤, **현재 실행 중인 start
   Job**의 `ActivationDetails`가 이 타이머를 가리키는지 확인한다. Job의 서비스·상태를 확인하고,
   Job에 기록된 realtime/monotonic trigger 시각을 타이머의 D-Bus 속성과 각각 대조한다.
2. 해당 실행에서 새 checkpoint를 실제 저장해야 한다. 종료 코드 0인 저장 시도·전체 snapshot ID·
   실제 업로드 시각이 그 실행의 시작/종료 구간과 일치해야 한다.
3. 같은 예약 실행에서 수행한 `check --read-data`가 성공하고 그 ID가 검사 목록에 포함되어야 한다.
4. checkpoint 기준 UTC 날짜가 기존 완료 날짜와 달라야 한다. 같은 날짜의 재시도·사본 여러 개는
   그 날짜 한 번만 센다. 지연 사본을 나중에 처리해도 날짜는 업로드 날짜로 바꾸지 않는다.

수동 CLI·수동 `systemctl start`·처리 0개·기존 성공 재조회·오래된 전체 검사 기록은 집계하지 않는다.
현재 Job 증거가 누락되거나 대조되지 않으면 자동으로 예약 성공이라 추정하지 않는다. 보관 자체는
수행할 수 있지만 해당 실행은 집계에서 제외하고 사유를 남긴다. 상태 조회의 `last_run`에도 판별 결과와
오류를 표시한다. 환경변수 `TRIGGER_*`는 진단 증거로 보존하되 집계 근거로 사용하지 않는다.

2026-09-28 중간점검에서 이 호스트의 systemd 255가 최초 타이머 실행의 `TRIGGER_*` 값을 이후
실행과 수동 실행에도 재사용하는 것을 확인했다. 기존 코드는 현재 타이머 시각과 다른 환경변수를 보고
집계를 보수적으로 제외했으나, 이 때문에 새 daily 저장 후 복원 관찰이 시작되지 않았다. 단순히 시각
검사를 제거하지 않고, 매 실행마다 새로 만들어지는 Job의 증거를 사용하도록 수정했다.
근거는 [v255 Unit의 이전 activation details 유지](https://github.com/systemd/systemd/blob/v255/src/core/unit.c#L1820),
[타이머가 현재 Job에 trigger 시각을 전달하는 코드](https://github.com/systemd/systemd/blob/v255/src/core/timer.c#L561),
[Job의 D-Bus ActivationDetails](https://github.com/systemd/systemd/blob/v255/src/core/dbus-job.c#L126)와
별도 시험 타이머의 반복 실행·수동 실행 검증이다. 9/28 13:35의 원래 영수증은 그대로 남기며,
이미 사라진 Job 증거를 실행 시각만으로 재구성해 날짜별 성공에 소급 집계하지 않는다.

관찰은 **서로 다른 새 checkpoint 날짜 3개**와 **그 기간의 새 snapshot 복원 1개**를 별도로 추적한다.
실행 횟수·날짜별 증거·복원 결과는 분리한다. 3개 날짜가 찼어도 복원 검증이 미완료이면 전체 관찰은
미완료다. 전체 관찰 완료 뒤에도 타이머의 병행 보관은 계속되며, 기존 백업 전환이나 사본 삭제를 시작하지 않는다.

## 새 스냅샷의 한 차례 복원 시험

첫 집계 대상 날짜의 새 snapshot을 선택하고 **64자리 전체 ID를 observation에 먼저 기록**한다.
`latest`를 사용하지 않는다. 같은 서비스가 기존 `restore-check --snapshot-id <선택한 ID>`와 동일한
함수를 호출해 다음을 수행한다.

1. 새 독립 디렉터리에 restic 추출, 원래 manifest·파일·카탈로그·pack 검증.
2. 프로그램의 복원 절차로 별도 store root 생성.
3. 표본 조회 대조 및 복원 저장소 전체 무결성 검증.
4. 그 store 전용의 새 폴더에 독립 첫 백업 생성, 새 백업 검증과 inode 분리 확인.

이전 manifest/source_root를 수정하지 않으며 기존 출처 검사도 유지한다. 복원·재백업은
`restore_work_root` 아래 새로운 UUID 디렉터리에 만들어 기존 사본을 덮어쓰지 않는다.
복원 프로그램이 수집 서비스를 시작하지 않는다. 시작 전 세 사본 분량과 여유 공간을 검사한다.

자동 복원 시도는 **한 번**이다. 실패·중단하면 증거와 작업 공간을 보존하고 매 30분마다 새 대용량
복원 사본을 반복 생성하지 않는다. 다음 실행이 완료된 복원 영수증을 발견하면 같은 ID의 실제 결과로
상태를 복구한다. 그렇지 않으면 실패/중단 상태로 남는다. 필요하면 원인 확인 후 같은 명시적 ID로
수동 복원 시험을 다시 수행할 수 있으며, 이후 예약 실행이 성공 영수증을 관찰에 연결한다.
이 제한은 날짜별 새 사본의 병행 보관을 중지하는 제한이 아니다.

날짜별 집계와 독립적으로 관찰 기간의 새 운영 스냅샷을 명시적으로 복원하려면 다음 명령을 쓴다.
원래 관찰 시작 뒤 생성되고, 이 scope에서 저장·전체 검사된 스냅샷만 선택할 수 있다. 선택 ID와 수동
시도를 실행 전에 기록하고 동일한 복원 절차를 수행한다. **이 명령은 날짜별 성공 횟수를 올리지 않는다.**
이미 통과한 복원은 다시 만들지 않으며, 다른 ID로 기존 선택을 바꾸지 않는다.

```bash
.venv/bin/python -m scripts.restic_schedule \
  --profile /home/yoonhc/.config/marketdata/restic-archive.toml \
  restore-check --snapshot-id <64자리-ID>
```

## 상태와 증거 경로

현재 호스트에서 다음 명령을 사용한다.

```bash
systemctl --user list-timers marketdata-restic-archive.timer --all
systemctl --user show marketdata-restic-archive.service -p Result -p ExecMainStatus -p InvocationID
journalctl --user -u marketdata-restic-archive.service --since today --no-pager
```

프로젝트 루트에서 보관 상태와 날짜별 관찰을 함께 조회한다. 이 조회는 기록을 새 성공으로 갱신하지 않는다.

```bash
.venv/bin/python -m scripts.restic_schedule \
  --profile /home/yoonhc/.config/marketdata/restic-archive.toml status
```

- `archive`: 기존 보관 도구의 현재 입력·대기·오류·마지막 저장/검사 기록.
- `observation.completed_dates`와 `date_evidence`: 실제 증거로 확정된 날짜 개수와 ID·실행/검사 시각.
- `observation.restore`: 선택한 전체 ID, 진행/성공/실패 상태, 복원 조회·새 백업 검증 근거.
- `observation.complete`: 날짜 3개와 복원 검증을 모두 충족했는지.

실행 영수증은 `<reports_root>/scheduled/<실행 ID>.json`, 관찰 상태는
`<state_root>/<repo ID>/<scope>/observation.json`이다. 각각 원래 archive 실행 ID 및 systemd 실행 ID와
연결된다. 원래 입력 검증·restic stdout/종료 코드·수집 관측 로그도 기존 reports_root에 계속 남는다.
실패하거나 기록이 불완전하면 추정한 성공을 채워 넣지 않는다.

## 새 예약만 중지하는 롤백

```bash
systemctl --user disable --now marketdata-restic-archive.timer
```

이 명령은 새 restic 예약만 중지한다. 이미 실행 중인 보관·복원 서비스는 현재 작업을 끝까지 수행한다.
기존 daily·수집 타이머와 데이터·사본·상태 기록·설정 파일은 유지한다. 이 롤백 명령은 적용 시험 중
실행하지 않았으며, 필요할 때 사용할 절차로 제공한다.

다시 시작하려면 다음 명령을 사용한다. 기존 observation을 초기화하거나 증거를 지우지 않는다.

```bash
systemctl --user enable --now marketdata-restic-archive.timer
```

Proxmox VM에서는 새 사용자·코드/Python 경로·마운트/UUID·저장소 ID·암호 접근·linger를 확인한 뒤
호스트 설정을 맞춘다. 실제 새 root로 복원했다면 새 전용 백업 폴더와 archive_scope를 사용한다.
현재 머신의 설치 검증은 미래 VM의 부팅/마운트/권한 시험을 대신하지 않는다.
