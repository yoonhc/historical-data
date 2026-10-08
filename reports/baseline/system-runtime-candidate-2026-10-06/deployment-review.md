# 검토할 운영 전환 범위와 되돌리기

대상 후보: **system-runtime-20261006-c09bafd0645578a3**.
현재 상태는 후보·격리 검증 완료이며, 아래 설치·서비스 변경·시각 보정은 실행하지 않았다.
이 문서는 명령을 무조건 한 번에 실행하는 설치 스크립트가 아니다. 각 단계의 실제 결과를 기록하고
선행 조건이 맞지 않으면 다음 단계로 넘어가지 않는다.

## 설치할 것과 유지할 것

| 항목 | 제안 적용 |
|---|---|
| 고정 코드/venv | `/opt/marketdata/releases/system-runtime-20261006-c09bafd0645578a3` |
| 수집 프로필 | `/etc/marketdata/runtime.json` — [확정 경로의 후보](machine-profile.json) |
| NTP 설정 | `/etc/chrony/marketdata.conf` — [내용](release/integrations/runtime/chrony.conf) |
| Chrony drop-in | `/etc/systemd/system/chrony.service.d/marketdata.conf` — [내용](units/chrony-override.conf) |
| 시스템 수집 유닛 | `/etc/systemd/system/marketdata-collector@.service` — [내용](units/marketdata-collector@.service) |
| 시각 근거 | 기존 data root의 `state/runtime-clock/{depth,aggtrade}` |
| 수집 계정 | `yoonhc`/UID 1000; 새 root 수집 프로세스 없음 |
| 원본·카탈로그·source_root | 기존 경로 그대로 |
| 기존 daily/restic/정리 grant | 코드·설정·권한 범위 유지; 전환 중 예약 보류/재개는 따로 기록 |

Chrony는 호스트에 **미설치**다. 검증한 패키지는 Ubuntu noble용 `4.5-1ubuntu4.2 amd64`,
SHA256 `434f9fda4bf0de2d0c89c132a0b2c1ddda2fdd50c2b0338e1422d015c45d72d2`다.
현재 OS는 Linux Mint 22(기반 Ubuntu noble), systemd 255다. 실제 설치 시 버전이 바뀌면
설정·서비스·CSV 접점을 그 버전으로 다시 확인한다. `apt install`의 time-daemon 충돌로
timesyncd가 교체될 수 있으므로 패키지 설치부터 운영 전환으로 취급한다.

## 1. 중단 전 준비

1. `release.json`의 전체 source identity와 `source.tar.gz` 해시를 확인한다. 새 경로에 추출하고
   파일별 SHA256도 대조한다. 검증된 Python 3.12.3·uv 0.12.3·setuptools 83.0.0을 사용한다.
2. 새 release 경로에서 `uv sync --frozen --no-editable --no-dev --link-mode copy`를 실행한다.
   다른 경로에서 만든 venv를 복사하지 않는다. 의존성 다운로드 실패는 여기서 해결한다.
3. 그 release의 `.venv/bin/python -I integrations/runtime/seal_runtime.py`로 설치 receipt를
   생성한다. marketdata import가 새 venv 내부인지, runtime 파일·의존성 버전이 검증 기준과
   일치하는지 확인한다. 코드·venv는 root 소유로 두고 수집 계정이 수정하지 못하게 한다.
4. 프로필과 Chrony 설정은 root 소유 일반 파일로 설치 준비한다. 비밀값은 없다.
   `chrony_config_sha256`를 그대로 확인하고 실제 release 경로·UID·데이터 UUID를 재확인한다.
   `state/runtime-clock`은 yoonhc 소유 0700으로 미리 만든다. systemd ReadWritePaths는 없는
   경로를 자동으로 만들어 주는 기능으로 취급하지 않는다.
5. 기존 사용자 수집 서비스 파일·enabled 상태·PID·invocation, timesyncd 설정·RTC LOCAL 상태,
   retirement 타이머 상태·grant 해시, 실행 중 daily/restic/정리 작업을 기록한다.
   grant 기준 SHA256은 [validation.json](validation.json)에 있다. 암호 파일은 복사·출력하지 않는다.
6. `systemd-analyze verify`를 실제 설치 경로와 최종 설정으로 다시 수행한다. 후보에서 완료한 것은
   tmp 실행 경로의 구문 검사이며, 아직 존재하지 않는 `/opt` 경로의 실행 가능성까지 확인한 것은 아니다.

## 2. 승인한 전환 창에서만 진행

다음 조작은 운영 중지 범위와 예상 수집 공백을 승인한 뒤 수행한다. 지금 실행한 작업이 아니다.

- `systemctl --user disable --now marketdata-checkpoint-retirement.timer`로 새 자동 삭제를 막는다.
  서비스가 이미 실행 중이면 자연 종료와 journal 완료를 기다린다. 타이머 중지만으로 실행 중인
  삭제가 멈췄다고 판단하지 않는다. 진행 중인 정리를 임의로 kill하지 않는다.
- 두 사용자 수집 서비스를 `disable --now` 한다. 종료 보고서·저장 완료 범위·남은 PID/cgroup과
  stream lock 해제를 확인한다. 기존 interrupted/partial을 정상으로 고쳐 쓰지 않는다.
  사용자 관리자와 시스템 관리자의 `Conflicts=`가 서로 연결된다고 가정하지 않는다.
- 패키지 설치 전에 **시스템 관리자**의 Chrony/chronyd/chrony-wait에 임시 runtime mask를 걸어
  패키지가 기본 `makestep`/RTC 설정으로 자동 시작하지 못하게 한다. 새 설정과 drop-in을 설치한
  뒤 실제 유효 ExecStart·config를 확인하고 이 임시 mask만 해제한다. 기존 mask가 있었다면 그
  이유를 먼저 확인하며 일괄 unmask하지 않는다. `policy-rc.d` 전역 파일을 덮어쓰지 않는다.
- timesyncd와 Chrony가 동시에 시계를 조정하지 않게 한다. 현재 유효 설정과 서비스 상태로 확인한다.
  배포판의 Chrony sandbox/AppArmor를 유지한다. 설정을 `/etc/chrony/marketdata.conf`에 두므로
  AppArmor 규칙을 완화할 필요가 없다.
- Chrony를 시작하고 배포판 chrony-wait와 후보의 **현재 상태** 준비 검사를 통과하는지 확인한다.
  시간원 주소·reference age·남은 보정량·조건부 오차·invocation을 기록한다. timeout을 성공으로
  간주하거나 기준을 임시 완화하지 않는다. 조건이 맞으면 시스템 수집 두 instance를 enable/start한다.
- 기존 사용자의 두 자동 시작이 꺼져 있고, 새 시스템 서비스가 각 한 개의 child만 실행하는지
  확인한다. 새 PID에서 실제 import 경로·실행 코드 해시를 확인한다.

큰 step은 이 전환의 **기본 조작에 포함하지 않는다**. steady 설정이므로 큰 초기 오차가 남으면
slew가 끝날 때까지 기다린다. 빠른 step이 필요하면 그 시점의 오차와 영향 범위를 보고 별도 승인한다.
그 경우 depth/aggTrade·정리뿐 아니라 wall clock을 쓰는 OI/daily/pack/backup/restic 등 다른
작업의 실행·예약도 조사하고 필요한 hold를 명시해야 한다. 실행 중인 백업을 임의로 강제 종료하지 않는다.
정확한 기준 없이 `chronyc makestep`, `date`, `hwclock`, `--adjust-system-clock`을 실행하지 않는다.

## 3. 운영 적용 후 완료 기준

- 새 서비스가 정상 수신·원문/표준 저장·depth REST 대조·aggTrade ID 연결을 이어 간다.
- 기존 데이터 상태 조회와 새 `clock_usable`을 함께 확인한다. 준비 대기는 healthy 수집으로 세지 않는다.
- 새 hourly 세션의 종료 보고서와 다음 시작이 실제 발생한 뒤 기록한다. 계획된 시각만으로 완료를 쓰지 않는다.
- 서비스 SIGINT 종료·재시작을 승인된 창에서 확인한다. 호가 소진·DNS 장애·시각 역행을 운영에
  인위적으로 만들 필요는 없다. 세부 오류 경로는 이번 격리 시험과 구분한다.
- 수집을 중단하는 재부팅·로그아웃·VM suspend/resume 시험은 **별도**다. 이번에는 수행하지 않았다.

일상 상태 조회 예시(설치 뒤에만 해당):

```bash
systemctl status marketdata-collector@depth.service marketdata-collector@aggtrade.service
journalctl -u marketdata-collector@depth.service -u marketdata-collector@aggtrade.service
/opt/marketdata/releases/system-runtime-20261006-c09bafd0645578a3/.venv/bin/python -I /opt/marketdata/releases/system-runtime-20261006-c09bafd0645578a3/integrations/runtime/marketdata_runtime.py status --profile /etc/marketdata/runtime.json --stream depth
```

## 4. 되돌리기

1. 새 **시스템** 수집 instance 두 개를 disable/stop한다. PID/cgroup·stream lock 해제를 확인한다.
2. 기록한 이전 사용자 유닛 내용과 운영 소스 해시가 남아 있는지 확인한다. 데이터·카탈로그를 되돌리거나
   수정하지 않는다. 현재 시각이 준비된 상태인지 확인한 뒤 원래 enabled 상태에 맞춰 사용자 수집만 복원한다.
3. 수집 코드 rollback에 NTP daemon rollback을 자동으로 묶지 않는다. Chrony가 정상이라면 그대로
   둘 수 있다. 시간 서비스를 되돌려야 하면 수집·정리 hold 상태에서 별도 현재 동기화 확인을 거친다.
   예전 `/run` 표시 파일이 존재한다는 사실만으로 새 시간 서비스의 동기화를 인정하지 않는다.
4. 이전 사용자 유닛에는 새 준비 gate가 없다. 따라서 이 rollback은 긴급 복구 경로이며, 다음 부팅의
   시각 문제까지 해결한 구성이라고 보고하지 않는다. 기존 후보/receipt/시각 근거는 지우지 않는다.
5. checkpoint 타이머는 정상 시각·미완료 정리 없음·기존 grant 일치를 확인한 뒤 원래 예약만 복원한다.
   새 수동 삭제를 실행하거나 반복 권한을 확대하지 않는다. 보류된 날의 정리를 수동으로 몰아 실행하지 않는다.

## 다음 데이터 품질 단계와 한계

이번 시각 기록은 프로세스별 **관측 근거**다. 기존 파일/메시지의 품질 상태를 자동으로 바꾸지 않고,
연구 adapter가 시간 이상 구간을 자동 제외하는 기능도 없다. `clock_usable`과 원래 collector 상태를
함께 확인하는 운영 단계다. 시각 이상은 이후 정상 회복으로 과거 기록에서 삭제하지 않는다.

또 현재 `backup.py`는 카탈로그에 등록한 자료와 catalog를 백업한다. 새 `state/runtime-clock`의
JSONL/latest는 그 목록에 들어 있지 않으므로 **기존 daily/restic이 자동 보관한다고 주장하지 않는다**.
코드에서 백업 목록을 확인한 제한이며, 기존 manifest/source_root 검사를 완화하지 않았다.
장기 연구용 품질 이력으로 사용하기 전에는 시각 근거를 일관되게 보관하는 접점과 collection ID
연계를 별도 후보로 정해야 한다. 현재 단계에서 이 로그만으로 백업 복원 후 시각 품질까지 복구됐다고
판정하면 안 된다. 이 제한은 운영 승인 시 함께 검토할 항목이다.

자동 삭제의 영구 시각 gate도 이번에 바꾸지 않았다. 후속 후보에서는 현재 Chrony 품질·monotonic
관측·시각 설정 변경 여부를 plan과 apply의 실제 판단 지점에서 검사하고, 불확실하면 보류하도록
설계할 수 있다. 기존 frozen 정리기와 반복 grant 해시가 달라지므로 별도 시험·승인을 받는다.
이번 collector 전환 승인으로 해당 반복 삭제 권한까지 확대하지 않는다.

Proxmox에서는 같은 배포본을 새 Linux VM에 설치하고 UID·경로·UUID·프로필을 새 환경에서 검증한다.
호스트 Chrony 정상 여부를 게스트 준비 완료로 대신하지 않는다. VM pause/resume·복원·Guest Agent
시각 조정과의 관계는 실제 VM 구축 뒤 검증한다. 현재 PC의 듀얼부팅 RTC 정책도 그 VM에 그대로 복사하지 않는다.
