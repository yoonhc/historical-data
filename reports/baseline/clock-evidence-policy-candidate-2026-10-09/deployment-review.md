# 후보 적용 범위와 되돌리기 검토

2026-10-09. 후보 `clock-evidence-policy-20261009-26e7fd4bbd0a68a5`.
**운영 쓰기·수집 재시작 승인 요청 단계 전의 검토 자료다. 실행기는 포함하지 않았다.**
현재 시스템·Chrony·부팅 ledger는 그대로다.

## 정확한 파일 범위

[plan.json](deployment/plan.json)의 계획 ID:
`f25a43909700fd3de9b7809b4b20f088dc67043f13038cf43d2ac0184aba2c86`.
`executable=false`, `approval=null`이며 [승인 양식](deployment/approval.template.json)은 false다.
현재 boot/원래 ledger/파일 바이트 해시를 묶었으므로 적용 전에 현재 상태가 같은지 다시 확인해야 한다.
다른 부팅이나 다른 후보에는 이 계획을 그대로 사용하지 않는다.

| 대상 | 후보가 요구하는 변경 |
|---|---|
| `/opt/marketdata/releases/clock-evidence-policy-20261009-26e7fd4bbd0a68a5` | 별도 root 소유 immutable release·non-editable venv 설치. 기존 release 유지 |
| `/etc/marketdata/runtime.json` | `release_root`만 의미상 변경. UID·data_root·mount UUID·Chrony·모든 수치 정책 유지 |
| `/etc/systemd/system/marketdata-collector@.service` | 새 release와 `capture_runtime.py` 실행, 정상 종료 허용시간 150→330초 |
| `/etc/marketdata/clock-bootstrap.json` | runtime 파일 해시·collector unit guard 해시만 변경 |
| `/var/lib/marketdata-clock/boot-state.json` | 승인·전환 receipt와 연결된 새 profile binding. 부팅·closed·초기 보정 증거·과거 이력 보존 |
| 별도 root 전환 evidence 디렉터리 | 원래 파일·ledger·승인·계획·단계·apply/rollback receipt 보존 |

[정확한 before/after diff](deployment/configuration.diff)에 위 세 파일의 내용을 고정했다.
runtime JSON의 키 정렬 차이는 데이터 값 변경과 구분한다.
330초는 child 종료 대기 120초와 publication drain/worker 종료 여유를 포함한다.
User=yoonhc, ProtectClock, NoNewPrivileges, CapabilityBoundingSet, ReadWritePaths 등 보호 설정은 유지한다.

기존 부팅 서비스·Chrony drop-in·사용자 예약 업무는 이전 고정 release의 **동일한 엄격한 gate**를 계속 쓴다.
그 gate는 새 runtime profile을 읽을 수 있고 bootstrap hash를 확인한다. 이 경로를 함께 바꾸기 위해
8개 업무의 유닛을 다시 배포할 필요는 없다. 이전 release를 지워서는 안 된다.
두 collector의 데이터 경로·비밀값·백업 프로필·보관/정리 grant는 변경 대상이 아니다.

## 적용 순서와 남은 검증

1. 새 release 설치·source/설치 module/import 해시 확인. 운영 데이터에 접근하기 전 검사한다.
2. 승인된 공백 창에서 depth·aggTrade system collector만 정상 종료한다.
   관련 예약 업무가 실행 중이면 완료를 기다리고, 시작 경합을 coordinator 잠금과 pending 상태로 막는다.
   활성 작업이 없다고 확인하지 않고 파일부터 바꾸지 않는다.
3. 정확한 승인·계획·before 파일·기존 closed ledger를 보존한다.
   coordinator exclusive lock 아래 **pending schema 2를 먼저 fsync**하여 기존 gate가 거부하게 한다.
4. 세 파일을 정확한 after로 바꾸고 fsync·daemon-reload 한다.
   현재 system manager의 effective unit·유저·보호 옵션·실행 경로·쓰기 권한을 실제로 확인한다.
5. 새 profile binding과 연결된 receipt를 확정한다. `boot_id`, `phase=closed`, step 시도/결과,
   `closed_observation`은 그대로이며 history에 이번 전환을 추가한다. 해시만 덮어쓰지 않는다.
6. **설정 승인/적용 완료와 현재 엄격한 시각 준비를 별도 확인**한다.
   준비 통과 뒤 두 collector를 시작하고 중복 없음·실제 파일/collection·닫힌 시각 artifact를 확인한다.
7. 실제 첫 세션 종료·다음 child·새 백업의 evidence 편입을 후속 기록으로 확인한다.

현재 [gate 의미 검증 8가지](deployment/gate-simulation.json)는 fixture ledger에서
pending/이전 binding/부팅 변경/현재 시각 미준비를 거부하고, closed 이력 보존과 새 binding을 구분한 결과다.
**이는 아직 운영 전환 실행기의 crash/recovery 시험이 아니다.**
실제 쓰기 실행 절차는 위 계획에 한정해 준비하고, 각 파일·receipt·ledger 확정 사이 중단과 원복을
격리 검증한 뒤 운영 승인 대상으로 제시해야 한다. 기존 T10 전환기는 `chrony_source_configuration`
범위만 허용하므로 이 runtime/unit 변경을 그 계획이나 승인으로 수행하지 않는다.

현재 통과한 systemd-analyze verify는 최종 unit의 release 경로만 clean 설치 경로로 치환했다.
[치환 범위](deployment/syntax-substitution.json). 최종 `/opt` 경로·root 소유권·systemd 보호 설정에서의
실제 데이터 쓰기는 아직 수행하지 않았다. 이 한계를 숨기기 위해 보호 옵션을 줄이지 않는다.
운영 세 파일의 root UID 0/0644는 샌드박스 밖 read-only stat으로 확인했다.
샌드박스 namespace 안에서는 host root가 nobody로 표시되는 차이가 있었다.

## 중간 실패와 원복

pending 상태에서 설정·unit·ledger가 일부만 반영됐으면 업무를 허용하지 않는다.
실제 완료 단계와 승인한 before/after 해시를 대조해 재개하거나 원복한다.
현재 파일이 둘 다 아닌 경우, receipt/ledger가 맞지 않는 경우, boot가 달라진 경우 자동 채택하지 않는다.

원복은 후보 collector를 종료한 뒤 같은 coordinator/pending 절차로 원래 세 파일을 복원하고
daemon-reload 한다. 이전 profile binding을 **원복 receipt와 history를 추가하여** 복구한다.
기존/새 시각 근거와 collection 기록은 삭제하지 않는다. 후보가 추가한 카탈로그 인덱스는 이전 코드와
호환되며 그대로 남길 수 있다. 이전 CLI에는 새 evidence 조회 명령이 없으므로 보관한 후보 CLI로 조회한다.
현재 엄격한 준비를 확인한 후 이전 collector를 재개한다.

이번 전환에 Chrony 재시작·설정 변경·makestep·RTC/Windows 변경·재부팅은 필요하지 않다.
bootstrap enroll/ledger 삭제로 초기 step 권한을 다시 열지 않는다.
부팅 변경이나 미해결 전환이 있으면 보류한 상태에서 별도 복구 계획을 만든다.
checkpoint 자동 정리는 계속 masked이며, 이 원복을 이유로 기존 grant를 재활성화하지 않는다.

## 실행/조회 안내의 의미

승인 후 배포본의 시각 상태 조회는 새 `capture_runtime.py --status`를 사용한다.
`raw_running`과 `current_estimate_usable`, `reference_stale`, 누적 history를 함께 읽는다.
기존 v1 latest 파일은 새 supervisor의 현재 상태가 아니다.

collection 시각 근거는 새 `clock-evidence` CLI로 조회한다. `available`/종료 코드 0은
근거가 있다는 뜻이며, 실제 호가/aggTrade 품질이나 모든 구간의 정상성을 인증하지 않는다.
시각 근거가 없는 오래된 collection과 열린 끝부분은 `unknown`으로 유지한다.

이 문서의 절차는 **검토할 적용 범위**이며 현재 실행 승인이나 완료 보고가 아니다.
