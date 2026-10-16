# T16 구성 전환 격리 검증과 관리자 시험

고정 수집/업무 후보는 `scheduled-clock-policy-20261016-544c61aca49f1355` 그대로다. runtime 291개 해시는 변경하지 않았다. **10/16 21:32 KST 실제 관리자 시험도 통과했다.** 아래 로컬 검사는 시험 준비 당시의 근거이며, 원본 관리자 결과와 이후 운영 인계 도구는 별도로 연결한다. 운영 승인·적용은 미완료다.

[관리자 원본 결과](system-preflight-result-48167a9e9eb24fdd.json)는 8개 보호 환경·서로 다른 invocation의 같은 요청 복구·실제 timer/수동 출처 구분을 통과했고, 운영 전후 동일·시험 12개 유닛/경로 정리·오류 0개를 확인했다. 이후 **76개 인계/설치 검사와 정확한 운영 계획**은 [새 승인 검토안](../scheduled-clock-deploy-2026-10-16/summary.md)에 고정했다. 이 폴더의 `validation.json`은 최초 로컬 검사 기록으로 보존한다.

- 관리자 시험 도구: `scheduled-clock-preflight-20261016-09c76b2964a76970`
- 격리 구성 계획: `5e7473d9d5c349c5ae31a0da1f1589391ef3507000e358d3ba74ccbc3be5f0cb`
- [정확한 도구 해시](tool-seal.json), [구성 계획](configuration-plan.json), [비활성 승인 양식](approval.template.json), [검증 결과](validation.json)

## 이번에 확인한 범위

| 범위 | 결과 | 한계 |
|---|---|---|
| 구성·ledger 변경, 중단·재개·원복 | 48개 통과. intent/pending/13개 파일/staged/receipt/commit 중단 후 재개 또는 원복. 부팅·보정 이력과 데이터 경로 보존 | systemd/시각 준비 확인은 대역 |
| 새 기록을 남긴 구성 원복·재적용 | 2개 통과. 실제 새 wrapper의 OI fetch 또는 network JSONL 저장 → 구성 원복 → 실제 이전 `/opt` CLI의 다음 관측 → 새 계획으로 재적용 → 기존 요청 복구 | 운영 구성 대신 격리 파일 트리 사용 |
| 관리자 시험 경계 | 11개 통과. 8개 유닛의 보호/종료 옵션 유지, 전용 waiter 연결, 운영 의존성 거부, 명령 allowlist, 기존 엔진/후보 무변경 | 실제 root/systemd 실행 전 |
| 다른 경로로 옮긴 세 가상환경 | 8개 업무 driver 통과. 설치된 site-packages import, 실제 CLI·request·임시 저장소 경로 확인 | 일반 계정 프로세스이며 systemd 보호 환경은 아님 |
| 임시 유닛 구문·의존 순서 | 9개(8개 업무+waiter) 통과 | 실제 기동 검증과 구분 |
| 현재 호스트 읽기 전용 조회 | 고정 `run_job.py --check`가 현재 T05 binding으로 ready 반환. 같은 boot/provider 확인 | 관리 보호 옵션 아래의 시험은 다음 단계 |

48+2+11=61개는 이번 전환/시험 도구 범위다. 세 실행본의 기존 119개 정책/복구 검사와 합치거나 전부 재실행했다고 표시하지 않는다. 8개 driver는 기존 검사 중 13개를 필요한 환경 접점에 재사용했다. [로컬 실행](local-driver-validation.json), [옮긴 가상환경 실행](relocated-validation.json), [유닛 검사](trial-unit-verification.json).

별도 두 Python 프로세스에서 첫 실행의 close 실패(exit 70)로 `finalization_failed`를 남긴 뒤, 다음 프로세스가 업무를 다시 호출하지 않고 원래 결과를 종결했다. [로컬 기록](local-invocation-validation.json). 이후 위 관리자 결과의 `two_invocations`에서 실제 systemd의 서로 다른 invocation도 확인했다.

## 구성 전환 범위

8개 service, 전용 waiter, topology, bootstrap guard 연결, 새 업무 binding, T05 binding의 **13개 경로**다. 8개 timer의 바이트·enable 링크, 기존 사용자 mask, 기존 strict waiter/collector/Chrony 관련 guard는 보호 대상이다. 단순 파일 수가 아니라 [계획의 전체 경로·before/after](configuration-plan.json)가 기준이다.

[T16 adapter](transition.py)는 T15의 [전환 엔진](vendor/transaction_engine.py)을 바이트 그대로 사용하고, 별도 로드한 모듈에 T16의 정확한 범위 검사를 연결한다. 운영 파일을 실행 대상으로 받는 CLI는 열지 않았다. 기존 파일을 덮어 수정하거나 T15 승인을 승계하지 않는다.

부팅 ledger는 전환 중 schema 2의 pending으로 기존 closed 증거를 보존하고, commit에서 profile 식별자와 구성 이력만 바꾼다. step 권한을 다시 열지 않는다. 원복 후 같은 후보를 재적용할 때도 원복 후 ledger를 기준으로 새 계획이 필요하다. 원래 계획 ID를 반복 사용하지 않는다.

종료 대기는 coordinator 배타 잠금을 잡기 전에 수행해야 한다. 엔진은 stage/commit의 짧은 호출 안에서만 잠금을 유지하며, 호출 사이 T05 완료 후처리의 공유 잠금을 얻을 수 있음을 시험했다. 이후 [운영 인계 실행기](../scheduled-clock-deploy-2026-10-16/handoff.py)에서도 기존 service/closed ledger를 보존한 종료 대기와 실제 공유 잠금 획득을 격리 검증했다. 운영 실행은 아직 하지 않았다.

미완료 request 조회는 기존 `request.lock`을 사용하고 파일을 변경하지 않는다. 원래 identity·pins·해시와 재조정 필요 여부를 반환한다. 실행 중 요청의 잠금은 거부한다. 다른 구성의 pending을 제거하거나 complete로 바꾸는 명령은 없다.

## 관리자 시험의 정확한 범위

[system_preflight.py](system_preflight.py)는 `/opt/md-t16-<nonce>`와 `/var/lib/md-t16-<nonce>`에만 시험 실행본/자료를 만든다. 설치된 라이브러리와 source를 해시로 고정하고 root 소유·쓰기 제한을 확인한다. 패키지 resolver·네트워크 설치는 하지 않는다.

전용 system 유닛은 `User=yoonhc`, 최종 업무의 보호·종료 옵션을 유지한다. 업무 데이터 경로는 임시 저장소로 격리하고, 운영 경로는 읽기 전용으로 추가 제한한다. 외부 HTTP는 대역이며 업무 시험에 PrivateNetwork를 추가한다. 현재 호스트 Chrony 조회는 별도의 실제 waiter에서 현재 설치된 binding을 사용한다. **미설치 새 binding을 현재 운영에 적용한 것으로 가장하지 않는다.**

8개 업무의 실제 CLI/저장/결과 계약, 실패한 감시·기록의 비정상 종료, 서로 다른 invocation의 저장 결과 복구를 확인한다. restic 본문을 실행하지 않고 새 wrapper의 실제 manager adapter를 통해 시험 timer 출처·수동 시작·다른 unit의 거부를 확인한다. 실제 운영 restic snapshot을 추가하지 않는다.

운영 서비스는 조회만 한다. mutating systemctl은 등록한 `md-t16-*` 유닛에 제한되며, operating 서비스 start/stop/restart/enable은 허용하지 않는다. 끝나면 전용 유닛/폴더를 정리하고 진단·결과를 보고서에 보존한다. 운영 구성·ledger·4개 정리 mask와 Chrony/두 supervisor PID·invocation의 전후 일치를 별도로 확인한다. 운영 DB는 계속 바뀌므로 전체 DB 해시의 불변을 주장하지 않는다.

## 남은 순서

1. 관리자 시험과 원본 결과·정리 여부 확인은 완료했다. 같은 시험을 반복할 필요는 없다.
2. **system→system 운영 인계 실행기와 최종 외부 계획**도 [별도 폴더](../scheduled-clock-deploy-2026-10-16/deployment-review.md)에 고정했다. 기존 T15 user→system 실행기나 승인을 그대로 사용하지 않는다.
3. 다음은 그 최종 변경 목록·해시·중단/재개/원복 절차의 별도 운영 승인이다.

시각 정책 재설계, Chrony/RTC 변경, 수집기 재시작, T05 재등록/삭제/자동 정리 재개는 수행하지 않았다. 보조 request/attempt/JSONL의 독립 백업·회전은 기존 후속이며, 미완료 요청이 참조한 과거 기록을 먼저 지우면 안 된다는 의존관계를 유지한다.
