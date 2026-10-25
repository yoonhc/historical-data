# T05 자동 정리 후보 — 잠금 보류 후 재평가

**삭제 전에 잠금 획득이 실패한 실행만 다음 예약에서 재평가하도록 보완했다.**
새 후보·시험 도구의 61개 검사와 별도 경로 재구성은 통과했다.
**10/18 14:34:26 KST 관리자 재점검까지 통과했다.** 첫 시험의 임시 유닛 수명 문제는
시험 도구만 보완했으며, 원래 실패 기록을 유지한다.
운영 실행본·승인 문서·네 mask를 변경하지 않았으며, 자동 활성화 승인은 받지 않았다.
10/17 첫 수동 정리와 사후 검증의 완료 상태는 유지한다.

## 고정 후보와 변경

후보는 `t05-recurring-retirement-20261018-e57a7995f03fb301`이다.
기준은 설치된 `t05-completion-evidence-20261011-c9d413cfe11ec0e7`의 검증한 소스 151개다.
기존 소스 149개를 유지하고 실행 파일 2개를 변경했으며 시험 파일 1개를 추가했다.

| 파일 | 변경 |
|---|---|
| `scripts/retire_checkpoints.py` | 기존 preservation·checkpoint 잠금 문맥을 그대로 사용하는 진입 연결 추가. 기본 정리기의 동작은 유지 |
| `scripts/clock_retirement.py` | 잠금 문맥 진입 실패와 진입 이후 오류를 구분하고, 정상 확정된 보류 근거만 다음 자동 실행에서 인정 |
| `tests/test_recurring_deferral.py` | 실제 프로세스 간 잠금·SIGKILL·기록/종료 실패와 새 계획 재평가 검사 |

[전체 변경](candidate.patch), [파일별 해시](manifest.json), [고정 소스](source.tar.gz).
소스 archive SHA-256은 `dedc50a59b7a6b75c575e387fbbec6253e9e1c9df9328e8ec4a527fa3b0d0c26`이다.
`work/`는 구현·시험용 작업본, `reproduced/`는 archive를 다시 푼 검증본으로 Git 보관 대상에서 제외한다.
실행본의 권위 있는 입력은 고정 archive·manifest·patch다. 운영 `/opt`를 덮어쓰지 않았다.

## 재평가를 허용하는 경계

기존 코드는 계획 뒤 `deletion_attempted=true`를 먼저 저장한다.
그 뒤 잠금을 얻지 못하면 삭제·journal 생성 전이어도 다음 실행이 미완료 삭제로 보았다.
[이전 설치본의 대조](../t05-recurring-preparation-2026-10-18/deferral-probe.json)는 그대로 보존한다.

새 코드는 잠금 문맥의 `__enter__`에서 발생한 명시적 Busy만 구분한다.
진입이 성공한 뒤 본문이나 `__exit__`에서 발생한 오류에는 같은 근거를 부여하지 않는다.
checkpoint 입력 잠금 단계에서 빈 staging·잠금 메타데이터가 생길 수 있으므로
이를 ‘파일 쓰기 전’이라고 표현하지 않는다. 해당 사본의 이동·삭제 본문에 들어가지 않았다는 근거다.

새 `pre_application_lock_deferral`은 run·plan ID, 진입 단계, 원래 예외 유형을 기록한다.
`finished`/`deferred`/exit 75·삭제 0개·정상 종료까지 최종 보고서가 확정돼야 재평가에 사용한다.
**원래 `deletion_attempted=true`와 실패 보고서는 그대로 남는다.**
journal 부재나 삭제 수 0만으로 과거 상태를 지우거나 완료로 표시하지 않는다.

| 이전 실행 상태 | 다음 자동 실행 |
|---|---|
| 계획 생성 중 일반 잠금 보류 | 기존대로 현재 상태 재평가 |
| 적용 잠금 진입 실패 + 새 근거 + 정상 최종 저장 | 매번 새 계획으로 현재 사본 재평가 |
| 진입 후 Busy, 알 수 없는 오류, 감시 종료/최종 기록 실패 | 점검 필요 유지 |
| 근거 확정 전 SIGKILL, 기존의 미완료 intent | 점검 필요 유지 |
| 미완료 journal 존재 | 새 계획으로 우회하지 않고 점검 필요 유지 |
| 해당 plan의 완료 journal 존재 | 기존 완료 근거를 인정하는 현행 동작 유지 |

보존·무결성·시간·승인 정책, 실제 삭제 엔진과 기존 성공 plan/journal은 바꾸지 않는다.
수동 EXDEV 복구의 `scope_transition`을 자동 실행에 재사용하지 않는다.

## 이번 검증

| 범위 | 결과와 실제 사용한 구성 |
|---|---|
| 새 경계 검사 | **42개 통과**. 임시 실제 저장소/restic, 별도 프로세스의 maintenance·repository·checkpoint 잠금, SIGKILL, 종료/기록 실패. 시각과 manager 출처는 대역 |
| 관련 기존 회귀 | **4개 통과**. 삭제 중 시각 오류, 근거/대상 변경 거부, 정확한 미완료 삭제 재개, journal 전 강제 중단의 후속 차단 |
| 관리자 시험 도구·guard 검사 | **15개 통과**. 기존 12개와 임시 timer의 후속 예약·사라진 유닛 거부·중간 결과 보존 3개. 보호 옵션/쓰기 격리, 입력 변경 거부, 실제 fragment 비교 함수, 중복 출력 거부, scope 차이 포함 |
| 별도 경로 재구성 | 소스 152개·환경 1,646개 대조, 옮긴 Python/CLI/import 경로 확인. 61개를 다시 실행한 결과로 세지 않음 |
| 유닛 | 4개 `systemd-analyze verify` 통과. 실행 경로만 검증용 재구성 경로로 치환했으며 실제 설치 시험과 구분 |

[61개 검사별 ID·해시](validation.json), [재구성](reproduction.json), [유닛 검사](unit-validation.json).
이전 215개와 첫 수동 실제 삭제·mount 시험은 변경되지 않은 계약의 기존 근거로 연결한다.
이번 61개에 합산하지 않는다. 앞선 38개 경계 검사와 도구 12개도 최신 검사의 부분 실행이므로 중복 합산하지 않는다.
운영 checkpoint 삭제나 추가 backup, 전체 시장 자료 검사는 실행하지 않았다.

14:28–14:29 KST [첫 관리자 시험](system-preflight-result-dc353c8dacb4461c.json)은
첫 timer 실행 종료 후 임시 유닛이 자동 회수되어 수동 시작 검사에서 실패했다.
운영 전후 동일·시험 경로/유닛 부재는 확인했다. 첫 실행의 상세 결과를 다음 단계까지 메모리에만
보유했던 도구 문제도 함께 고쳐 각 단계 결과를 보존한다. 원래 보고서와 [v1 도구](preflight-v1/system_preflight.py)는 유지한다.
[수정 범위·해시](preflight-repair.json)는 시험 도구뿐이며 고정 후보·배포 유닛·scope는 동일하다.

14:33:49–14:34:26 KST [재점검 원본](system-preflight-result-fe42ff4321294b9a.json)과
[후속 대조](system-preflight-review.json)에서 실제 system timer 실행과 수동 시작을 각각 확인했다.
두 invocation은 다르고 UID 1000·고정 후보 import·실제 fragment guard를 통과했다.
읽기 전용 Chrony 관측은 U 약 0.134초로 유효했으며, 비활성 승인에서 두 실행 모두
`authorization_pending`/exit 75·삭제 진입 false·삭제 0개였다.
보호 구성 15개·네 mask·Chrony/두 supervisor·boot가 전후 동일하고 임시 두 유닛/경로도 정리됐다.
이는 관리자 보호 환경과 출처/비활성 승인 경로의 완료다. 운영 자동 삭제나 최종 경로 설치 완료로 세지 않는다.
고정 입력인 `validation.json`·`preflight-repair.json`의 당시 pending 표시는 원본으로 유지하고,
이후 관리자 완료는 별도 review에 연결한다. 기존 61개 검사 수에 root 실행을 임의로 더하지 않는다.

구버전 guard에 새 보류 기록을 남기는 방향도 [별도로 대조](old-guard-compatibility.json)했다.
구버전은 새 근거를 이해하지 못해 `unfinished_retirement`로 막는다.
이는 설치된 구버전 guard에 합성 보고서를 준 검사이며 전체 구성 원복 시험이 아니다.
따라서 원복은 비활성·mask 상태로 끝내며, 새 보고서를 지워 구버전 자동 실행을 통과시키지 않는다.

## 자동 구성과 남은 범위

현재 수정된 canonical 유닛을 복사해 실행본 경로만 새 후보로 바꿨다.
**옛 renderer는 실행하지 않았다.** [최종 후보 바이트·before/after 해시](deployment-files.json)를 사용한다.
공통 부모 `ReadWritePaths=/mnt/backup/marketdata-historical`, UID 1000,
15:40 KST·`Persistent=false`·`Restart=no`·`SuccessExitStatus=75`는 유지한다.

현재 scope `84a0b92a…`에서 후보 scope `311ab16f…`로 바뀌는 입력은 `code`와 `system_units`뿐이다.
[비활성 초안](projected-recurring.disabled.json)은 `enabled=false`·승인 필드 null을 유지한다.
이는 [설치 전 계산](scope-projection.json)이다. 실제 설치 후 UID 1000의 `propose` 결과와
정확히 일치하기 전에는 활성화에 사용할 수 없다. baseline·완료 상한·생성 순서는 재등록하지 않는다.

완료한 [전용 관리자 환경 시험](system-preflight.md)을 [운영 적용 검토안](deployment-review.md)에 연결했다.
실행 가능한 최종 설치/활성화·중단/원복 도구와 해당 최종 operation plan은 아직 고정하지 않았다.
다음은 그 도구·정확한 before/after·중단 검사를 마친 뒤 별도 운영 승인을 받는 단계다.
현재 단계의 완료를 반복 자동화 또는 T05 전체 완료로 표시하지 않는다.
