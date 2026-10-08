# 실제 운영 전환의 최종 검토 범위

**root 접근 사전 점검은 통과했다. 아래 운영 변경은 아직 승인·실행되지 않았다.**
고정 후보를 바꾸지 않고 다음 승인에 포함할 작업과 중단 경계를 정한다.
구현 세부 절차는 기존 [deployment-review.md](../system-runtime-bootstrap-revision-2026-10-07/deployment-review.md)를 유지한다.

| 고정 대상 | 값 |
|---|---|
| release ID | `system-bootstrap-20261007-f85eb8321a19c1ee` |
| source archive SHA-256 | `1960c8b743d377170e0683678dde4078895be2650958ead027f5fd7fe9a4dfa1` |
| deployment plan ID | `1784221fd075bbf7e59e99b521ce038bae2ce99bbbee06dfe4684387a300d78e` |
| 소스/배포 파일 | 172개/20개, 원래 manifest 유지 |
| 검토한 Chrony 패키지 | Ubuntu `4.5-1ubuntu4.2`; 다른 버전이면 자동 채택하지 않고 차이 확인 |
| 사용자/데이터 루트 | yoonhc(1000), `/home/yoonhc/projects/algoTrade/historical_data` |
| 데이터 filesystem UUID | `d8020c0b-a5df-4e47-99d9-2378b12ab2ea`; 전환 시 재확인 |

## 전환 승인에 포함할 작업

| 순서 | 실제 변경/담당 | 다음 단계로 진행할 조건 |
|---|---|---|
| 1. 작업창 준비 | Linux에서 기존 source/units/links/masks/enabled 상태·설정·PID·grant 기록. 최신 완료 checkpoint와 restic 처리·검사 결과 각각 확인 | 기존 daily update의 waiting_source/exit 2를 별도로 대조하고, 최근 복구 근거·백업 목적지 마운트/UUID·저장소 ID가 확인됨 |
| 2. 업무 보류·수집 종료 | Linux에서 8개 업무 timer와 자동 checkpoint 정리의 새 예약 보류. 진행 중 작업 자연 종료 확인. 옛 depth·aggTrade를 SIGINT 경로로 종료 | 최종 저장 범위/종료 보고·PID/cgroup/stream lock 확인. Windows 왕복 부팅에도 자동 시작하지 않도록 기존 unit을 보존하고 persistent mask 적용 |
| 3. Windows RTC 규칙 변경 | 사용자가 Windows에서 기존 registry 값·시간대를 기록하고 `RealTimeIsUniversal`을 REG_DWORD 1로 설정. Windows 실제 재시작과 동기화 확인 | 값 존재만으로 완료 처리하지 않음. 그동안 Linux 업무는 계속 보류 |
| 4. Linux RTC UTC 전환·왕복 확인 | Linux로 돌아와 기존 timesyncd의 외부 동기화와 정확한 시스템 시각 확인 후 `timedatectl set-local-rtc 0`. Windows/Linux로 다시 부팅해 양쪽 확인 | `--adjust-system-clock` 사용하지 않음. 정확한 시스템→RTC 방향, `/etc/adjtime=UTC`, LocalRTC=no. KST 표시 유지. 확인된 사실로 RTC 확인 자료 작성 |
| 5. 패키지·고정 실행 환경 전환 | Chrony가 기본 설정으로 자동 기동하지 못하게 설치 전 임시 persistent mask. 패키지 교체와 고정 release/비 editable venv/프로필/유닛 설치 | timesyncd와 Chrony가 동시에 제어하지 않음. 실제 경로·권한·AppArmor·유효 설정 검사. 설치 부팅은 ledger의 installation_hold로 등록 |
| 6. 최종 Linux 부팅·재개 | 새 bootstrap·두 system collector의 부팅 자동 시작 설정 후 승인한 Linux 재부팅. 8개 업무는 새 공통 wait 뒤에서 재개 | 현재 부팅/provider/시간원 검증, 필요한 경우 부팅당 최대 1회 보정, 준비 완료 뒤 UID 1000 수집 시작. 옛 user collector와 중복 없음 |
| 7. 실제 수집·업무 확인 | 새 프로세스 import/receipt·원문/표준 저장·depth 대조·aggTrade 연결·시각 기록 확인. 첫 정상 세션 종료와 다음 시작 관찰 | 실제 발생한 기록만 완료로 집계. missed timer 기동과 업무 성공 구분. checkpoint/restic도 각 ID와 결과 대조 |

8개 업무는 `backup`, `daily`, `oi`, `oi-probe`, `oi-quality`, `pack`, `restic-archive`,
`network-observer`다. 기존 본 명령·재시도 한도·타이머 주기는 유지하고 시작 의존 관계와 검사를 연결한다.
오래된 unit 파일/링크는 별도 보관 후 mask하며 삭제로 대체하지 않는다.

## 설치 경로와 시각 변경

코드/venv는 `/opt/marketdata/releases/<release ID>`, 설정은 `/etc/marketdata/runtime.json`과
`/etc/marketdata/clock-bootstrap.json`, 시각 설정은 `/etc/chrony/marketdata.conf`다.
부팅 상태는 `/var/lib/marketdata-clock`, 시각 근거는 기존 루트의 `state/runtime-clock`에 둔다.
기존 data root·manifest·source_root·원문·revision·백업 사본을 수정하거나 이동하지 않는다.

steady Chrony에는 자동 makestep을 두지 않는다. 후보의 초기 준비 절차만 시간원 조건을 확인한 뒤
남은 오차가 100ms 초과·300초 이하인 경우 최대 한 번 요청할 수 있다. 300초 초과·시간원 불충분은
자동 통과시키지 않는다. 결과 unknown은 보존하며 재요청으로 해결하지 않는다.
설치한 현재 부팅의 ledger를 삭제하거나 재-enroll하여 보정 권한을 열지 않는다.

RTC 규칙 전환과 Chrony 설치에는 시스템/하드웨어 시각 변경 권한이 필요하다. 이번 읽기 전용
tracking 시험의 승인에 포함되지 않았다. 최종 승인에는 Windows 설정, Linux 시스템 시각 동기화·RTC 반영,
Chrony 제어, 수집 공백·재부팅을 명시한다.

## 수집 공백과 작업창

공백은 두 스트림 정상 종료부터 새 시스템 collector가 준비 확인 후 실제 수신을 재개할 때까지다.
Windows 작업·Windows 재시작·Linux 복귀·RTC 확인용 왕복·설치 후 최종 Linux 재부팅이 포함된다.
현재 소요 시간을 측정하지 않았으므로 몇 분에 끝난다고 보장하지 않는다. 실행 중 백업과 유지보수가
끝난 후 시작하고, 사용자에게 Windows 작업을 수행할 시간이 확보된 창으로 잡는다.

중단 중 확보하지 못한 실시간 호가 원문은 확보한 것으로 채우지 않는다. 재개 뒤에도 공백과
기존 partial을 유지한다. aggTrade·봉 등의 후속 보강은 가능한 원천/범위를 따로 검증한다.
수집 재개 후 첫 1시간 세션 종료 관찰은 전환 중단 시간과 별도로 기록한다.

## 자동 삭제·승인 자료

전환할 때 자동 checkpoint 정리 service/timer는 보류하며, **상시 시각 검사 후보가 별도 검증·승인될 때까지
자동 재개하지 않는다.** 현재 grant 해시 `6b630689fcf4bb11294711299d63f09238a6a3de2a5704a13599b913fe3f9be0`는
수정하지 않는다. 기존 백업/수집의 재개와 자동 삭제 재개를 구분한다.
forget/prune·원본 삭제·시험 공간 삭제·복구 키트 변경은 전환 범위에 포함하지 않는다.
이번 점검에서는 기존 정리 timer를 중지하지 않았다. 위 보류는 앞으로 승인받을 전환 작업이다.

## 설치 시 남는 검사와 중단 조건

이번 시험은 같은 보호 옵션에서 전용 경로를 사용했다. 실제 `/run/chrony`의 `_chrony` 소유권,
패키지 AppArmor, 실제 venv/설정 경로, 운영 시간 제어가 켜진 Chrony의 상태는 설치 과정에서 다시 확인한다.
그 확인을 위해 보호 옵션이나 일반 계정 권한을 완화하지 않는다.

Windows/Linux UTC 확인, 패키지/해시, 경로/마운트, 최신 복구 근거, 공유 잠금/업무 정지 조건 중 필요한
전제가 맞지 않으면 다음 변경을 멈추고 기록을 보존한다. ledger나 manifest/source_root 수정으로
통과시키지 않는다. 수집 재개 시 실제 import·UID·중복 PID·현재 시각을 확인한다.

## 되돌리기

새 수집 서비스를 먼저 종료하고 저장·PID·lock을 확인한 후 보관한 옛 unit을 복원한다.
현재 시각과 시작 조건을 확인하기 전에는 옛 수집도 시작하지 않으며, 양쪽을 동시에 실행하지 않는다.
공통 waiter는 Requires로 연결된 업무를 멈출 수 있으므로 실행 중 업무를 둔 채 단독 stop하지 않는다.
관련 timer의 새 요청 보류와 업무 자연 종료/대기 job 확인 후 새 연결만 해제한다.

Chrony→timesyncd 복귀와 RTC UTC→LOCAL 복귀는 별도다. 시간원 프로그램만 되돌린다고 RTC 규칙을
되돌리지 않는다. RTC 복귀가 필요하면 양 OS의 해석과 내용을 함께 확인한다.
boot ledger·의도/unknown 기록·기존 데이터·사본·grant를 지우지 않는다.

전환 이후에도 시각 로그의 보존/복원·collection 연결과 자동 삭제의 영구 시각 검사는 필수 후속이다.
이번 전환 승인에 그 구현이나 삭제 재개 권한을 포함하지 않는다.
