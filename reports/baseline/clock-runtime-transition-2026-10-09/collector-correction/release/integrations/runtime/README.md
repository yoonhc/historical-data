# 시스템 수집·부팅 초기 시각 보정 후보

운영에 설치하지 않은 2026-10-07 보완 후보다. 이전 runtime 및 bootstrap 후보는
수정 없이 보존한다. 원본 collector·Book·요청 정책·partial/replay와 기존 백업 코드는 그대로다.
이 후보는 Windows/Linux RTC의 UTC 통일을 **적용 전제**로 요구한다. 아직 실제로 확인한 사실이 아니다.

## 실행과 권한

- 시스템 `marketdata-collector@depth/aggtrade.service`는 일반 계정으로 고정 release의
  비 editable venv와 `python -I`를 사용한다. 기존 runtime 준비 검사·시각 감시·SIGINT 종료를 유지한다.
- `marketdata-clock-bootstrap.service`만 root로 초기 절차를 관리한다. 직접 시각 변경 syscall은
  `ProtectClock=yes`, capability 제한으로 차단하고 Chrony의 Unix socket에 인자 없는 `makestep`을
  요청한다. 동일 chronyc 프로세스에서 `-m "retries 0" "makestep"`으로 응답 유실 재시도를
  끈다. 인자 없는 makestep이며 향후 자동 step window를 남기지 않는다. root 유닛은
  `ReadWritePaths=/run/chrony`로 chronyc 응답 소켓 생성에 필요한 디렉터리만 추가 허용한다. collector에는 그 socket의 쓰기 권한이나 시각 변경 capability를 주지 않는다.
- 정상 Chrony 설정은 `rtcsync`, `minpoll 6 maxpoll 8`을 사용하며 `makestep`, `initstepslew`,
  숨은 include/local reference는 없다. 재시작만으로 자동 step window가 생기지 않는다.
  윤초도 `leapsecmode slew`를 명시하여 기본 kernel step을 피한다. 그 구간의 Leap/오차 품질은
  기존 검사대로 별도 거부·기록하며 UTC와 일시적 오차가 없다는 뜻은 아니다.
- Chrony 자체의 ExecStartPre가 Windows UTC 확인 자료, `/etc/adjtime=UTC`, `LocalRTC=no`를 읽는다.
  준비되지 않았으면 **RTC 쓰기가 있는 daemon부터 시작하지 않는다**. Linux/Windows 설정은 이 코드가
  바꾸지 않는다. Windows 확인은 운영자가 제공한 증거이며 Linux에서 Windows 레지스트리를 검증했다고 주장하지 않는다.

## 부팅별 보정 허용 상태

`/var/lib/marketdata-clock/boot-state.json`은 root 소유의 영속 상태다. `/run` 표시만 사용하지 않는다.
부팅 ID·전체 bootstrap profile 해시·최초 boottime·명령 시도·결과 알려짐 여부를 포함한다.
root 소유 0755 디렉터리/0644 상태 파일을 일반 계정은 읽기만 한다. 비밀값은 없다.

```text
첫 설치: installation_hold (현재 부팅에서는 보정/작업 시작 금지)
다음 부팅: preparing
  → 유효 시간원 확인
  → 필요하면 step_intent 저장·fsync
  → 인자 없는 chronyc makestep 최대 1회 요청
  → step_returned
  → 현재의 전체 품질 검사
  → closed 저장·fsync
  → 각 작업이 현재 품질을 다시 검사한 후 시작
```

- 준비가 끝난 순간 그 부팅의 step 허용을 닫는다. child가 아직 안 떴거나 hourly 사이 PID가 없어도
  다시 열지 않는다. 완료한 oneshot/Chrony 재시작은 추가 step을 허용하지 않는다.
- 명령 전 intent를 저장한다. 강제 종료·명령 timeout·응답 유실 뒤에는 명령을 재전송하지 않는다.
  `step_result=unknown`을 유지하면서 현재 품질만 검사한다. 나중에 품질이 충분하면 작업은 시작할 수
  있지만 과거 명령 성공을 소급하여 만들지 않는다. 큰 오차가 남으면 점검이 필요하다.
- 없는/손상된 ledger나 다른 profile을 자동 초기화하지 않는다. `enroll`은 기존 상태를 덮어쓰지 않고,
  설치한 현재 부팅을 hold 상태로 등록하는 명령이다. 상태 삭제를 재시도 방법으로 쓰지 않는다.
- 파일 잠금은 두 coordinator의 동시 실행을 막는다. 상태 공개 전 저장 실패는 작업 시작을 허용하지 않는다.
  일반 계정의 시작 검사도 같은 파일의 공유 잠금에 참여하므로, coordinator가 기록 중인 상태를
  커밋 완료로 읽어 통과하지 않는다.
- 새 부팅은 다시 초기 준비 대상이다. VM 복원으로 같은 boot ID가 돌아온 경우에는 기존 closed가 유지되고
  현재 품질 검사가 적용된다. 실제 VM pause/resume 동작은 별도 미검증이다.

## 보정 전 검사와 보정 후 검사의 차이

보정 전에는 외부 reference·Leap Normal·source 불확실성·skew·새 측정의 신선도를 검사한다.
아직 큰 시스템 오차가 있는 시점에 남은 correction 100ms 조건을 요구하지 않는다.
Chrony 4.5 CSV correction은 system clock에 더할 보정량이다. reference age는 이 단계에서만
`system wall + correction - reference_time`으로 비교한다. 입력 원문/실제 수신 시각을 고치는 기능이 아니다.
근거는 [Chrony REF_GetTrackingReport](https://github.com/mlichvar/chrony/blob/4.5/reference.c)다.

| 항목 | 초기 후보 정책 |
|---|---:|
| 보정 전 reference age | 30초 이내, 미래 허용 100ms |
| 보정 요청 임계값 | 남은 correction 절댓값 > 100ms |
| 자동 초기 보정 상한 | 300초; 9시간 같은 RTC 기준 오류는 점검 대상으로 보류 |
| 보정 후/작업 시작 남은 correction | 100ms 이하 |
| 보정 후 조건부 오차 상한 | 500ms 이하 |
| 보정 후 reference age | 600초 이하 |
| skew | 100ppm 이하 |
| 준비 시도 제한/재시도 | monotonic 120초/서비스 15초 후 재시도 |
| 장기 대기 표시 | 현재 부팅 최초 시도 이후 600초, boottime으로 누적 |

초기 정책값이며 실제 시간원 품질을 측정하여 최적화한 값은 아니다. maxpoll 8은 명목상 최대 256초다.
600초와 기본 maxpoll 10의 1,024초가 충돌하는 설정은 아니다. 유효 측정 거부·통신 실패는 여전히
reference를 오래되게 할 수 있다. 10초 tracking 조회가 10초 NTP 측정이라는 뜻도 아니다.

부트 coordinator가 배포판 chrony-wait(남은 correction 0.1초 조건)보다 앞에 온다.
최종 `rt.assess_clock` 검사는 waitsync보다 reference/불확실성 조건까지 검사한다. 배포판 wait와
각 collector runtime 검사도 유지한다. marker가 준비된 뒤에도 각 작업은 최신 tracking을 다시 읽는다.

## 기존 예약 작업 연결

`user-unit-inventory.json`은 기존 20개 사용자 unit과 새 공통 대기 service 1개를 분류한다.
bootstrap profile은 새 waiter 항목을 포함하는 schema 2이며 ledger schema는 1이다.

- 새 `marketdata-clock-ready.service`는 일반 계정의 읽기 전용 oneshot이다. 별도 타이머는 없다.
  `Requires`/`After`로 필요한 업무가 불러오고, 현재 boot/closed/설정/시각 조건을 5초마다 검사한다.
  준비 미완료와 읽기·설정 오류는 `phase=waiting`, `ready=false`로 기록하며 성공 종료하지 않는다.
  600초 이후 `long_wait=true`를 기록한다. 이 표시가 외부 알림을 보냈다는 뜻은 아니다.
- OI·OI probe·OI quality·daily·pack·backup·restic archive·network observer 8개는 대기 service의
  완료까지 **start job이 큐에 남는다**. 따라서 준비 대기가 각 업무의 시작 횟수·실행 timeout을
  소진하지 않는다. 무제한 대기는 공통 대기 service에만 설정하며, 기존 업무의 ExecStart,
  TimeoutStartSec, Restart, StartLimit, SuccessExitStatus와 timer는 그대로 둔다.
- 준비 회복 후 대기 중인 start job이 진행하므로 다음 날짜/타이머를 기다릴 필요가 없다.
  최종 ExecStartPre의 현재 품질 검사는 유지한다. 대기 완료와 이 검사 사이에 시각이 다시 나빠지는
  경우, 또는 실제 업무 오류에는 기존 오류/재시도 정책이 적용된다. 모든 실패의 무한 재시도를
  보장하지 않는다. 오랫동안 겹친 timer 요청은 systemd의 같은 start job으로 합쳐질 수 있으며,
  놓친 예약 횟수만큼 업무를 반복 생성하는 기능은 아니다.
- 대기 unit은 `RemainAfterExit=no`이므로 다음 업무 활성화 때 현재 품질을 다시 검사한다.
  이전 성공 표시를 영구 준비 상태로 재사용하지 않는다. 대기 중에는 공유 잠금을 유지하지 않아
  root coordinator의 배타 잠금을 방해하지 않는다.
- coordinator는 이 대기 unit의 고정 ExecStart와 추가 실행 명령 부재를 확인한 뒤 업무와 구분한다.
  8개 업무에는 effective Requires/After와 최종 시작 검사가 모두 있어야 한다. 실제 업무 PID나
  알 수 없는 unit/명령이 있으면 보정을 거부한다. 사용자 대기 unit이 시스템 target을 기다리도록
  연결하지 않는다.
- 옛 depth/aggTrade service와 checkpoint retirement service/timer는 **persistent mask**로 유지할
  전환안이다. 정리 grant는 바꾸지 않는다. 자동 삭제는 영구 시각 gate 별도 검증/승인 전 재개하지 않는다.

step 전에는 실제 user manager의 unit 목록·effective ExecStartPre·timer 대상·persistent mask와
실행 중 PID를 확인한다. 시스템 collector도 비실행이어야 한다. 조회가 불가능하거나 새 unit이 발견되면
보류한다. 시스템 `After=`가 사용자 manager까지 연결된다고 가정하지 않는다.

이 보호는 명시한 서비스 진입점에 대한 것이다. 관리자가 별도 수동 CLI나 미등록 작업을 실행하거나,
root 상태/유닛을 바꾸면서 step 절차와 경쟁하는 경우까지 강제 격리하지 않는다. 전환 시 수동 작업도
종료·금지하고 부팅 자동 시작 전체 목록을 조사한다. OS 전체의 모든 프로세스가 gate에 참여한다는 뜻이 아니다.

## 관측과 남은 작업

기존 runtime은 1초 UTC/monotonic/boottime, 10초 provider/heartbeat를 관측한다. 시각 이상 후에는
그 실행의 clock_usable=false를 유지하며 원문 child는 계속 수집한다. 감시 기록 저장 실패 시에는
child를 SIGINT로 종료한다. raw 수신 시각을 증가값으로 조작하거나 과거 partial을 수정하지 않는다.

장기 대기는 bootstrap stdout의 구조화 결과와 journal warning으로 확인한다. 외부 메신저/이메일 알림을
설치하거나 보냈다는 의미가 아니다. ledger에 처음 기다린 boottime이 남아 재시도로 경과시간이 초기화되지 않는다.
실패 원인이 파일/RTC/서비스 접근인 경우 stderr에도 남고 자동 통과하지 않는다.

runtime JSONL은 실행마다 파일이 나뉘지만 총용량/보관 관리가 아직 없다. bootstrap events/ledger 역시
현재 daily/restic 백업 목록에 편입되지 않는다. **닫힌 로그 불변 확정·압축/용량 경보·collection 연결·
기존 검증/백업/복원 편입**이 다음 필수 작업이다. 로그가 없으면 시각 품질은 미확인이다.
기존 depth-status healthy와 clock_usable은 다른 의미이며 어느 하나로 나머지를 대체하지 않는다.

## 재현 범위

`test_clock_bootstrap.py`는 ±41초 합성 입력, SIGKILL 전후 지속 상태, 잠금·저장·명령 timeout,
RTC 확인·unit inventory·재시작을 격리 검사한다. `isolated_bootstrap_test.py`는 무작위 이름의 사용자
systemd 시험 unit만 만들고 JSON 가짜 시각을 사용한다. 호스트 clock/RTC/운영 service에는 손대지 않는다.
`isolated_chronyc_requests.py`는 실제 Chrony 4.5 client와 응답을 버리는 별도 Unix socket을 사용해
이전 호출의 재전송과 새 Host.step의 단일 요청을 대조한다. daemon을 시작하지 않으므로 실제 시각은
변경되지 않는다. `isolated_job_gate_test.py`는 8개 가짜 업무와 실제 audit/gate, 사용자 manager를
연결한다. 업무 timeout/횟수는 짧게 한 시험 조건이고 실제 calendar/Persistent 부팅 시험은 아니다.
실제 root system unit 보호 옵션·runuser·Unix socket 권한·외부 NTP 품질과 Windows 왕복 부팅은 운영
전환 과정에서 확인할 항목이다. 격리 결과로 실제 boot/clock 제어 성공을 주장하지 않는다.

참고: [Chrony commands](https://chrony-project.org/doc/4.5/chronyc.html),
[Chrony configuration](https://chrony-project.org/doc/4.5/chrony.conf.html),
[systemd service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html).

## unknown·취소·운영 복구

`status`로 현재 boot ID와 ledger의 profile hash, phase, step_observation/provider, step_result를
확인하고 journal에서 의도 기록·client 오류·현재 tracking을 대조한다. 살아 있는 bootstrap/chronyc
프로세스가 있는지도 확인한다. 상태를 읽기만 하며 ledger 삭제, 재-enroll, retries 재활성화로 우회하지 않는다.

- 결과가 unknown이어도 현재 source/전체 품질과 업무 정지 조건이 충족되면 기존 coordinator가
  재보정 없이 closed로 진행한다. 과거 step_result=unknown은 그대로 남고, 각 업무가 현재 품질을 다시 본다.
- 조건이 부족하면 계속 보류하고 slew/시간원 회복을 관찰한다. 추가 큰 보정이 필요하다고 판단하면
  업무와 수집을 보류하는 별도 승인 작업으로 처리한다. 자동 두 번째 명령은 없다.
- closed는 이 부팅의 자동 보정 허용 종료다. 현재 시각의 정상 여부는 `check`와 runtime 관측으로 확인한다.

공통 waiter를 명시적으로 중지하거나 프로세스를 강제 종료하면 준비 성공으로 처리하지 않는다.
`Requires` 관계상 명시적 stop은 의존 업무를 중지시킬 수도 있다. 운영 중인 backup/daily가 있는데
waiter만 무심코 stop하지 않는다. 타이머의 새 예약을 보류하고 진행 중 업무의 종료를 확인한 뒤
계획된 취소/rollback을 수행한다. 대기 프로세스 자체의 비정상 종료로 dependency job이 실패한 경우에는
원인을 고친 후 보류 업무를 명시적으로 다시 요청해야 할 수 있다. 정상적인 시각 준비 대기와 다르다.

상태 확인은 `systemctl --user status marketdata-clock-ready.service`, `systemctl --user list-jobs`,
해당 unit의 journal과 본 업무 최종 결과를 나눠 본다. waiter 성공은 업무 성공이나 백업 성공이 아니다.
