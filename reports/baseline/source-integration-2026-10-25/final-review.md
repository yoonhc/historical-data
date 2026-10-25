# 루트 통합본 최종 검토

개발 소스 통합의 완료 판단을 유지한다. 이번 검토에서 새로운 업무 연결 결함은 발견하지 않았다.
운영 배포 전 확인할 코드 고정 범위 한 곳과 조건부 유지보수 개선 두 곳을 확인했다.
업무 구현·정책·운영 파일·service/timer는 변경하지 않았고, 아래 항목을 TODO와 runtime 안내에 연결했다.

## 1. 배포 전: 설치 패키지와 루트 소스의 일치 및 전체 manifest

[`run_job.use_source_tree()`](../../../scripts/run_job.py)는 루트의
`packages/marketdata/src/marketdata`를 선택한다. 수집 runtime의
[`environment_check()`](../../../integrations/runtime/marketdata_runtime.py)는
고정 가상환경에 비-editable로 설치한 `marketdata`를 요구한다.
두 방식 자체는 오류가 아니지만, 하나의 통합본을 배포할 때 두 사본의 일치 확인이 필요하다.

기존 [`seal_runtime.py`](../../../integrations/runtime/seal_runtime.py)는
설치된 패키지의 Python 파일과 `integrations/runtime/*.py`만 receipt에 넣는다.
루트 업무 소스·`scripts/`·의존성 선언·유닛 전체를 고정하는 도구가 아니다.

실제 임시 venv와 복사한 패키지/runtime으로 실행했다. seal의 42개 파일은 모두 그대로 둔 채
임시 루트 `marketdata/__init__.py`에 주석만 추가해도 receipt의 전체 해시는 계속 일치했다.
동일한 Python에서 기본 import는 설치 사본을, wrapper는 바뀐 루트 사본을 선택했다.
운영 실행본을 변조하거나 업무를 호출한 검사가 아니며, 실제 dependency 전체 설치나
수집 `environment_check()`의 운영 통과까지 시험한 것으로 해석하지 않는다.

다음 통합 배포를 준비할 때 설치 패키지와 루트 패키지의 상대 파일 목록·해시를 대조하고,
실제 실행하는 scripts/runtime·의존성·유닛을 포함한 고정 manifest와 UID별 import 검사를 연결한다.
기존 수집용 receipt만으로 통합 배포 전체의 검증을 대신하지 않는다.
현재 별도 `/opt` 운영본의 안전성 문제가 새로 발견됐다는 뜻은 아니다.

## 2. 조건부 개선: 테스트 fixture를 날짜별 보고서에서 분리

[`test_unified_source.py`](../../../tests/test_unified_source.py)의 timer 바이트 검사는
`reports/baseline/source-integration-2026-10-25/origins.json`에서 기대 해시를 읽는다.
현재 저장소에서는 통과하지만 `packages/scripts/integrations/tests`만 내보내면 이 검사는
보고서 파일 부재로 실행할 수 없다. 기존 depth 독립 검사에도 같은 형태의 보고서 참조가 있다.

소스 배포·개발 검사 정비 때 작은 기대 입력과 출처를 `tests/fixtures/`로 고정하면 좋다.
원래 감사 보고서·출처는 유지하며, 검사를 없애거나 현재 출력에서 기대 해시를 즉석 생성하지 않는다.

## 3. 조건부 개선: 요청 identity의 소스 범위

[`request_contract.inputs()`](../../../packages/marketdata/src/marketdata/request_contract.py)는
core·scripts·runtime의 모든 Python 파일을 요청 scope에 포함한다.
따라서 연구 도구·renderer만 수정해도 같은 개발 경로의 미완료 요청은 다른 scope로 판단된다.
이는 변경 누락을 막는 보수적인 동작이며 이번에 검사를 약화하지 않았다.

임시 binding과 pack 입력으로 12회 측정했을 때 파일 78개·약 948KB를 읽고,
로컬 캐시 상태의 중앙 경과시간은 약 4.43ms였다. 이것은 운영 I/O 계측이나 병목 증거가 아니다.
향후 개발·배포 단위를 정비할 때 실제 의존성의 명시적 목록 또는 고정 manifest로
관련 없는 도구 변경의 영향을 줄일지 검토한다. 설정·업무 소스 변경을 놓치거나
이전 request identity를 덮어쓰는 방법으로 해결하지 않는다.

## 수행한 확인과 범위

| 확인 | 결과 |
|---|---|
| 기존 최종 소스/구성 106개 | 해시 변경 0개 |
| 원본 출처 132개 | 해시 변경 0개 |
| 루트 통합 연결 검사 | 16개 통과, [JUnit](final-review-tests.xml) |
| 생성한 scheduled 17개 + T05 4개 유닛 | `systemd-analyze --generators=no --man=no --recursive-errors=no verify` 통과, stderr 없음 |
| 새 export의 import·seal 범위 | 위 1번 경계 재현, [probe 코드](final_review_probe.py)·[결과](final-review-probes.json) |
| 정적 검사 | `ruff check packages scripts integrations/runtime tests`, `git diff --check` 통과 |

정적 유닛 검사는 `/tmp`의 파일과 시스템 기본 의존 유닛으로 수행했다.
service를 설치·시작하거나 실제 namespace의 권한·mount 동작을 검증한 것은 아니다.
추가 16개는 기존 통합 검사 재실행이며 기존 977개에 별도 고유 검사로 더하지 않는다.
앞선 전체 회귀와 수정 후 140개 검사의 근거는 [validation.json](validation.json)을 재사용했다.
운영 전환·실제 자료·전체 무결성·보존 검사를 반복하지 않았다.
