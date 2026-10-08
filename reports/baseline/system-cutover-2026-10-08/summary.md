# 실제 운영 전환 — 2026-10-08

대상은 고정 후보 **system-bootstrap-20261007-f85eb8321a19c1ee**다.
사용자가 **“지금 시작 — Windows 작업 가능”**으로 전환 작업창과 그동안의 수집 공백을 승인했다.
[승인 근거](approval.json)와 [전체 전환 범위](../system-runtime-root-preflight-2026-10-07/transition-review.md)를 연결한다.
아래 시각은 KST다. 21:57 첫 Windows 인계 준비를 완료했고, **첫 Windows 작업 후 Linux로 복귀했다.**
**22:53 Linux RTC UTC 반영 후 두 번째 Windows/Linux 왕복과 23:15 복귀 검증을 완료했다.**
**23:52 고정 배포본·Chrony 설치와 실제 접근 검사가 완료됐다.** 당시 부팅은 installation hold였으며,
**23:57 시각 준비 기준 통과, 23:58 최종 확인 후 Linux 재부팅 인계를 준비했다.**

**10/9 최종 재부팅 후 시각 준비·수집 시작·실제 저장 확인을 완료했다.**
[부팅 결과](post-reboot-summary.md)에 새 boot의 `closed`·step 0회, 두 system collector의 단독 실행,
호가/aggTrade 저장 검증과 기존 예약 업무 재개를 기록했다. 전환 중단 구간의 aggTrade 264,069건은 아직 미복구이며,
00:20:43 호가 범위 소진 1건은 자동 복구 후에도 품질 손실로 유지된다. 시각 위반은 관측되지 않았다.
첫 1시간 세션 종료·오늘 새 checkpoint/restic는 후속이다. 자동 checkpoint 정리는 계속 masked다.
아래는 각 단계 당시 기록이며, 현재 인계 상태는 [handoff-current.json](handoff-current.json)을 따른다.

## 실제 설치 결과와 남은 부팅 검증

[최종 설치 결과](install-final-result.json)는 23:52:42 `passed=true`다.
처음 두 번의 중단도 보존했다. 첫째는 로컬 .deb 취득을 막은 설치기의 `--no-download` 옵션,
둘째는 `chronyd.service` Alias 등록보다 앞서 의존 관계를 검사한 설치 순서 문제였다.
[첫 실행](install-first-result.json), [두 번째 실행](install-continuation-result.json),
[전체 명령 기록](install-final-operations.jsonl)에 원인·중단 위치와 후속 통과를 구분한다.
후보 코드·시각 정책·보호 옵션을 바꾸거나 ledger를 삭제/재등록하여 통과시키지 않았다.

- `/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee`에서 비 editable venv를 생성했다.
  [설치 receipt](installed-runtime-manifest.json)의 Python 3.12.3, 운영 의존성 12개, 실행 파일 35개 해시가 고정 후보와 일치한다.
- timesyncd 255.4-1ubuntu8.16을 제거하고 Chrony 4.5-1ubuntu4.2를 설치했다. 기본 Chrony의 자동 기동은
  임시 mask로 막았고, 검토한 설정과 RTC 검사·ledger 설치 이후에만 새 daemon을 시작했다.
- 새 ledger는 이 부팅의 `installation_hold`, `step_attempts=0`이다. 현재 bootstrap을 실행하거나
  큰 보정을 요청하지 않았다. 일반 계정 gate가 정확히 installation hold 사유로 거부했다.
- 실제 root 보호 환경에서 `/run/chrony` tracking·응답 소켓 정리, 사용자 manager의 21개 유닛 감사,
  공유 잠금·RTC 확인이 통과했다. collector와 같은 보호 환경의 UID 1000 접근·실제 venv import·데이터 경로·DNS도 통과했다.
- 원래 업무 8쌍은 보존한 파일/링크 그대로 복원하고 공통 대기와 마지막 검사를 연결했다. timer 8개는 원래 주기로
  재개했지만 본 업무는 공통 wait 뒤에서 대기했다. 이를 업무 성공이나 새 백업 성공으로 세지 않는다.
- 새 bootstrap/두 system collector는 다음 부팅 자동 시작만 설정했다. 옛 user collector 2개와
  자동 checkpoint 정리 service/timer 2개는 계속 persistent mask다. 기존 grant는 유지했다.

[23:54 실제 설치 관측](installed-observation.json)에서 Chrony의 지정 config·`-x` 없는 명령행,
`/usr/sbin/chronyd (enforce)` AppArmor, seccomp level 1, 현재 시스템/RTC UTC와 Linger=yes를 확인했다.
root bootstrap/새 collector는 enabled지만 inactive이며 아직 실제 수집 프로세스를 시작하지 않았다.
초기 접근 검사의 `Not synchronised`는 소켓 접근 성공만 뜻하고 시간 품질 통과로 세지 않았다.
23:54에는 외부 시간원·Leap Normal이 확인됐지만 skew/조건부 오차 상한이 기준 밖이었다.
[시각 관찰 기록](installed-clock-observations.jsonl)은 기존 기준을 그대로 적용해 후속 상태를 기록한다.
23:57:07에는 ready=true, 남은 보정 약 3.96ms·조건부 오차 상한 약 151.23ms·skew 47.123ppm으로 기준을 통과했다.
추가 NTP burst/step이나 기준 완화는 하지 않았다. 이 값은 운영 준비 판정이며 메시지별 시각 정확도 인증이 아니다.

[23:58 보존·권한 확인](installed-preservation.json)에서 root 소유/일반 사용자 수정 불가,
설치 소스 172개·원래 설정/승인 해시 일치, 업무 8개 모두 MainPID/ControlPID 0과 시작 이력 없음,
접근 시험 transient unit 3개 not-found를 확인했다. 제한된 도구 namespace가 root UID를 65534로 표시한
첫 소유권 조회는 별도 보존하고 실제 host namespace에서 UID 0을 재확인했다. 이를 고치려고 chown하지 않았다.

[최종 Linux 재부팅 안내](final-linux-reboot-handoff.md)를 준비했다. 새 boot의 gate 완료·수집/저장·첫 세션 종료는
아직 미완료다. 현재 업무 start job은 공통 wait 뒤에서 대기하며, 이것을 수행 완료로 집계하지 않는다.

## 두 번째 왕복과 설치 준비

[두 번째 Windows 기록](windows-second-result.json)은 `REG_DWORD 0x1`·`Korea Standard Time` 유지,
최초 `w32tm` 서비스 미시작 오류 `0x80070426`, 이후 Leap indicator 0·Stratum 5·`time.windows.com,0x8`
동기화 성공(23:07:25)을 구분한다. **자동 동기화 이전 Windows의 정확한 표시 시각은 입증하지 않았다.**
최초 오류가 왜 언제 해소됐는지까지 추정하지 않는다.

[Linux 두 번째 복귀 기록](linux-second-return-detail.json)의 boot ID는 `9e33f0bc-b734-45c9-8a85-8aa6c35efa71`이다.
`LocalRTC=no`, `/etc/adjtime=UTC`, `Asia/Seoul`이 유지됐고,
RTC `14:15:52 UTC`와 시스템 `14:15:52.316622 UTC`가 초 단위 읽기 범위에서 근접했다.
timesyncd는 이번 부팅 23:12:56 KST에 외부 동기화를 완료했다. 20개 unit의 persistent mask,
업무/수집 프로세스 부재와 기존 두 파일시스템 UUID를 재확인했다.

이 근거와 미확인 범위를 명시한 [RTC 확인 자료](rtc-utc-confirmation.json)를 준비했다.
Windows 사용자 확인과 Linux 실제 관측을 구분하며 Windows의 부팅 직후 정확도를 인증하지 않는다.
고정 배포본은 [설치 준비 receipt](runtime-build-receipt.json)의 12개 운영 의존성·35개 실행 파일 해시가
기존 검증 receipt와 일치했다. 재부팅으로 없는 캐시는 고정 lockfile 버전으로 다시 받았다.

APT 모의 결과는 현재 timesyncd 제거와 Chrony 4.5-1ubuntu4.2 설치뿐이었다.
현재 timesyncd 동일 버전이 최신 APT 인덱스와 일반 pool에서 사라져,
공식 Ubuntu 2026-06-10 스냅샷에서 [동일 버전 되돌리기 패키지](timesyncd-rollback-package.json)를 확보했다.
설치된 payload 체크섬 10개와 대조했다. 이 준비 과정에서 패키지나 시간 서비스를 변경하지 않았다.

[설치 작업 안내](installation-step.md)와 [고정 입력](install-inputs.json)을 준비했고,
관리자 실행 스크립트의 구문·정적 검사 및 입력 검증이 통과했다. 실제 root 설치는 사용자 인증 실행 결과로 별도 기록한다.
설치 부팅의 `installation_hold`, 현재 bootstrap 미실행, 재부팅 미수행, 자동 정리 보류를 명시했다.

## 첫 Windows 완료와 Linux 복귀

[사용자 확인 기록](windows-first-result.json): Windows 실제 재시작 뒤 `RealTimeIsUniversal=REG_DWORD 0x1` 유지,
`Korea Standard Time`, `w32tm /resync` 성공, Leap indicator 0·Stratum 5·`time.windows.com,0x8`,
마지막 동기화 **22:40:10**을 전달받았다. Linux가 Windows를 직접 조회한 근거로 표현하지 않는다.
변경 전 registry 값은 전달되지 않아 미확인으로 남긴다.

새 Linux boot ID는 `656ec5e3-7734-455f-9214-1326c988f837`이다.
[복귀 점검](resume-observation-656ec5e3-7734-455f-9214-1326c988f837-20261008T224913413522.json)에서
20개 unit 모두 persistent masked/비활성, 사용자 manager 대기 작업 없음, 원본·백업 UUID 일치를 확인했다.
[상세 관측](linux-return-detail-20261008T225109.json)에서 실행 중 시장 데이터 프로세스나 새 system unit이 없고,
timesyncd가 이번 부팅 **22:43:45에 외부 동기화**한 것을 확인했다. Chrony는 아직 미설치다.

22:51:09에 실제 RTC는 `13:51:09`, 시스템 UTC도 `13:51:09.289667`이었지만,
Linux의 설정은 여전히 `LocalRTC=yes`, `/etc/adjtime=LOCAL`이다.
승인된 다음 명령은 `sudo /usr/bin/timedatectl set-local-rtc 0`이며 `--adjust-system-clock`은 사용하지 않는다.
도구 세션의 `sudo -n true`는 관리자 암호가 필요하다고 반환하여,
사용자가 자기 터미널에서 관리자 인증 후 위 명령을 실행했다. 승인 범위 재요청은 하지 않았다.

[RTC 전환 검증](linux-rtc-utc-verification.json)에서 journal의 **22:53:41.060707 `RTC configured to UTC time`**을 확인했다.
22:56 점검의 `LocalRTC=no`, `/etc/adjtime=UTC`, `Timezone=Asia/Seoul`, `NTPSynchronized=yes`가 모두 일치했다.
실제 RTC는 `13:56:41 UTC`, 시스템은 `13:56:40.778343 UTC`였다. RTC의 초 단위 읽기임을 감안한 근접성 확인이며
RTC가 0.22초 정확도로 인증됐다는 의미는 아니다. 전후 UTC 경과와 monotonic 경과의 순차이는 약 1.6µs였으며,
두 끝점의 비교이지 중간 모든 순간을 연속 관측한 결과는 아니다.
20개 unit의 보류와 시장 데이터 프로세스 부재를 다시 확인했다. 관측한 운영 설정 중 `/etc/adjtime`만 계획대로 바뀌었다.

[보존 확인](linux-return-preservation.json)에서 운영 파일 139개와 고정 후보 파일 196개의 해시가 일치했다.
부팅 전 `/tmp`에 있던 시험용 Chrony 바이너리 2개만 없어졌으며,
[영구 보관 패키지 재대조](staged-package-return-check.json)에서 .deb와 내부 두 바이너리가 기존 시험 해시와 일치했다.
패키지는 설치하거나 실행하지 않았다. 기존 설정·정리 grant 해시도 유지됐다.

RTC 반영 확인을 마쳤으므로 이제 [Windows 두 번째 왕복 안내](windows-roundtrip-handoff.md)를 진행한다.
그 왕복과 Linux 재확인이 끝나기 전에는 RTC 최종 확인 자료나 Chrony 설치 완료를 기록하지 않는다.
[현재 인계 상태](handoff-current.json)는 `ready_for_second_windows_roundtrip`이며, 최초 `handoff.json`은 당시 기록으로 보존했다.

## 수행한 Linux 작업

1. 21:37 기존 source·후보·설정 해시와 user unit 20개, 원본·링크·활성화 상태를 [before.json](before.json)에 기록했다.
   `saved-user-units/`에 원래 user unit 디렉터리를 symlink 보존으로 복사하고, 원문은 `unit-content/`에 별도로 보관했다.
2. 21:38 예약 timer 9개를 stop하고 원래 경로의 파일/링크를 `held-user-units/`로 보존한 뒤 persistent mask를 적용했다.
   실행 중 업무를 timer stop으로 종료했다고 가정하지 않았으며 업무의 종료 상태를 각각 확인했다.
3. 기존 daily 서비스를 한 번 수동 실행하여 10/7 UTC의 trade/mark/index/OI 누락 4종 4,608행을 채웠다.
   [갱신 결과](updated-daily.json)는 대상 8개 모두 complete이며 오류 배열이 비어 있다. 예약 재시도 정책은 바꾸지 않았다.
4. 21:39 업무 service 9개를 원본 보존 후 persistent mask했다. 자동 checkpoint 정리 service/timer도 포함한다.
5. **21:40:09** 두 collector를 기존 `KillSignal=SIGINT` 경로로 종료했다. 종료 후 원래 unit을 보존하고 mask했다.
   `/run`의 임시 mask가 아닌 `~/.config/systemd/user/<unit> -> /dev/null`이며 20개 전체의 실제 masked/비활성을 확인했다.
   이 시점부터 실시간 수집 공백이 시작되고, 새 수집기의 실제 수신 재개 전까지 계속된다.

구체적인 명령·시각·원본 이동은 [hold-events.jsonl](hold-events.jsonl), 종료 journal과 mask 확인은 [held-state.json](held-state.json)에 있다.

| 종료한 수집 | 수신 건수 | 저장 건수 | 마지막 수신(KST) | 최종 상태 |
|---|---:|---:|---|---|
| depth `2ea50dbce730490080131273c6f49a3e` | 11,184 | 11,184 | 21:40:09.235883 | interrupted |
| aggTrade `24d467b9c2264c5da13f12ccd19c36a1` | 11,281 | 11,281 | 21:40:09.248188 | interrupted |

의도한 SIGINT에 따라 CLI는 exit 130으로 종료했고, 강제 종료/시간 초과 기록은 없었다.
호가 미재구성 메시지 0, aggTrade 미복구 gap 0이며 stream lock이 해제된 것을 확인했다.
정상적으로 저장을 마쳤다는 뜻이며 중간 종료된 수집을 `complete=true`로 바꾸지 않는다.
[호가 종료 보고서](final-depth-latest.json)·[aggTrade 종료 보고서](final-aggtrade-latest.json)를 보존했다.

## 최종 checkpoint와 restic

[기존 backup CLI](final-backup-plan.json)로 새 checkpoint를 생성했다.
기존 잠금·파일 검증·pack 검증과 원래 manifest/source_root 계약을 그대로 사용했다.
오늘 이미 수행한 원본 전체 논리 검사와 packing을 다시 실행하는 maintenance 대신,
허용된 `backup --reuse-from` 진입점으로 최종 사본을 만들고 완성 사본의 검증을 수행했다.

- 실행: 21:41:41–21:48:24, exit 0.
- checkpoint 기준: **21:43:04.729937**.
- backup ID: `be6f6e493aa13931b3d0cddd03581f5b009c540b7560b7a9a7faf7006d5174a8`.
- 경로: `/mnt/backup/marketdata-historical/automatic/2026-10-08/56a530cf83164a83aefd30653138904a`.
- 검증한 파일 53,041개, 논리 크기 35,952,474,467바이트. 기존 사본과 공유한 내용 32,883,806,042바이트,
  새로 저장한 파일 내용 3,068,668,425바이트. 실제 파일시스템 전체 사용량과는 별개다.
- 원래 정기 사본과 모든 기존 사본은 유지했다. daily maintenance 보고서를 이 수동 실행으로 덮어쓰지 않았다.

21:49:54 기존 restic runner를 수동 실행하여 입력 검증 후 **21:55:35 저장**, **21:56:41 전체 읽기 검사**를 완료했다.
[실행 결과](final-archive.json)는 `processed=1`, `counted_dates=[]`이며 이번 수동 실행을 예약 성공 날짜로 집계하지 않는다.
새 snapshot ID는 `da1af66eeece550548f2b6daf549ffa22b662a9d32155bf4485c20c6cae51ae2`이고,
기존 지점을 포함한 13개 snapshot이 전체 읽기 검사 대상과 일치했다.

[최종 대조](final-verification.json)에서 현재 저장소 ID·snapshot 태그/기준 시각/경로·manifest·catalog 해시를 확인했다.
갱신 보고서의 10/7 UTC revision 6개(누락을 채운 4개와 기존 spot/funding 포함), 두 종료 collection의
저장 건수·마지막 수신 시각이 checkpoint catalog에 들어 있음을 확인했다. 별도 전체 복원 시험은 반복하지 않았다.
원래 프로필·암호 파일·grant·RTC 설정 파일·운영 소스·고정 후보 해시는 유지됐고, 20개 unit은 계속 persistent mask다.

**21:57:13 [handoff.json](handoff.json)을 `ready_for_first_windows`로 확정했다.**
다음 진행에는 사용자의 실제 Windows 작업 결과와 Linux 복귀가 필요하다. 아직 그 결과를 완료로 기록하지 않았다.

## 다음 사용자 작업과 유지할 보류

[Windows 첫 작업 안내](windows-handoff.md)를 준비했다. Windows 기존 레지스트리 값 보존 → UTC 해석 설정 →
Windows 실제 재시작·시각 동기화 확인 → Linux 복귀가 다음 단계다. 설정값/동기화 결과를 받아 사실로 기록한다.
Linux 복귀 후 [resume_check.py](resume_check.py)는 mask·마운트·부팅·timesyncd를 읽기 전용으로 확인하며 어떤 서비스도 재개하지 않는다.

이후 Linux RTC UTC 반영과 Windows/Linux 왕복 확인, Chrony·고정 실행 환경 설치, installation hold 등록,
최종 Linux 부팅·새 수집 확인을 이어간다. [검증된 Chrony 패키지](staged-package.json)는 재부팅에 대비해 작업 저장소의
`.cache/system-cutover-20261008/`로 복사했고, apt 메타데이터 SHA-256 및 실제 root 시험의 두 바이너리와 일치했다.
패키지를 설치하거나 daemon을 실행하지 않았다.

첫 Windows 인계 준비 시점에는 시스템 시각·RTC·Windows 설정·Chrony·운영 소스·root boot ledger를 변경하지 않았다.
이후의 Windows 사용자 작업과 Linux 복귀 상태는 문서 위의 후속 기록을 따른다.
자동 checkpoint 정리는 새 수집 재개와 별개로 계속 보류하며, 상시 시각 검사 후보의 검증·승인 후에만 재개한다.
grant·forget/prune·기존 원본/사본·시험 공간·복구 키트는 변경하거나 삭제하지 않았다.
