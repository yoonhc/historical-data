# 수집 시스템 서비스 후보 계약

이 디렉터리는 depth·aggTrade의 **배포 후보**다. 실행 코드는 별도 release의 비 editable
가상환경에서 가져오며, 데이터 루트는 현재 위치를 유지한다. 수집·호가 계산·요청 예산·최소 간격·
원문 형식·partial/replay 판정은 바꾸지 않는다. 운영 적용과 재부팅 시험은 별도 승인 대상이다.

## 역할과 실행

- 시스템 systemd: `marketdata-collector@depth.service`, `marketdata-collector@aggtrade.service`.
  `User=yoonhc`, 루트 권한·시각 변경 capability 없음. 시스템 network-online/Chrony와 순서를 연결한다.
- 배포판 `chrony.service`·`chrony-wait.service` 활용. wait unit의 최초 통과만 믿지 않고,
  각 수집 시작 시 현재 daemon invocation, 실제 tracking, 설정 해시를 다시 확인한다.
- `marketdata_runtime.py`: 준비 확인, 한 개의 collector subprocess 관리, 별도 시각 품질 기록.
  `date`, `hwclock`, `chronyc makestep`을 호출하지 않는다. Chrony 조회는 localhost UDP의 읽기 전용
  `tracking`만 사용한다. collector에 Chrony 관리 그룹·Unix socket 권한을 주지 않는다.
- collector는 고정 가상환경의 `python -I`로 실행한다. 기존 개발 경로·PYTHONPATH·사용자 site의
  package를 가져오지 않는다. UID·설치 receipt·Python 버전·실행 소스 해시·data UUID·접근성을 검사한다.
- 데이터 루트의 기존 stream flock도 그대로 사용한다. 사용자 서비스의 자동 시작을 해제한 뒤
  새 시스템 서비스로 전환하며, 실수로 두 CLI를 실행해도 기존 flock이 중복 writer를 거부한다.

서비스는 `ExecStartPre` 준비 실패 시 실패로 종료하고 `Restart=always`, 15초 뒤 재시도한다.
`StartLimitIntervalSec=0`으로 반복 실패 후 영구 정지하지 않는다. 준비 대기는 monotonic 120초,
각 외부 명령은 최대 3초이며, pre-start 제한은 150초다. 네트워크는 startup target과 필수 호스트
DNS를 확인한다. 거래소 API를 추가 호출하지 않으며 이후 인터넷 지속성을 보장하지 않는다.
기존 소켓 재연결은 collector가 맡는다. 새 시각 프로필 수치는 승인된 단위 시간 제한과 함께 변경한다.

supervisor는 SIGINT/SIGTERM을 받으면 child process group에 SIGINT를 보내 120초까지 저장 종료를
기다린다. systemd `KillMode=mixed`, `TimeoutStopSec=150s`로 비정상 잔류 프로세스도 함께 관리한다.
정상·partial·error collector 종료 코드는 유지한다. 한 시간 세션 뒤 systemd가 새 준비 검사를 한다.

## Chrony와 듀얼부팅

현재 PC는 사용자가 확인한 **Windows 등 다른 OS와의 듀얼부팅**이다. 후보 `chrony.conf`에는
`rtcsync`, `rtcfile`, `makestep`, `initstepslew`, local reference, 외부 설정 include가 없다.
`/etc/adjtime`·LocalRTC·Windows 설정은 이번에 바꾸지 않는다. RTC가 왜 빨라졌는지는 미확인이다.
파일 접근 정책을 완화하지 않도록 NTP 설정은 배포판 AppArmor가 허용하는
`/etc/chrony/marketdata.conf`에 둔다. 런타임 프로필은 `/etc/marketdata/runtime.json`에 둔다.

배포판 기본값 `makestep 1 3`을 그대로 쓰지 않는다. 단순 waitsync 통과는 남아 있는 자동 step
횟수를 닫는 동작이 아니며, daemon 재시작은 그 횟수를 다시 열 수 있기 때문이다.
후보는 **부팅 때도 재시작 때도 자동 큰 보정을 하지 않는 steady daemon**을 사용한다.
초기 오차가 크면 slew 동안 수집 시작이 늦어진다. 120초 초과 시 자동 통과하지 않고 재시도한다.
이 비용을 없애기 위해 임의로 step 권한을 주지 않는다.

큰 보정이 필요하면 수집과 시간 기반 정리를 통제한 별도 관리자 절차로만 수행한다.
`chronyc makestep`은 이 후보의 자동 명령이 아니다. 실제 운영 적용 때 초기 오차를 측정해
slew로 기다릴지, 별도 승인한 수동 step을 할지 선택한다. 부팅마다 step하는 자동 bootstrap은
기존 사용자 예약 작업까지 같은 gate에 편입하기 전에는 추가하지 않는다.

RTC 기준은 Windows에서 현재 UTC 사용 여부를 확인한 뒤 두 OS를 함께 정한다. Linux만
`set-local-rtc 0`으로 바꾸거나 빠른 RTC로 시스템 시각을 되돌리지 않는다. 미래 Linux 전용 VM의
UTC RTC/rtcsync는 별도 머신 프로필로 승인·검증한다. 시간대 Asia/Seoul과 RTC 기준은 별개다.

## 시작 시각 정책: 우선 검증할 초기 수치

| 조건 | 후보 값 |
|---|---:|
| 남은 시스템 보정량 | 100ms 이하 |
| 조건부 오차 상한: abs(correction)+dispersion+max(delay,0)/2 | 500ms 이하 |
| 마지막 reference age | 600초 이하, 허용 미래값 100ms 이하 |
| 주파수 불확실성 | 100ppm 이하 |
| Leap status | Normal만 허용; 윤초 예고 시 보수적으로 신규 시작 보류 |
| 외부 reference | 비 loopback·비 unspecified 주소, stratum 1~15 |

이 값은 새 PC의 Chrony를 실측해 최적화한 값이 아니며, ms 단위 지연 정확성을 인증하지 않는다.
UTC 오차 상한도 시간원이 올바르다는 전제를 갖는다. `-x` daemon은 시작 조건에서 거부한다.
Leap 예고를 허용하려면 예상 윤초 정책과 신규 gate 시험을 별도로 검토한다.

## 실행 중 시각 품질

1초 간격으로 UTC·monotonic·CLOCK_BOOTTIME을 관측한다. 시계 호출 사이의 스케줄링 지연도 기록한다.
wall-boottime 차이 250ms 초과, boottime-monotonic 차이 250ms 초과, 관측 간격 5초 초과,
Chrony 불가·기준 미충족·invocation 변경을 품질 이상으로 기록한다. Chrony는 10초마다 조회한다.
큰 보정을 수집기가 직접 되돌리거나 timestamp를 억지로 증가시키지 않는다.

이상이 생기면 **그 supervisor 실행의 `clock_usable=false`를 유지**한다. 원문 child는 계속
실행하며 다음 정상 시간 세션과 경계를 구분한다. `violations`는 이상 관측 수이며 장애 사건 수가 아니다.
시각이 회복돼도 그 실행의 과거 이상을 지우지 않는다. 다음 hourly 실행은 새로운 현재 준비 확인을 한다.

`state/runtime-clock/<stream>/<run-id>.jsonl`에 append/fsync 후 `latest.json`을 원자 교체한다.
boot ID·supervisor/child PID·프로세스 시작 tick·systemd invocation·프로필 해시·UTC/단조 시각·
원인·관측 전후 불확실 구간을 남긴다. JSONL은 누적 근거이고 latest는 현재 조회용 사본이다.
상태 조회는 최대 25초 신선도, 동일 boot/profile, supervisor/child 생존, 시각 연속성을 검사한다.
latest 쓰기 실패 시 수집 child를 종료하고 서비스 오류를 남긴다. 저장 장치 자체가 쓰기를 거부하면
파일 근거를 보장하지 않으며 stderr/systemd 기록과 신선도 제한을 함께 사용한다.

**이 근거는 메시지별 시각 인증이 아니다.** 1초 사이의 작은 이동, 상쇄된 이동, 샘플 사이 daemon
장애를 전부 검출한다고 보장하지 않는다. VM의 모든 resume 형태를 실측한 것도 아니다.
Chrony 상태는 최대 조회 간격+명령 지연 뒤 감지한다. OS/VM 관리자 시간 변경을 강제로 차단하지 않는다.

`depth-status`/`aggtrade-status`의 기존 데이터 품질과 이 시각 상태를 **둘 다** 확인한다.
이 후보는 기존 healthy/partial/replay 값을 수정하지 않는다. JSONL을 기존 report의 대체물이나
새 revision으로 취급하지 않는다. collection ID와의 정확한 연결은 child PID·invocation의 collector
journal을 함께 사용한다. 데이터 파일에 run ID를 추가하거나 연구 adapter에서 자동 제외하는 기능은
이번 단계에 없다. 연구에서 영향을 받은 메시지를 자동 식별하려면 그 접점을 별도로 구현해야 한다.

## 설치 자료와 검증 경계

`profile.example.json`은 이 PC의 기존 경로·UUID·UID를 담은 **설치 예시**다. `RELEASE_ID` 치환,
소유권·마운트 확인, evidence 디렉터리 사전 생성, 고정 prefix의 새 가상환경 생성이 필요하다.
venv를 다른 경로로 복사하지 않는다. `uv.lock`과 setuptools build constraint를 사용하여 새 경로에서
`uv sync --frozen --no-editable --no-dev --link-mode copy` 후 `seal_runtime.py`로 설치 receipt를 만든다.
운영에서는 release/venv와 프로필을 root 소유·수집 계정 쓰기 불가로 두고 state/data만 쓰게 한다.
그 소유권 전환과 `/opt`, `/etc`, 유닛 설치는 아직 하지 않았다.

`isolated_service_test.py`는 고유 이름의 **사용자** transient unit에서 가짜 provider/collector로
재시도·회복·종료를 확인한다. root system unit의 모든 sandbox 옵션이나 실제 거래소 접속 검증으로
확대하지 않는다. `isolated_chrony_test.py`는 일반 사용자, CapEff=0, `-x -U`의 실제 바이너리와
루프백 가짜 NTP만 사용한다. 호스트 시각을 보정하거나 실제 외부 시간원의 정확도를 입증하지 않는다.

## 자동 정리와 복구 경계

Chrony 설치·시간 보정 전 `marketdata-checkpoint-retirement.timer`의 새 실행을 해제하고,
이미 실행 중인 정리를 완료할 때까지 기다려 비활성 상태를 확인한다. 중간 삭제를 강제 종료하지 않는다.
기존 grant·정리 코드·정책·타이머 내용은 수정하지 않는다. 패키지 설치가 자동으로 timesyncd를
대체하고 daemon을 시작할 수 있으므로, 설치 자체가 운영 전환에 포함된다.

시간 준비와 새 수집의 정상 저장 확인 후에만 기존 예약 복원을 검토한다. 부팅/실행 중 시각이
불확실할 때 삭제를 자동 보류하는 **영구 gate는 아직 구현·활성화하지 않았다**. 이 작업은 기존
반복 승인 범위를 바꾸므로 별도 후보·격리 시험·승인을 필요로 한다. 이번 runtime의 clock 상태를
확인하지 않는 기존 정리기가 자동 보호된다고 주장하지 않는다. forget/prune·시험 공간 삭제도 없다.
