# Root 보호 환경의 제한된 접근 시험

대상은 `system-bootstrap-20261007-f85eb8321a19c1ee`이며 후보 자체는 변경하지 않는다.
이번 승인은 접근 시험에 한정된다. 운영 전환·시각 변경·패키지 설치 승인이 아니다.

현재 상태: 시험 스크립트와 입력을 준비했다. `sudo -n true`는 관리자 암호를 요구했다.
**실제 root 시험 통과 여부는 아직 확인하지 않았다.**

## 실제 실행할 내용

- 기존 추출 바이너리 Chrony 4.5를 해시 확인 후 root 소유의 새 `/run/md-root-preflight-<무작위값>`에 복사한다.
- 임시 daemon은 `-x`, 빈 capability bounding set, `ProtectClock=yes`, `PrivateDevices=yes`, `PrivateNetwork=yes`로 제한한다.
  별도 설정에는 전용 PID/Unix socket 경로와 `port 0`, `cmdport 0`만 있다. 외부 시간원, RTC 설정, 설정 include가 없다.
  `-x`는 시스템 시각 제어를 비활성화한다([Chrony 4.5 공식 문서](https://chrony-project.org/doc/4.5/chronyd.html)).
- 별도 root probe는 최종 bootstrap의 NoNewPrivileges, CapabilityBoundingSet, ProtectClock, ProtectSystem,
  ProtectHome, PrivateTmp, RestrictAddressFamilies, UMask, StateDirectoryMode를 그대로 적용한다.
  쓰기 예외와 StateDirectory 이름만 시험 전용 경로로 치환한다. 실효 설정도 조회·비교한다.
- 실제 `chronyc -n -h <시험 socket> -m 'retries 0' tracking`을 호출한다. inotify로
  해당 PID의 응답 소켓 생성·삭제를 확인한다. 시간원이 없는 시험 daemon의 미동기화 응답은 정상이다.
  이 시험은 접근 시험이며 시간 동기화 성공의 근거가 아니다.
- 후보의 `Host.user_command`로 실제 yoonhc manager의 show/list-jobs만 읽는다.
- 후보의 `Ledger.save`와 `Ledger.lock`으로 별도 `/var/lib/md-root-preflight-<무작위값>`에서 파일·디렉터리
  fsync 성공, root 배타 잠금 중 사용자 공유 잠금 거부, 공유 잠금의 동시 허용, 사용자 쓰기 거부를 확인한다.
  fsync 관측 wrapper는 실제 syscall을 실행하고 성공 후에만 기록한다.

`makestep`, `date`, `hwclock`, enroll/bootstrap/check 명령은 호출하지 않는다. 운영 ledger도 사용하지 않는다.
apt, 운영 서비스 stop/restart/daemon-reload, 사용자 권한 변경, Windows 설정, 재부팅을 수행하지 않는다.
시험 unit에는 운영 unit을 시작하거나 멈추는 의존 관계가 없다. 시험 재시작은 비활성화한다.

## 관리자 실행

암호는 대화에 보내지 않고 로컬 터미널의 sudo 입력창에만 입력한다.

```bash
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/system-runtime-root-preflight-2026-10-07/preflight.py --run
```

실행 전에 일반 계정으로 입력 해시를 확인할 수도 있다.

```bash
/usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/system-runtime-root-preflight-2026-10-07/preflight.py --check
```

결과는 같은 보고서 폴더의 `result-<무작위값>.json`에 일반 계정 권한으로 새로 생성한다.
기존 결과를 덮어쓰지 않는다. 보호 설정의 접점 실패는 기록하며, 통과시키려고 설정을 낮추지 않는다.

## 정리와 보존 확인

정상 종료·시험 실패·SIGINT/SIGTERM 때 시험의 두 transient unit만 중지한다. 해당 namespace와 root 소유,
무작위 소유 표식을 확인한 뒤 이번에 만든 `/run`·`/var/lib` 시험 폴더만 제거한다.
시작한 시험 unit이 남으면 폴더를 지우지 않고 정리 오류로 기록한다. 호스트 journal의 시험 기록은 증거로 남긴다.
SIGKILL·전원 장애 시에는 finally 실행이 보장되지 않는다. 시험 daemon은 별도 180초 실행 제한을 가지며,
그런 중단이 생겼다면 결과의 시험 ID와 경로를 확인한 뒤 그 시험만 정리해야 한다.

전후에 고정 후보·운영 소스·사용자 유닛·시각 설정·RTC 해석 설정·계정/그룹·정리 승인 자료의 해시와
권한을 비교한다. timesyncd/패키지 상태, 사용자 유닛 등록 상태와 두 collector의 invocation도 기록한다.
정기 업무에 의한 데이터 추가는 계속 허용하며, 전체 데이터 파일이 전후 동일하다고 주장하지 않는다.

## 최종 설치에서 별도로 남는 확인

- 시험 `/run/.../chrony` 대신 실제 `/run/chrony`, 실제 `_chrony` 소유권, 패키지 AppArmor/daemon sandbox.
- 실제 `/opt` 배포본·venv와 `/etc/marketdata` 설정의 전체 경로/권한. 시험은 `/usr/bin/python3`와 root 소유 복사본을 사용한다.
- 실제 `/var/lib/marketdata-clock`의 등록·부팅 상태 전이. 시험 파일은 운영 부팅 상태가 아니다.
- 정상 시간 제어가 켜진 Chrony와 실제 외부 시간원의 품질, Windows/Linux UTC RTC 왕복 부팅.
- 실제 시스템 유닛의 부팅 의존 관계, 업무 재개, 수집 저장·종료. 이번 root 시험은 운영 업무를 시작하지 않는다.

위 접점 시험 통과 후에만 운영 전환 범위를 최종 정리한다. 실제 전환은 별도 승인이 필요하다.
