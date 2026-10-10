# T13 승인된 daily 운영 전환 — 2026-10-11

사용자 승인: “오케이 운영 적용하자”. 대상은
`t13-daily-scheduler-20261011-703fb0d455523c1b`의 검증한 고정 소스다.
재개 CLI의 존재는 모든 needs_review 작업의 재개 승인이 아니다.
**실제 적용·첫 실행과 처리 0건 재평가는 완료했다.** 설치·전환의 중단 및 재개는
[완료 범위와 원본 결과](summary.md)를 따른다. 아래 설치/전환 스크립트를 다시 실행하는 안내가 아니다.

## 정확한 변경 범위

- `/opt/marketdata/releases/t13-daily-scheduler-20261011-703fb0d455523c1b/`: 신규 비-editable 실행본.
  고정 소스 149개, 시험 설치의 런타임 파일 1,486개·패키지 버전·실제 import를 대조한다.
- `/etc/marketdata/daily-scheduler.toml`: 검증한 대상별 일정·예산 설정.
- `/etc/marketdata/daily-publication-probe.toml`: `enabled=false` 유지.
- `~/.config/systemd/user/marketdata-daily.service`·`.timer`: 기존 소스 링크를 새 고정 실행본을 쓰는 base 파일로 교체.
  링크의 대상인 workspace 파일은 수정하지 않는다.
- daily timer만 전환 중 disable/stop한다. 중간 중단·재부팅에 불완전한 조합으로 예약이 실행되지 않게 하고,
  첫 실행 결과 확인 후 enable/start한다. 진행 중 daily는 정상 종료를 기다리며 강제 종료하지 않는다.
- 최초 실제 실행 때 새 daily 원장·보고서를 작성한다. 미리보기 사본을 운영 DB에 이식하지 않는다.

기존 root clock drop-in·모든 ExecStartPre·waiter, bootstrap ledger·부팅/보정 기록, 두 수집기,
Chrony·RTC, backup/pack/restic, 자동 checkpoint 정리 mask는 유지한다.

## 단계와 중단 기록

1. 현재 운영 상태·guard·원본 daily 링크를 [전환 전 기록](operating-before.json)에 고정했다.
2. 00:37 KST의 [새 사본 미리보기](predeploy-preview.json)는 기존 확보 156건 제외·예정 전 20건·HTTP 0건이다.
   그 시점 즉시 내려받을 자료가 없으며 실제 첫 실행은 현재 상태를 다시 평가한다.
3. 관리자 설치는 서비스 제어 없이 실행한다. 00:42 KST 첫 실행은 환경 대조 이후 권한 검사에서 중단됐다.
   uv 캐시에서 복사된 그룹 쓰기 권한·생성된 lock의 쓰기 권한 때문에 고정 배포본 요건을 충족하지 못했다.
   [최초 결과](initial-install-result.json)는 보존하며, 검사를 없애지 않고 새 release의 쓰기 권한을 제거한다.
   `finish_install_daily.py`가 같은 소스·설치 환경을 다시 대조하고 00:46:28에 권한 제한·설정 설치를 완료했다.
   사용자가 기존 install 명령을 재실행한 경우 기존 경로 검사가 변경 전에 거부했다.
4. 현재 엄격한 시각 준비를 확인한 뒤 `activate_daily.py --run`으로 daily 전환을 시작했다.
   00:49:13 timer disable이 기존 base 링크도 제거한 뒤, 교체 대상까지 변경 금지로 묶은 검사에서 중단됐다.
   `continue_activation.py`는 승인된 두 base 경로만 구분하고 기록된 중단 상태·나머지 보호 조건을 확인해
   00:51:22 교체·실효 검사·첫 daily 시작을 완료했다. 원래 실패 결과와 두 코드 해시는 보존했다.
   current reference_age 보류에는 기존 정책을 유지하고 자연 회복을 기다렸다.
5. 첫 invocation의 종료·원장·HTTP 시도·제외/보류 분류·보고서를 대조한 뒤 00:53:18 daily timer를 재개했다.
   처리 0건 실행은 실제 정오/저녁/날짜 이월 수집 완료가 아니다.

실제 `systemctl show`의 반복 ExecStartPre를 보존하는 파서와 원본 링크를 건드리지 않는 교체,
부분 임시 파일 거부·정확한 변경 범위를 [배포 검사 7개](deployment-tests.xml)로 확인했다. 기존 후보의 213개 검사는 별도 근거다.

## 실패 시 복구와 원복

- 설치만 중단됐다면 기존 daily 링크·예약을 유지한다. 루트 설치 원본 receipt와 실패 위치를 보고
  완료된 소스/환경을 다시 설치하지 않고 남은 단계만 명시적으로 이어간다.
- daily 전환 중 오류가 나면 `activation-events.jsonl`, 원래 `activation-result.json`,
  `continuation-result.json` 및 `timer-enable-result.json`에서 실제 완료 단계를 확인한다.
  timer는 disabled 상태로 유지하고 부분 파일을 삭제하거나 스크립트를 맹목적으로 재실행하지 않는다.
- 원복 시 새 daily의 정상 종료를 기다린 뒤 `operating-before.json`의 **두 base 유닛 링크와 이전 enable 상태**만 복원한다.
  원본 링크는 각각 workspace의 `integrations/systemd/marketdata-daily.service`와 `.timer`다.
  두 링크 대상의 저장된 해시를 확인한 뒤 링크를 교체하고 사용자 manager를 daemon-reload한다.
- 원장·자료·revision·처리 기록·새 설정·고정 실행본을 보존한다. DB 과거 사본 복원이나 테이블 삭제는 하지 않는다.
- 이전 실행기는 새 cooldown/needs_review/추가 예산을 따르지 않는다. 조회로 제한 상태를 먼저 확인하며,
  제한을 우회하는 전체 daily 재실행은 원복의 자동 후속으로 수행하지 않는다.
- 기존 clock guard가 모두 남아 있는지 대조하고, 시각 준비가 통과한 경우에만 이전 timer를 재개한다.

## 별도 후속

정오·저녁·날짜 이월 관찰, 새 작업/재개 이력의 운영 checkpoint/restic·복원 조회는 아직 남는다.
공개 HEAD 관측·업무별 시각 정책·시스템 manager 이전·자동 삭제 재개는 이번 적용에 포함하지 않는다.
