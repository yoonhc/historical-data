# 두 번째 왕복 이후 설치 작업

고정 후보 `system-bootstrap-20261007-f85eb8321a19c1ee`와 기존 [전환 승인](approval.json)을 적용한다.
후보의 코드·정책·unit 해시를 바꾸지 않는다. 이 문서는 실행 계획이며 실제 결과는 root 작업 기록으로 별도 확인한다.

## 첫 실행 중단과 이어가기

첫 관리자 실행은 **23:39:46 패키지 취득 단계에서 중단**됐다.
[최초 결과](install-first-result.json)와 [명령 기록](install-first-operations.jsonl)을 보존한다.
`apt-get --no-download install <local.deb>`가 로컬 패키지 취득도 진행하지 못한 설치 스크립트의 문제다.
고정 release/venv/설정·unit 파일과 Chrony 임시 mask는 설치됐지만, 패키지 교체·ledger 등록에는 도달하지 않았다.
23:41 점검에서 timesyncd active, Chrony masked/inactive, 기존 업무 20개 보류를 확인했다.

**아래 첫 설치 명령은 다시 실행하지 않는다.** 동일한 부분 설치 상태와 실제 파일 해시를 확인한 뒤,
패키지 취득 옵션을 수정하여 남은 절차만 수행하는 명령은 다음과 같다.

```bash
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/system-cutover-2026-10-08/continue_system_runtime.py --run
```

[후속 입력](install-continue-inputs.json)은 첫 실패 결과·원래 설치기·설치 receipt와 후속 코드를 고정한다.
APT 변경이 여전히 timesyncd 제거/검증 버전 Chrony 설치 두 항목뿐인지 다시 검사한다.
원래 코드·정책·유닛은 바꾸지 않으며 첫 결과도 덮어쓰지 않는다. 결과는 root 작업 폴더의 `result-continuation.json`이다.
후속 작업도 자동 재부팅하지 않는다.

두 번째 관리자 실행은 **23:48:13 `chronyd.service` 별칭을 찾지 못한 유닛 검사에서 중단**됐다.
[두 번째 결과](install-continuation-result.json)에 기록했다. 패키지 교체·ledger의 최초 installation hold 등록,
8개 업무쌍의 원래 파일 복원·공통 gate 연결은 끝났고, 일반 계정 gate가 예상한 hold로 거부했다.
Chrony를 enable하면 등록되는 배포판 Alias를 의존 관계 검사보다 먼저 만들었어야 하는 설치 순서 문제다.
이 시점에는 Chrony와 새 수집기 모두 inactive였으며 ledger는 원래 boot의 hold/step 0을 유지했다.

패키지·ledger 작업을 반복하지 않고 남은 순서만 수행하는 현재 명령은 다음과 같다.

```bash
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/system-cutover-2026-10-08/finish_system_runtime.py --run
```

[완료 단계 입력](install-finish-inputs.json)은 현재 부분 설치·ledger·실패 기록과 코드를 고정한다.
Chrony 별칭 등록을 먼저 수행하고 원래 유닛 검사·실제 권한 점검·다음 부팅 준비를 이어간다.
이전 결과는 보존하고 `result-finish.json`에 별도 결과를 남긴다. 고정 후보 정책과 보호 옵션은 유지한다.

## 확인한 전제

- [두 번째 Windows 결과](windows-second-result.json): UTC flag/한국 시간대 유지, 최초 서비스 미시작 오류
  `0x80070426`, 이후 time.windows.com 동기화 성공(23:07:25 KST)을 모두 보존한다.
  Windows 부팅 직후 자동 동기화 이전의 표시 시각은 입증하지 않았다.
- [Linux 복귀](linux-second-return-detail.json): 새 boot ID, RTC UTC 유지, 실제 RTC와 시스템 UTC 근접성,
  이번 부팅의 외부 동기화(23:12:56 KST), 수집 프로세스 부재를 확인했다. 20개 user unit은 모두 보류다.
- [설치 입력](install-inputs.json)의 원래 archive·배포 계획·설정 해시를 고정한다.
  운영 의존성 12개와 실행 파일 35개는 [준비 경로 receipt](runtime-build-receipt.json)가 기존 후보 receipt와 일치했다.
  최종 venv는 `/opt`의 실제 설치 위치에서 다시 생성한다. 준비 venv를 복사하지 않는다.
- [Chrony 패키지](staged-package.json)는 검증한 4.5-1ubuntu4.2다. APT 모의 결과는 timesyncd 제거/Chrony 설치뿐이다.
  되돌리기용 [동일 timesyncd 패키지](timesyncd-rollback-package.json)는 Ubuntu 공식 스냅샷에서 확보하고,
  현재 설치의 payload 체크섬 10개를 대조했다. 최신 systemd 패키지 묶음을 추가 업그레이드하지 않는다.

## 관리자 실행이 하는 일

```bash
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/system-cutover-2026-10-08/install_system_runtime.py --run
```

1. 현재 부팅·마운트·기존 보류·UTC/NTP·입력 해시를 재검사한다. 기존 설치나 ledger가 있으면 덮어쓰지 않는다.
2. `/opt/marketdata/releases/<release-id>`에 소스를 검증해 설치하고, 별도 root 캐시와 고정 lockfile로
   비 editable venv를 offline 생성한다. 기존 후보 receipt와 대조하고 root 소유/일반 계정 수정 불가로 확정한다.
3. 프로필·UTC 근거·검증한 설정/유닛을 설치한다. Chrony/alias/wait에 설치 전 persistent mask를 적용한다.
   패키지 교체 동안 기본 설정의 Chrony 기동을 막고 기존 timesyncd가 종료됐는지 확인한다.
4. root ledger를 **최초 한 번 `installation_hold`로 등록**한다. 현재 부팅의 step이나 수집은 시작하지 않는다.
   기존 업무 8쌍의 원래 파일/링크를 복원하되 새 공통 대기와 최종 검사를 연결한다.
5. 임시 Chrony mask만 해제하고 실제 no-step 설정으로 Chrony를 시작한다. root/일반 사용자 각각 최종 보호 옵션 아래에서
   실제 소켓·manager·공유 잠금·데이터 접근을 확인한다. 일반 계정의 현재 boot gate 거부도 확인한다.
6. 다음 부팅의 bootstrap/수집 자동 시작을 설정하고 이전 enabled 업무 timer 8개를 재개한다.
   현재 업무는 installation hold의 공통 대기 뒤에 있어야 한다. 현재 bootstrap/collector는 실행하지 않는다.
7. 실제 유닛과 gate를 다시 검사한다. 원래 두 user collector와 자동 정리의 service/timer는 계속 mask다.

원본·revision·manifest/source_root·checkpoint/restic 지점·시험 공간·복구 키트·정리 grant는 변경하지 않는다.
현재 부팅에서 자동 step을 실행하거나 ledger를 재등록하지 않는다. **스크립트는 재부팅하지 않는다.**

## 결과와 실패 시 처리

명령·상태·출력은 `/var/lib/marketdata-cutover-20261008/operations.jsonl`,
최종 결과는 같은 폴더 `result.json`에 남는다. 일부 단계 실패 시 즉시 멈추고 기록과 설치 상태를 보존한다.
실패를 숨기기 위해 같은 명령을 무조건 재실행하거나 ledger를 삭제하지 않는다.

성공 뒤 에이전트가 실제 Chrony 제어 옵션/AppArmor·현재 시간원·설치 경로/권한·21개 user inventory와
다음 부팅 설정을 점검한다. 그다음 승인된 최종 Linux 재부팅을 별도 인계한다.
재부팅 이후 실제 새 boot의 준비 완료·수집·저장까지 확인하기 전에는 전체 전환 완료라고 기록하지 않는다.

되돌리기가 필요하면 새 수집 기동을 보류하고 업무를 먼저 멈출 수 있는 상태인지 확인한 뒤 진행한다.
Chrony와 timesyncd를 동시에 기동하지 않고, 보관한 동일 timesyncd 패키지와 원래 유닛으로 복구할 수 있다.
UTC RTC 규칙·Windows flag는 시간원 교체와 별개로 유지한다. ledger·기존 원본·사본·grant는 삭제하지 않는다.
