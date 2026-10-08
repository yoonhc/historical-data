# Root 접근 사전 점검 완료 — 운영 전환은 미적용

고정 후보 **`system-bootstrap-20261007-f85eb8321a19c1ee`**에 대해, 2026-10-07
**22:52:29~22:52:30 KST** 실제 관리자 권한의 격리 접근 시험을 통과했다.
운영 시각 변경·RTC 쓰기·패키지 설치·수집 중지·재부팅은 수행하지 않았다.

근거: [root 실행 결과](result-1f383e4dff67457d.json), [결과 대조](validation.json),
[별도 승인할 전환 범위](transition-review.md). 기존 후보의 소스 172개·배포 파일 20개·아카이브 해시는 유지된다.

## 실제 확인한 접점

| 항목 | 관측 결과 |
|---|---|
| 최종 bootstrap 보호 옵션 | ProtectClock/ProtectSystem/ProtectHome/PrivateTmp/NoNewPrivileges/capabilities/address families/권한 설정 일치. StateDirectory·socket 경로만 시험 전용으로 치환 |
| root 프로세스 | UID/GID 0, CapEff/CapBnd `0xc2`(DAC_OVERRIDE/SETGID/SETUID), NoNewPrivs=1, seccomp 적용, CAP_SYS_TIME 없음 |
| Chrony Unix socket | 실제 `chronyc ... -m 'retries 0' tracking` 종료 0 |
| client 응답 소켓 | inotify로 `chronyc.287438.sock` 생성·삭제 확인, 종료 후 잔존 소켓 없음 |
| yoonhc manager | 후보의 Host.user_command로 실제 사용자 manager의 show/list-jobs·depth 상태 조회 성공 |
| 별도 상태 파일 | 후보 Ledger.save의 실제 파일 fsync·디렉터리 fsync 성공, root:root 0644 |
| 일반 계정 공유 잠금 | root 배타 잠금 중 획득 거부, root 공유 잠금과 동시 획득 허용. UID 1000·effective/permitted capabilities 0, 쓰기 open 거부 |

시험용 daemon은 기존 추출 Chrony 4.5를 `-x`로 실행했다. CapEff/CapPrm/CapBnd 모두 0,
ProtectClock·PrivateDevices·PrivateNetwork 적용, RTC device 미노출, NTP/command UDP port 0이었다.
설정에는 시험 PID/Unix socket 경로만 추가했으며 시간원·RTC·외부 include는 없다.
응답은 `Not synchronised`였다. **접근 성공이며 실제 시간 동기화 성공이 아니다.**
`-x`의 의미는 [Chrony 4.5 공식 문서](https://chrony-project.org/doc/4.5/chronyd.html)에 근거한다.

## 최초 실패와 수정 범위

[첫 실행](result-a6ff5d22404f47b3.json)은 22:46:22 KST에 시험 바이너리를 `/run`에서
실행하려다 EACCES로 중단됐다. 호스트 `/run`은 `noexec`이며, 시험 유닛은 아직 시작되지 않았다.
시험 스크립트의 실행 파일 배치 오류였고, 해당 파일·상태 폴더 정리는 완료됐다.

[첫 스크립트](harness-v1/)와 실패 기록은 보존했다. 재실행에서는 코드만 새 root 소유
`/opt/md-root-preflight-<ID>`에 두고, 소켓은 `/run/.../chrony`, 상태는 `/var/lib/...`에 두었다.
`/run`의 noexec와 기존 마운트 옵션은 유지했다. 후보 코드·유닛 보호·계정 권한은 변경하지 않았다.

## 정리와 운영 보존

두 시험 transient unit은 `not-found`, 시험 `/opt`·`/run`·`/var/lib` 폴더는 모두 제거됐다.
정리 오류는 0건이다. stop 후 이미 사라진 transient unit에 대한 reset-failed는
`unit not loaded`를 반환했다. 최종 unit 부재를 별도로 확인했으므로 정리 실패로 해석하지 않는다.
host journal과 보고서·재현 스크립트는 검증 근거로 남겼다.
[후속 읽기 전용 조회](postflight-observation.json)에서도 두 unit의 not-found·MainPID 0과
시험 daemon/probe PID의 부재를 다시 확인했다.

전후 379개 최상위 경로(일부 설정 디렉터리는 자식까지)의 해시·권한·링크 대조에 차이가 없었다.
timesyncd·패키지 상태, 사용자 unit 등록/설정, RTC 해석, 정리 grant, 계정/그룹도 유지됐다.
depth PID 283756·aggTrade PID 284121 및 각각의 invocation이 전후 동일했다.
원문/카탈로그/백업 내용의 전수 검사는 이번에 수행하지 않았고 기존 정기 쓰기는 계속됐다.

## 별도로 발견한 기존 상태

실제 사용자 manager의 `SystemState=degraded`도 숨기지 않았다. 시험 시작 전·후 모두
`marketdata-daily.service`에 동일한 실패 invocation이 남아 있었다.
22시 root 시험보다 앞선 **12:27:45~12:27:51 KST** 실행의 [journal 발췌](postflight-observation.json)에서 10/6 UTC 선물 봉·mark/index·OI
아카이브가 `waiting_source`로 기록돼 결과가 partial이고 종료 코드가 2였다.
12:28:52에 기존 재시도/시작 제한에 도달했다. update CLI는 incomplete 결과에 2를 반환한다.
systemd의 `INVALIDARGUMENT` 표기만으로 잘못된 CLI 인자를 원인으로 단정하지 않는다.

이번에는 재실행·reset-failed·설정 변경을 하지 않았다. **현재 원격 파일 제공 여부는 미확인**이다.
이 서비스는 데이터 update 작업이며, checkpoint를 만드는 `marketdata-backup.service`나
restic 작업의 실패와 같은 의미로 취급하지 않는다. 운영 전환 전 이 미완료 update와 최신 백업 상태를
각각 대조한다. root 접근 통과를 모든 업무 정상의 근거로 확대하지 않는다.

## 아직 운영 전환에서 확인할 것

- 실제 `/run/chrony`와 `_chrony` 소유권·패키지 AppArmor/daemon 보호 설정.
- 실제 `/opt/marketdata/releases/<ID>` 비 editable venv, `/etc/marketdata`, 운영 상태 경로의 권한.
- Windows/Linux UTC RTC 왕복, 외부 시간원과 정상 시간 제어를 사용하는 Chrony.
- 새 부팅의 준비 순서·한 번 보정 계약·업무 재개·중복 수집 방지·실제 저장과 첫 세션 종료.

이번 시험은 bootstrap의 시각 보정 경로를 실행하지 않았다. 자동 정리의 상시 시각 검사와
시각 로그의 백업/복원·collection 연결도 별도 미완료 작업이다.
