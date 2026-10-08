# UTC RTC·부팅 초기 보정 후보의 전환 검토

대상: **system-bootstrap-20261007-f85eb8321a19c1ee**. 2026-10-07 보완 구현·격리 시험을 완료한 후보다.
운영 시간 서비스·RTC·수집·예약·정리 grant는 변경하지 않았다. 아래 절차는 실행 기록이 아니다.
이전 후보와 달리 **자동 checkpoint 정리를 전환 후 자동 재개하지 않는다**.

## 고정한 적용 범위

| 항목 | 설치 후보/역할 |
|---|---|
| release | `/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee` |
| runtime 설정 | `/etc/marketdata/runtime.json` ← [machine-profile.json](machine-profile.json) |
| 초기 보정 설정 | `/etc/marketdata/clock-bootstrap.json` ← [boot-profile.json](boot-profile.json) |
| RTC 확인 자료 | `/etc/marketdata/rtc-utc-confirmation.json` — 운영자가 실제 확인 후 작성; 현재 없음 |
| Chrony 설정 | `/etc/chrony/marketdata.conf` ← release의 chrony.conf; rtcsync, maxpoll 8, leap slew |
| Chrony/chrony-wait drop-in | [units/system](units/system/); UTC 전제 확인 및 bootstrap 뒤의 wait 순서 |
| root 초기 준비 | `marketdata-clock-bootstrap.service`; 직접 시각 syscall 금지, Chrony socket 명령 |
| 일반 계정 수집 | `marketdata-collector@depth.service`, `marketdata-collector@aggtrade.service` |
| 부팅 상태 | `/var/lib/marketdata-clock`; root:root 0755, boot-state/lock 0644 |
| 사용자 기존 작업 | [8개 대기 의존 관계·최종 검사 drop-in](units/user/); 기존 ExecStart·타이머·업무 제한 유지 |
| 새 사용자 대기 | `marketdata-clock-ready.service`; 읽기 전용, 별도 timer 없음, 업무 start job 대기 |
| 유지 보류 | 옛 depth/aggTrade service, checkpoint retirement service/timer persistent mask |
| 데이터·manifest·source_root | 현재 경로와 계약 유지 |
| 삭제/권한 | grant 변경·forget/prune·시험 공간 삭제 없음 |

후보는 자동 step을 남은 오차 >100ms, <=300초 범위에서 부팅당 최대 한 번 요청한다.
300초 초과는 UTC/LOCAL 혼동 같은 큰 설정 오류를 자동 통과시키지 않는 초기 정책이다.
시간원 조건이 불충분하면 보내지 않는다. source age 30초는 보정 전 확인용이고, 현재 수집 준비의
reference age 600초·correction 100ms·조건부 상한 500ms는 유지한다. 실측 최적값이라는 뜻은 아니다.

## 전환 전에 끝낼 준비

1. source.tar.gz 및 release.json의 172개 해시를 확인하고 새 설치 경로에서
   `uv sync --frozen --no-editable --no-dev --link-mode copy`로 설치한다. Python 3.12.3,
   uv 0.12.3, setuptools 83.0.0 및 uv.lock을 기준으로 한다. venv를 다른 경로에서 복사하지 않는다.
2. 그 venv의 Python `-I integrations/runtime/seal_runtime.py`로 설치 receipt를 만들고
   실제 package import가 새 venv에 속하는지 확인한다. release·venv·설정·global drop-in은 root 소유,
   일반 계정이 수정할 수 없는 권한으로 한다. 운영 소스는 editable 경로에서 수정하지 않는다.
3. 기존 unit 원문/링크·enabled/masked 상태·drop-in·PID/invocation, 설정·RTC 상태·시각·grant hash를
   기록한다. 암호 값을 출력하지 않는다. 수동 CLI·cron·다른 자동 시작도 조사한다.
4. 이번 실제 승인에는 **Windows 왕복 부팅과 최종 검증용 Linux 부팅, 그동안의 수집 공백**을 명시한다.
   이 후보는 설치한 부팅을 hold로 기록하고 다음 부팅에서만 초기 보정을 연다. 재부팅 없는 현행
   부팅의 step 허용 기능은 추가하지 않았다.
5. 실제 배포판 Chrony는 현재 미설치다. 검토한 패키지 4.5-1ubuntu4.2와 다르면 config/CSV/socket/
   service 접점을 재확인한다. apt 설치가 timesyncd를 교체할 수 있음을 전환 범위에 포함한다.

## A. 재부팅을 넘어 유지되는 작업 보류

- 8개 업무 timer와 자동 정리 timer의 새 예약을 중지한다. 실행 중 daily/pack/backup/restic/정리는
  자연 종료와 최종 상태를 확인한다. timer 중지를 실행 중 작업 종료로 오인하지 않는다.
- 두 기존 수집기는 SIGINT 경로로 종료하고 최종 보고서·저장 완료 범위·PID/cgroup·stream lock을 확인한다.
- 업무 service/timer의 임시 보류, 옛 수집 및 자동 정리의 지속 보류를 **persistent mask**로 만든다.
  단순 stop이나 `--runtime` mask는 다음 Linux 부팅의 자동 시작을 막지 못한다.
- 기존 사용자 경로에 unit 파일/링크가 있으면 mask 명령이 실패할 수 있다. 원본/링크를 기록한 별도
  보관 경로로 옮긴 뒤 mask를 만들고, UnitFileState=masked까지 확인한다. 무작정 파일을 삭제하거나
  `/etc`의 mask가 사용자 홈의 더 높은 우선순위 unit을 이긴다고 가정하지 않는다.
- 현재 등록한 모든 marketdata 진입점이 보류되고 수동 실행도 없는지 확인한다. 데이터/백업 내용은 유지한다.

## B. Windows·Linux RTC를 함께 UTC로 정리

이 순서에서는 아직 기존 timesyncd를 유지하여 Linux 시스템 시각을 확인할 수 있게 한다.
Chrony 패키지 교체를 먼저 하여 시간원 없이 돌아오는 상황을 만들지 않는다.

1. Windows의 기존 RealTimeIsUniversal 값 유무/자료형·시간대와 실제 시각을 기록한다.
   관리자 셸에서 필요할 때 다음 REG_DWORD 설정을 적용한다.

   ```cmd
   reg add "HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\TimeZoneInformation" /v RealTimeIsUniversal /t REG_DWORD /d 1 /f
   reg query "HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\TimeZoneInformation" /v RealTimeIsUniversal
   ```

2. Windows의 재시작·시각 동기화를 확인한다. RTC 내용이 아직 LOCAL일 수 있어 이 중간 상태에
   9시간 차이가 나타날 수 있다. flag 하나가 존재한다는 것만으로 전환 완료로 판정하지 않는다.
3. Linux로 돌아와 모든 앱 보류가 유지됐는지 먼저 확인한다. 실제 외부 동기화와 시스템 UTC가
   정확해진 뒤 `sudo timedatectl set-local-rtc 0`으로 시스템→RTC를 반영한다.
   **`--adjust-system-clock`은 사용하지 않는다.** 지역 표시 시간대는 Asia/Seoul로 유지할 수 있다.
4. `/etc/adjtime=UTC`, `LocalRTC=no`, RTC와 동기화된 UTC의 근접성을 확인한다. Windows와 Linux를
   다시 실제로 부팅하여 양쪽 시각이 맞고 RTC 해석이 되돌아가지 않는지 기록한다. suspend/최대절전
   복귀만으로 새 부팅 검증을 대신하지 않는다.
5. 확인이 끝난 뒤 example JSON의 false/null을 실제 근거·확인 시각과 함께 작성한다. 이 값은
   Linux가 Windows를 원격 검증한 것이 아니라 운영자가 확인한 사실의 기록이다.

기술 근거: [Windows DWORD 설정을 사용하는 AWS 문서](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/change-time-zone-of-instance.html)
(EC2 대상 자료이므로 이 PC의 왕복 확인을 생략하지 않는다),
[systemd의 set-local-rtc 방향](https://raw.githubusercontent.com/systemd/systemd/v255/man/timedatectl.xml).

## C. 새 기반 설치·다음 부팅 준비

1. 앱은 계속 보류한다. Chrony/chronyd/chrony-wait가 기본 설정으로 자동 시작하지 않도록 **패키지 설치 전**
   시스템 manager에 persistent mask를 준비하고 기록한다. 기존 관리자가 만든 mask와 구분한다.
   기본 makestep/rtcsync 설정의 순간 실행을 허용하지 않는다. 전역 policy-rc.d를 덮어쓰지 않는다.
2. 패키지 설치, 검증된 config/drop-in, collector/bootstrap unit, runtime/boot profile과 UTC 확인 자료를
   설치한다. 두 time daemon이 동시에 시계를 제어하지 않게 한다. distro AppArmor/sandbox를 완화하지 않는다.
3. root 상태 디렉터리를 준비한다. 다음 명령은 최초 등록에만 사용하며 기존 파일이 있으면 덮어쓰지 않는다.

   ```bash
   sudo /opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee/.venv/bin/python -I /opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee/integrations/runtime/clock_gate.py enroll --profile /etc/marketdata/clock-bootstrap.json
   ```

4. 새 공통 wait service와 8개 service의 global 대기 의존 관계·최종 검사 drop-in을 설치하고 두 manager를 daemon-reload한다. 해당 service/
   timer의 임시 mask만 해제하고 보관한 원본/링크를 복원한다. 이전 enabled 상태에 따라 예약을 복원한다.
   현재 부팅은 installation_hold이므로 업무는 공통 gate 뒤의 start job으로 대기해야 한다. 본 명령 실행이나 성공 기록이 생기면 안 된다. **옛 두 수집과 정리 service/
   timer의 persistent mask는 유지한다.** 기존 grant는 수정하지 않는다.
5. 실제 `systemctl --user show`의 Requires/After/ExecStartPre·waiter ExecStart·timer Unit·21개 목록을 후보와 비교한다. read-only check가
   아직 거부하는지, root startup 코드가 일반 계정으로 실행되지 않는지 확인한다.
6. 새 root 서비스와 두 collector는 다음 부팅의 자동 시작만 설정한다. Chrony 임시 mask는 새 유효
   설정·ExecStartPre를 확인한 뒤 해제한다. 이미 존재하던 다른 mask는 임의로 해제하지 않는다.
   현재 boot ledger를 지워 보정을 강제로 열거나 `enroll`을 재사용하지 않는다.
7. 최종 설치 경로에서 systemd-analyze verify, normal UID의 데이터/설정 접근, Chrony Unix socket,
   root helper의 runuser→사용자 bus 조회, 실효 보호 옵션을 확인한다. 후보의 사용자 unit 시험은 이 검증을 대체하지 않는다.

## D. 승인한 실제 부팅과 완료 기준

- 새 boot ID의 preparing → 필요 시 step_intent/step_returned → closed가 실제 순서대로 기록된다.
  source/correction 전후, provider invocation, 명령 결과를 확인한다. 정상 RTC이면 step 0회도 정상이다.
  테스트를 위해 운영 시계를 일부러 41초 틀리게 하지 않는다.
- root coordinator는 chrony-wait 전에 동작하고, 두 stream 및 user 작업은 closed 이전에 시작하지 않는다.
  옛 user stream과 새 system stream이 중복되지 않는다. 프로세스의 실제 import/receipt/version을 확인한다.
- 새 collector의 원문·표준 저장, depth REST 대조, aggTrade 연결, 시각 기록을 함께 확인한다.
  첫 hourly 종료와 다음 세션 시작은 실제 발생 후 기록한다. 이 문서에 미리 완료로 쓰지 않는다.
- root unit 보호 옵션 아래의 접근/권한, 실제 chronyd에 `-x`가 없고 지정 config를 사용함,
  운영 시간원 품질을 확인한다. VM pause/resume는 이후 별도 검증이다.
- 자동 정리는 계속 보류한다. 초기 동기화 gate는 매 정리 plan/apply의 영구 시각 검사를 대체하지 않는다.

## 상태 조회·중지·되돌리기

설치 후 상태 확인:

```bash
systemctl status marketdata-clock-bootstrap.service marketdata-collector@depth.service marketdata-collector@aggtrade.service
journalctl -u marketdata-clock-bootstrap.service -u chrony.service
/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee/.venv/bin/python -I /opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee/integrations/runtime/clock_gate.py status --profile /etc/marketdata/clock-bootstrap.json
```

status의 ledger.closed는 **보정 허용이 닫혔다는 상태**다. 현재 시각 건강 상태를 보려면 같은 CLI의
`check`와 collector runtime status를 사용한다. 600초 장기 대기는 journal warning으로 남으며 외부
알림을 구성했다고 보고하지 않는다. 명령 결과 unknown이면 현재 품질/로그를 조사하고 자동 재전송하지 않는다.
현재 boot/profile·provider·source/전체 품질과 업무 정지 조건이 충분하면 coordinator가 재보정 없이
closed로 진행할 수 있고 step_result=unknown은 남는다. 부족하면 계속 보류하고 slew/시간원 회복을
관찰한다. 추가 큰 보정이 필요하면 별도의 업무 정지·관리자 승인 작업으로 다룬다.

새 자동 초기 준비만 중지하려면 bootstrap unit을 disable/stop하고 persistent mask로 추가 시작을 막는다.
이미 closed 상태와 ledger는 삭제하지 않는다. 이때 다음 부팅의 수집도 gate에 막히므로, 중지는 전체
준비/수집 가용성에 영향을 준다. 실행 중 보정은 취소 여부를 가정하지 말고 intent/result를 점검한다.

수집 rollback은 새 system collector를 disable/stop하고 저장 종료를 확인한 뒤 진행한다. Chrony no-step
및 UTC RTC는 정상이라면 유지할 수 있다. 검증한 현재 시각 아래서 보관한 옛 user collector를 복원하되,
새 gate를 붙일지 또는 다음 부팅을 금지한 임시 수동 복구인지 명확히 한다. 두 collector를 동시에 켜지 않는다.
자동 정리는 rollback에도 보류한다. 데이터·manifest·기존 백업·시험 사본·grant를 되돌리거나 삭제하지 않는다.

시간 서비스 rollback과 RTC LOCAL 복귀는 별도다. Windows flag가 UTC인 채 Linux만 LOCAL로 되돌리지 않는다.
양쪽 설정과 RTC 내용을 함께 정할 전환 창을 다시 마련한다. root ledger/profile 변경도 기록을 보존하는
명시적 migration 대상이며 파일 삭제로 startup을 통과시키지 않는다.

## 다음 필수 작업

시각 JSONL/닫힌 bootstrap 기록의 불변 확정·용량 관리·collection 연결·백업/복원 편입과,
자동 삭제 plan/apply의 현재 시각/불연속 검사는 별도 후보로 진행한다. 현재 이 두 기능은 미완료다.
자료가 없으면 연구 시각 품질은 미확인으로 취급한다. 기존 보존·복구 검사를 완화하지 않는다.

## 이번 보완과 검증 경계

- 동일 client에서 `chronyc -n -h <고정 Unix socket> -m "retries 0" "makestep"`을 실행한다.
  보정 명령 형태는 인자 없는 현재 보정이다. Chrony 4.5/동일 프로토콜의 응답 유실 시험에서는
  기존 호출 2회와 새 Host.step 1회 요청을 관측했다. 실제 daemon의 시계 제어를 시험한 것은 아니다.
- bootstrap의 `ReadWritePaths=/run/chrony`는 client 응답 소켓 생성을 위한 좁은 쓰기 예외다.
  StateDirectory만으로는 이 경로가 writable해지지 않는다. collector 권한·CAP_SYS_TIME은 추가하지 않는다.
- 공통 user wait service는 5초마다 현재 조건을 확인하고 준비가 되면 종료한다. `RemainAfterExit=no`라
  다음 업무 시작 때 다시 검사한다. 무제한 TimeoutStartSec는 이 대기에만 적용한다.
- 8개 업무 자체의 Restart/StartLimit/TimeoutStartSec/SuccessExitStatus 및 timer는 변경하지 않는다.
  정상적인 준비 대기는 업무의 시작 실패를 소진하지 않는다. gate 종료 직후 현재 품질이 다시 바뀌거나
  실제 업무가 실패한 경우에는 기존 오류 정책이 적용된다. 모든 종류의 실패에 자동 재개를 약속하지 않는다.
- 기다리는 start job은 systemd가 유지한다. 그동안 발생한 여러 같은 timer 요청을 모두 별도 작업으로
  재생하는 구조는 아니다. `Persistent=true`의 성공 보장으로 설명하지 않는다.

[의존 관계 검사](unit-verify.json)는 systemd 255 및 배포판 Chrony 4.5 원래 유닛·alias에 후보 drop-in을
합쳤고, 시스템 5개·사용자 9개 root transaction을 오류 없이 검증했다. 미설치 실행 파일 경로만 시험
경로로 치환했다. 사용자 wait가 활성화 중인 상태를 실제 coordinator의 audit로 구분하는 시험도 했다.
이는 실제 새 부팅의 서비스 접근 성공을 뜻하지 않는다. `user@`의 READY는 user basic.target에 도달하면
발행되며 모든 업무 완료까지 기다리는 것은 아니다([systemd 255 구현](https://raw.githubusercontent.com/systemd/systemd/v255/src/core/manager.c)).

실제 root 보호 환경은 이번에 실행하지 못했다. sandbox 밖에서도 `sudo -n true`가 관리자 암호를
요구했다. 권한 제한을 우회하지 않았다. 전환 전 관리자 인증을 갖춘 상태에서 다음 접점을 확인해야 한다.

1. 최종 bootstrap과 같은 ProtectClock/ProtectSystem/ProtectHome/CapabilityBoundingSet/NoNewPrivileges
   및 ReadWritePaths 아래에서 Unix socket `tracking` 조회가 성공하는지 확인한다. 이 접점 시험은
   `makestep` 대신 읽기 전용 명령을 사용한다. client 소켓 생성·정리도 관측한다.
2. 같은 보호 환경에서 `runuser -u yoonhc`로 사용자 manager show/list를 조회한다.
3. root StateDirectory 파일 생성·fsync와 일반 계정의 read-only shared flock을 별도 시험 파일로
   확인한다. 운영 ledger/lock을 삭제하거나 시도 카운터를 바꾸지 않는다.
4. 실제 chronyd의 명령행·설정·invocation, 외부 시간원 품질은 별도로 대조한다.

읽기 전용 root 소켓 접점의 예시(설치/관리자 승인 후, 새 이름을 사용):

```bash
sudo systemd-run --unit=marketdata-clock-access-probe-20261007 --wait --pipe --collect \
  -p NoNewPrivileges=yes -p ProtectClock=yes -p ProtectSystem=strict \
  -p ProtectHome=read-only -p PrivateTmp=yes \
  -p 'CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_SETUID CAP_SETGID' \
  -p 'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK' \
  -p ReadWritePaths=/run/chrony \
  /usr/bin/chronyc -n -h /run/chrony/chronyd.sock -m 'retries 0' tracking
```

## 공통 대기 상태와 되돌리기 주의

`systemctl --user status marketdata-clock-ready.service`, `systemctl --user list-jobs` 및 해당 journal로
대기를 확인한다. 대기 성공과 본 업무/백업 성공은 별도다. 600초 이후 long_wait는 journal의 JSON 표시이며
외부 알림은 아직 없다. waiter 자체를 SIGKILL하거나 명시적으로 stop하여 dependency job이 취소된 경우에는
원인 해결 후 해당 업무를 다시 요청해야 할 수 있다. 이것은 시각 준비 조건이 정상적으로 회복되는 경로와 다르다.

**waiter의 명시적 stop은 Requires로 연결된 업무도 중지시킬 수 있다.** rollback을 위해 이것만 먼저
stop하지 않는다. 관련 timer의 새 예약을 보류하고 실행 중 업무의 자연 종료를 확인한 뒤, 대기 중 요청을
기록·취소하고 새 drop-in/wait unit을 제거한다. 원래 설정으로 돌아간 업무를 시각 미검증 상태에서
재개하지 않는다. 앞 절의 collector/Chrony/RTC rollback 및 자동 삭제 보류 원칙은 그대로 적용한다.

이번 profile schema 2와 새로운 파일 해시는 이전 후보의 승인/ledger identity와 같지 않다.
이전 후보는 운영 미설치였으므로 새 배포에서는 새 전체 범위를 검토한다. 만약 별도로 이전 ledger를
등록한 환경이라면 삭제·재-enroll로 통과시키지 말고 명시적인 migration 절차를 먼저 검토한다.
