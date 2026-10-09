# Chrony 설정 전환 후보와 적용 경계

설정 전환 도구의 후보 구현·격리 검증은 완료했다. **운영 시간원 교체는 아직 권고하지 않는다.**
검토용 네 시간원 중 세 곳은 이 호스트에서 응답이 드물었고, 설정만으로 600초 안의 reference 갱신도 보장하지 못했다.
`candidate-review-only.conf`는 실제 시험한 입력을 고정한 검토용 파일이다. 운영 적용 승인을 받거나 적용한 파일이 아니다.

## 고정 배포본과 검토 계획

- 최종 도구: `clock-config-20261009-c365499dab14a4b1`.
- 압축본 SHA-256: `2a03d113cb58b95de7c63ee9daedff2e8954271287cd9f5d64cc2299cca6fe1f`.
- [배포본·재현 결과](release-final.json), [압축본](clock-config-20261009-c365499dab14a4b1.tar.gz).
- [최종 검토 계획](review-plan-final.json): `c36acd75fb6363817da0792ebe61d2a3546c6d427d9d955d1c34f96a09aec057`.
- [승인 양식](approval.template.json)은 `approved=false`다. 코드·계획·복구 범위에 대한 별도 명시적 승인이 필요하다.
- `release/`, `release.json`, `review-plan.json`, `clock-config-20261009-904f84547ed4ecf9.tar.gz`는
  새 부팅 복구 절차를 보완하기 전의 중간 후보다. 최종 적용에 사용하지 않는다.

현재 운영 수집 배포본 `system-bootstrap-20261007-f85eb8321a19c1ee`는 그대로다.
새 도구와 함께 묶은 `clock_gate.py`, `marketdata_runtime.py`는 그 배포본과 바이트 단위로 같다.
새 관리자 도구만 별도 `/opt/marketdata/clock-config-releases/<release-id>/`에 설치하는 안이다.
수집 프로세스·Python 환경·systemd 유닛은 이 도구 배포를 위해 교체할 필요가 없다.

## 명시적 전환 계약

승인된 계획은 기존 boot ID, 원래 ledger 전체, 세 파일의 이전/새 내용과 해시, 도구 해시,
Chrony 바이너리 해시, 시간원 검토 해시를 포함한다. 계획 ID는 그 내용 전체에 대한 해시다.
승인 이력에는 승인자·시각·승인 근거·계획 ID·stage/commit/rollback 범위를 따로 기록한다.

변경 가능한 파일은 아래 세 개뿐이다.

| 경로 | 허용 변경 |
|---|---|
| `/etc/chrony/marketdata.conf` | 시간원 행만 변경. 검토 후보는 고정 IPv4 네 개, 기존 `iburst minpoll 6 maxpoll 8` |
| `/etc/marketdata/runtime.json` | 위 Chrony 설정의 SHA-256만 변경 |
| `/etc/marketdata/clock-bootstrap.json` | 위 runtime 프로필의 SHA-256만 변경 |

600초·100ms·조건부 오차 상한 등 품질 기준, `rtcsync`, `leapsecmode slew`, 자동 `makestep` 부재,
고정 수집 배포본, 마운트·원본 경로·manifest, 서비스 권한, 정리 승인 범위는 바뀌지 않는다.
ledger 쓰기와 감사 증거 생성은 아래 프로토콜에만 따른다.

1. 기존 coordinator의 배타 잠금과 기존 업무 정지·유닛 해시 검사를 사용한다.
2. 계획·승인·진행 기록을 fsync한다.
3. 원래 closed 상태와 보정 증거를 포함한 **명시적 schema 2 전환 중 기록**을 ledger에 fsync한다.
4. 세 파일을 각각 원자 교체·fsync한다. 실제 해시로 완료 단계를 판별한다.
5. 관리자가 승인된 작업창에서 새 설정으로 Chrony를 기동한 뒤, 별도 `commit`이 실제 활성화를 검사한다.
6. 활성화·현재 시각 판정이 담긴 불변 receipt를 먼저 fsync한다. 그다음 schema 1 closed ledger에
   새 프로필 해시와 receipt 연결 이력을 기록한다.

schema 2는 가짜 프로필 해시나 준비 완료 표시가 아니다. 전환 중임을 나타내는 버전이 있는 상태다.
현재 배포된 gate는 schema 1만 받아들이므로 schema 2를 읽으면 업무·bootstrap 모두 거부한다.
기존 gate의 메시지는 일반적인 ledger schema 오류이고, 새 도구의 `status`에서 정확한 계획·진행 방향을 확인한다.
이 거부 동작은 복사한 실제 gate 함수로 시험했다.

기존 boot ID, `phase=closed`, 보정 횟수·결과·관측·종료 시각은 그대로 보존한다.
설정 전환이 현재 부팅의 초기 보정 권한을 다시 열지 않는다.
과거 runtime JSONL과 collection 품질 기록은 읽거나 덮어쓰는 대상이 아니다.
각 전환의 원래 ledger·승인·receipt는 `/var/lib/marketdata-clock/config-transitions/<plan-id>/`에 남는다.
다음 정상 부팅에서 기존 bootstrap이 새 boot ledger를 만들더라도 이전 전환의 receipt는 별도 경로에 남는다.
이 감사 경로의 기존 시장 데이터 백업 편입까지 구현했다는 뜻은 아니다.

## 설정 승인과 현재 준비 상태

`commit`은 세 파일 해시, 실제 Chrony 실행 설정 경로·바이너리·PID·invocation·시작 시점,
실제 주소 목록과 `sourcename`, 관측 전후 provider 일치를 확인한다.
파일이 다 바뀌기 전에 시작한 daemon을 새 설정으로 실행됐다고 인정하지 않는다.

올바른 설정이 활성화됐어도 reference가 오래됐으면 `current_clock_ready=false`로 기록할 수 있다.
이것은 **설정 전환 완료**와 **업무 시작 허용**의 분리다. 기존 gate는 매번 현재 시각을 다시 검사한다.
과거 receipt의 ready 값을 가져와 현재 준비 완료로 간주하지 않는다.

도구에는 서비스 시작·중지, `makestep`, RTC 변경, ledger 삭제·enroll 명령이 없다.
일반 계정의 apply는 거부하며 root 소유·쓰기 권한·경로 검사도 유지한다.

## 향후 승인받을 적용 순서

아래는 실행하지 않은 절차다. 시간원 후보를 먼저 보완하고 해당 설정으로 새 계획을 만들어야 한다.
지금 검토 계획에 다른 시간원이나 새 코드를 끼워 넣어 적용하지 않는다.

1. 확정한 배포본·설정·프로필·계획 ID·승인 및 되돌리기 범위를 검토한다.
2. root 소유의 독립 경로에 도구와 승인된 계획을 설치하고 해시·접근 권한을 확인한다.
3. 실행 중인 checkpoint/restic·파일 정리가 끝난 작업창에서 기존 예약을 보류한다.
   일반 업무 타이머는 중지하고 유닛 파일은 유지한다. 기존 검사에 필요한 유닛을 새 mask로 교체하지 않는다.
   자동 checkpoint 정리의 기존 mask는 계속 유지한다.
4. 두 수집기를 정상 종료하고 종료 보고·마지막 저장·계획된 공백 시작을 기록한다.
   bootstrap이 실행 중이지 않고 수동 작업도 없는지 확인한다.
5. 도구의 `stage --plan <승인 계획> --approval <승인 기록>`을 실행한다.
   실패하면 `status`와 실제 파일 해시부터 확인한다.
6. 같은 작업창에서 Chrony만 새 설정으로 통제된 재시작한다. 자동 step 없는 설정을 그대로 쓴다.
   bootstrap 재등록·재실행, Windows·RTC 작업, 재부팅은 포함하지 않는다.
7. `commit`으로 실제 활성화를 확인한다. 이후 기존 `clock_gate.py check`의 현재 준비 통과를 별도로 확인한다.
   준비되지 않았다면 정상 시작으로 기록하지 않고 대기를 유지한다.
8. 승인한 수집·기존 업무만 재개하고 새 invocation·import 경로·원문/표준 저장·시각 기록을 확인한다.
   자동 정리는 재개하지 않는다. 실제 공백과 aggTrade 보강 가능 범위는 별도 기록한다.

장기 공개 시간원 실측, 실제 root 대상 쓰기·권한, 최종 기동 뒤 준비 통과는 이번 격리 검사 결과로 대신하지 않는다.
이번 도구는 해당 작업을 사용자 대신 자동 승인하거나 수행하지 않는다.

## 중단과 되돌리기

| 발견 상태 | 허용되는 대응 |
|---|---|
| 계획·승인만 기록, ledger·세 파일은 원래 상태 | 같은 승인으로 stage 재개 또는 rollback 선택 |
| 전환 중 ledger, 세 파일 일부만 교체 | 현재 해시를 확인하고 같은 방향으로 재개하거나 명시적 rollback |
| 세 파일 교체, 활성화 미확인 | 업무 계속 거부. 새 daemon 활성화 검증 또는 rollback |
| receipt 저장 후 ledger 확정 전 종료 | 실제 활성화를 다시 확인하고 기존 receipt를 보존하며 commit 재개 |
| 적용 확정 후 rollback | 원래 파일을 복원하고 실제 이전 설정 활성화를 확인한 뒤 rollback receipt·이력 추가 |
| 승인되지 않은 파일 내용·증거 손상 | 추측하여 덮어쓰지 않고 중단 |
| boot ID 변경 | 새 설정 적용 재개 거부. 아래 제한된 원복만 허용 |

되돌리기는 같은 도구의 `rollback`으로 원래 세 파일을 복원한 뒤 `commit`으로 확정한다.
원래 daemon이 그대로라면 그 identity를 확인할 수 있고, daemon이 바뀌었다면 원래 설정으로 새로 기동한 증거가 필요하다.
원래 pool의 DNS 응답은 달라질 수 있으므로 오래된 IP 목록과 같아야 한다고 강제하지 않는다.
대신 실제 주소가 공개 주소이고 association 이름이 원래 승인한 `ntp.ubuntu.com`인지 확인한다.
**이 rollback은 이전 pool 동작을 복원하므로 문제 IP가 다시 들어올 수 있다.** 새 구성의 품질 개선을 뜻하지 않는다.

전환 중 전원이 끊겨 boot ID가 바뀌면 자동으로 새 구성을 채택하지 않는다.
먼저 모든 업무와 bootstrap을 정상 중지하고, 원래 bootstrap 유닛 파일을 보존한 채 비활성화한다.
helper는 실제 `inactive`, `disabled`, MainPID/ControlPID 0과 기존 guard 파일 해시를 함께 확인한다.
`disable` 자체는 의존 기동을 막는 mask가 아니므로 수집기·예약의 정지와 배타 작업창을 함께 유지해야 한다.
관리자가 이 복구와 동시에 다른 기동 작업을 하면 안 된다.

이 상태에서만 원래 세 파일로 rollback할 수 있다. 원래 boot ID와 closed 증거를 보존하므로
현재 새 부팅의 gate는 계속 거부한다. 원복이 새 부팅 준비 완료를 만들어 내지 않는다.
bootstrap의 재활성화와 새 부팅 준비·가능한 초기 보정은 별도 확인·승인할 작업이다.
기존 ledger를 지우거나 enroll을 다시 수행해서 통과시키지 않는다.

## 검증 범위

[최종 48개 회귀 검사](transition-tests-final.xml)와 [깨끗한 압축 해제 경로의 48개 검사](clean-transition-tests-final.xml)를 통과했다.
실제 파일·fsync·flock·SIGKILL을 사용했고, 서비스 상태·provider 활성화는 명시적 대역으로 주입했다.
새 부팅 rollback의 유닛 파일 보호는 실제 `audit_quiescent`의 guard 검사까지 연결했다.
최종 helper의 실제 root 쓰기·운영 재시작은 수행하지 않았다.

초기 clean 시험은 venv Python 심볼릭 링크를 시스템 Python으로 해석해 pytest를 찾지 못했다.
같은 압축본에서 올바른 venv 경로로 다시 통과했으며 [첫 실패](clean-attempt-1.json)를 남겼다.
그 뒤 검토에서 찾은 bootstrap mask와 guard 해시의 충돌을 고쳐 최종 후보를 별도로 고정했다.
이전 후보의 47건과 최종 후보의 48건을 합산해 검사 수를 부풀리지 않는다.
