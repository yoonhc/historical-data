# T18 승인 수령·운영 적용 현황

2026-10-25 **16:02:01–03 KST 설치·구성 확정·timer 재개 완료**, **17:09 여섯 사본 편입·전체 검사·요청 종결 완료**, **17:09/17:35 자연 no-op의 중복 본문 할당 억제 확인 완료**다. 18:03–05 읽기 전용으로 근거와 현재 저장 표본을 대조했다. [사본별 snapshot·T05 bundle·요청·할당 결과](operating-results.md). 과거 출력 추가 정리·과거 수집 공백·장기 보관/조회 개선은 별도 미완료다. 완료된 apply/recovery를 반복하지 않는다.

[사용자 승인 기록](approval-record.json)의 operation `27dd5101bfa0b7005ce0a931b075bc9cccad5a5331e9350a30c4765cbf1ee225`, JSON SHA-256 `5fe462588a59c9bfecc94c68d5473143bb4f1e02c56ca834744acd8bb02f2198`에 한정한다. 이전 operation·승인·보류 기록은 그대로 보존했다. 이 작업공간의 승인 기록은 root 도구가 한 번만 확정하는 `authority.json`을 대신하지 않는다.

## 적용 직전 읽기 전용 대조

[15:27:54 실제 호스트 대조](preapply-observation-20261025T062755Z.json): manifest 12개 경로·크기·해시, boot·보호 입력·root 소유권/권한·구성 before·ledger·현재 recurring, 소스 137개·유지 Python 환경 1,648개 항목이 계획과 일치했다. 8개 현재 request의 저장 근거·종결 대조도 통과했고 9개 업무 모두 유휴/발행 job 없음이다. archiver timer는 기존 정지·enabled, 다른 8개 timer는 active이며 중지 전파 의존관계도 일치한다. 두 수집 supervisor·Chrony는 같은 PID/invocation을 유지한다.

`/home` 가용 **16,634,351,616 B**, 요구 **12,208,032,519 B**, 초과 여유 **4,426,319,097 B**다. 이 관찰은 실행 시점의 재검사나 잠금 아래 검사를 대신하지 않는다. 최종 `/opt` 설치 및 UID 1000 import/CLI는 root 적용 도구가 timer 보류 전에 검사한다. 이 관찰에서는 전환 잠금을 얻거나 설정을 쓰지 않았다.

첫 보조 관찰은 native Host의 감사 디렉터리에 workspace 경로를 지정해 root 소유권 검사에서 멈췄다([원본](preapply-observation-20261025T062717Z.json)). 운영 입력 결함이나 적용 실패가 아니며, 동일한 무변경 도구를 기존 root 감사 경로 기준으로 읽기 전용 실행해 위 대조를 완료했다.

`sudo -n ... handoff.py --action check`는 `sudo: a password is required`로 실행되지 않았다. 운영 승인을 다시 요청한 것이 아니라, 이미 승인된 **apply 한 번**의 터미널 실행을 요청했다. 이 시점에는 새 소스 설치·timer 제어·configuration/ledger/grant 전환을 하지 않았다.

## 단계별 상태

| 단계 | 상태 |
|---|---|
| 새 operation 승인 | 완료, 정확한 ID·JSON 해시에 한정 |
| 소스 설치·최종 UID 1000 import | 완료 |
| 구성 연결·timer 재개 | 완료; after 구성·closed ledger·예상 scope의 활성 grant·9개 timer active |
| 10/20–25 미보관 6개 편입 | 완료; batch 3개씩 두 회차, 정확한 새 snapshot·전체 검사·T05 bundle·request/monitor 종결 대조 |
| 자연 no-op 출력 누적 방지 효과 | 완료; 같은 30개 집합으로 두 회차 각각 새 dump/검증 30회, 큰 본문 추가 할당 0 B; 작은 기록 증가는 별도 |

batch 3개와 기존 검증을 유지한다. 새 plan·자료를 확인용으로 만들지 않는다. 기존 대량 출력 정리·과거 수집 공백·장기 조회/보관 개선·신규 사본에 따른 재승인 계약 후속은 TODO의 별도 범위로 유지한다.

## 실제 적용 실패와 보완 경로

원본 실패: `/var/lib/marketdata-clock/t18-output-handoffs/27dd5101bfa0b7005ce0a931b075bc9cccad5a5331e9350a30c4765cbf1ee225/attempt-81b84a54e20e4c3789bf28d9087fe534.json`, 15:29:02–03 KST, `JSONDecodeError`, 보호 프로세스 유지. 원래 승인·authority·도구·실패 기록을 보존했다.

[첫 보완 경로](../t18-archive-recovery-2026-10-25/review.md)의 recovery `a277ae71…`는 별도 승인 뒤 15:43 실행됐으나 `KeyError: 'ExecStartPre'`로 중단됐다. root 실패 `attempt-c67fbf0d04f04f78802b7bcb948e6853.json`과 transaction은 보존했다. `protected_processes_unchanged=false`는 `After`/`Requires` 출력 순서 차이이며 실제 PID/invocation/활성 상태·의존관계 집합은 같았다. 원본 false를 고쳐 쓰지 않았다.

[두 번째 보완·정확한 중단 상태·31개 검사·실제 전체 propose 대조](../t18-archive-manager-recovery-2026-10-25/review.md)를 고정했다. recovery **`ab49fce3…`**, JSON SHA-256 **`8a139e1a…`**는 별도 승인 뒤 16:02 성공했다. 생략된 명령 속성의 실제 D-Bus 값 확인과 의존관계 출력 순서 대조를 보완한다. 업무 소스·원래 operation/authority·after 구성·정책은 유지한다. 15:50 가용 16.63GB/요구 12.21GB, manifest 12개·8개 요청 종결을 재확인했다. 이전 apply와 첫 복구는 반복하지 않았다. 실제 재개 이후 상태는 아래에 구분한다.


## 실제 재개와 사후 대조

성공 root receipt: `/var/lib/marketdata-clock/t18-output-recoveries/ab49fce3b04087ceb44874b0fde1c452bd457e4b7b9e255dfda4aa7b8ad90eb6/attempt-fe166c7a29ed48debc820fe398155cef.json`.

[16:03 사후 대조](activation-observed-20261025T070338Z.json): 설치 소스와 기존 Python, 보호 입력·권한, 구성 5개 after, recurring 활성, manual 비활성·사용자 두 mask 유지, 9개 timer active를 확인했다. Ledger는 `closed`로 확정됐고 기존 boot·closed 관측·보정 이력은 보존하며 이번 구성 이력 한 항목만 추가했다. 원래 authority SHA `5e501e42…`와 **15:29:02 승인 시각**은 그대로이고 두 실패 원본 해시도 유지했다. 두 수집 supervisor·Chrony의 PID/invocation은 같았다.

활성 recurring SHA `3d67f5a2ae4f44e0be36a68237fe30056ed6419d785b07a44b71f1f9797fa7e7`, scope `2875edd74ccef07cd4a1f09917d147ab0a73cde0a6921a5f3f7cd9930344ef83`. 재개 직전 가용 16,628,289,536 B, 요구 12,208,032,519 B로 통과했다.

## 16:10 당시 초기 업무 결과 — 과거 관찰 보존

[16:10 소규모 기록·할당 대조](archive-observed-20261025T071054Z.json): 실제 system timer trigger **16:02:03.274202**, invocation `afb362cf8c57479480acbc3bff80b58c`, scheduled `e11cc8d3f21242d38e742364e3f6975f`, archive run `96e57542113d4f82be4c191c883fb34e`, request `1e4d590321ee4a5bb4c68296e74f722e`를 연결했다. 새 실행본 경로에서 기존 24개 snapshot의 새 dump/해시 검증·24개 본문 공유 receipt와 동일 inode를 확인했다. 최초 pool 논리 크기 281,273,837 B, 실제 할당 **281,415,680 B**이며 이 생성 비용은 반복 no-op 증가 억제와 다르다.

현재 첫 10/20 사본의 `verify-input`이 진행 중이다. 6개 편입·전체 검사·bundle·요청 종결은 아직 완료되지 않았다. `/home` 가용 **16,327,528,448 B**다. 추가 restic 조회/전체 검사/backup을 확인용으로 실행하지 않았다. Batch 3개와 자연 예약을 유지한다.

[T05 및 OI 결과](timer-restoration-results.json): T05 timer 복원 시 실제 timer가 **16:02**에 요청했고 기존 `Expected today's 15:40 trigger` 검사에서 **failed/1**로 종료됐다. plan 없음·selection 미실시·`deletion_attempted=false`·삭제 0개다. 정상 보류 또는 삭제 완료로 바꾸지 않는다. Timer 파일의 `Persistent=false`는 그대로이며 다음 실제 예약은 **2026-10-26 15:40 KST**다. 수동 재시작·reset·조건 완화는 하지 않는다. 설치 코드상 이 실행은 `deletion_attempted`가 없으므로 미완료 삭제 journal 우회 예외가 필요하지 않지만 다음 예약의 실제 결과는 별도 관찰한다. 재활성화 시점의 timer 요청과 정확한 예약 시각 검사 접점은 후속 배포 계약에 기록한다.

OI는 저장/요청 종결 후 기존 coverage 미완료로 exit 2다. 10/15 자료 문제와 기존 재시작 동작을 유지하며 새 전환 실패로 묶지 않는다. 수집기 새 child는 자연 시작했고 기존 supervisor는 유지되나, 과거 공백·시각 품질 이력을 무효화하거나 전체 연속 수집을 주장하지 않는다.

## 18:05 완료 대조와 후속

[최종 운영 대조](operating-results.md)에서 여섯 사본의 새 snapshot, 각 batch의 기존 전체 읽기 검사, snapshot에서 업무가 이미 dump한 T05 bundle의 해시·17–22개 객체·동일 baseline/선행 관계, 네 wrapper 요청의 complete/0·감시 종료를 확인했다. 두 자연 no-op은 같은 30개 snapshot을 새로 조회·검증하면서 공유 본문 379,854,848 B를 그대로 참조했고 큰 본문 추가 할당은 0 B다. 추가 restic 명령·전체 검사·service 시작은 하지 않았다.

가용 16.03GB와 현재 원문/segment/새 시각 chunk 표본을 대조했다. 기존 clock 품질의 `prior_hard_fault_blocks_stale_exception`은 보존하고 수집 전체 무공백을 주장하지 않는다. T05의 16:02 실패·삭제 0개와 다음 10/26 15:40 자연 예약은 [별도 후속](timer-restoration-followup.md)이다. `Persistent=false`만으로 timer 복원 직후 기동을 완전히 배제하지 않으며 실제 systemd 패키지는 255.4-1ubuntu8.16이다. 같은 upstream issue의 원인으로 확정하지 않는다.

향후 중지는 원래 도구의 제한된 `stop` 분기, 구성 원복은 **성공한 `ab49fce3…` manager 보완 도구의 `rollback`**을 사용한다. 원래 adapter가 자동으로 고쳐진 것으로 가정하지 않는다. [정확한 명령·해시·600초 보류·기록 보존·검증 한계](stop-rollback-tools.md).
