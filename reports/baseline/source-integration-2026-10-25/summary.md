# 운영 변경의 루트 개발 소스 통합

상태: **루트 개발 소스 통합·관련 회귀 검증 완료. 운영 미배포.**

## 통합 범위

수집 시각 근거(T04/T11), T13 daily, T16 시각 판단·요청 복구·Decimal 보완,
T05 완료/순서 근거와 반복 정리의 잠금 보류, T18 출력 공유를 하나의 개발 소스로 합쳤다.
기존 고정 실행본·승인·실패·보고서는 보존하며 운영 서비스·설정·자료를 변경하지 않는다.
[선택한 파일별 출처와 변경 전 해시](origins.json)를 보존한다.

- 코어는 T16 preserve/T18의 업무 연결을 기준으로, collector의 시각 근거와 T13 daily 모듈을 추가했다.
  `cli.py`는 공통 루트 기준으로 collector와 T16 변경을 함께 병합했다.
- T05 정리기는 T18 소스 안의 이전 사본으로 되돌리지 않고 실제 반복 운영본의 `clock_retirement.py`·`retire_checkpoints.py`를 사용했다.
- T16 `run_job.py`는 `scripts/`, `request_contract.py`·`job_clock.py`는 `marketdata` 패키지에 두었다.
  세 variant·`parents.json` 경로 의존을 제거하고 같은 소스 트리를 명시적으로 선택한다.
- 읽기 전용 `checkpoint_clock.py` 바이트와 예상 SHA-256은 유지했다. 요청 identity에는 공통 업무 소스·의존성 선언을 포함한다.
  기존 운영 request의 identity나 pins를 변경·이전하지 않는다.
- runtime과 시스템 inventory adapter는 `integrations/runtime/`에 통합했다.
- 8개 시스템 업무의 유닛은 `integrations/systemd/scheduled/` 매개변수 템플릿과 설치 없는 renderer로 연결했다.
  timer 바이트는 가져온 운영 구성과 같다. T05 renderer는 실제 운영의 공통 부모 쓰기 경계를 반영했다.
- 공유 Archive reader의 출력 보관 방식을 호출부에서 구분했다. archiver는 T18의 공유 본문을 사용하고,
  T05는 별도 state/run/commands 경로의 기존 독립 출력·receipt를 유지한다.
  두 경로 모두 실제 dump와 기대 manifest 해시 대조를 수행한다.

## 검증과 해석

검사는 루트 소스를 대상으로 하며 작은 임시 저장소·SQLite·대역 HTTP/Chrony·프로세스·임시 restic 저장소를 사용한다.
운영 backup·삭제·service 시작·전체 시장 자료 검사는 실행하지 않는다.

첫 연결 검사에서 108개가 통과했고, 템플릿 `Description`의 옛 `candidate` 문구까지 소스 경로로
취급하던 검사 한 개가 실패했다. 경로 검사로 좁혀 수정했으며 [첫 결과](first-connections.xml)는 보존한다.
첫 전체 회귀는 **963 통과·14 실패**였다. [원본 결과](full-suite.xml),
[당시 소스 해시](initial-tree.json)를 보존한다. 실패는 모두 통합된 T05 reader가
archiver 전용 출력 공유 함수를 호출하면서 `Output outside its report run`으로 거부된 같은 접점이다.
운영에서는 두 업무가 별도 실행본을 사용하므로 이번 개발 통합에서 생긴 연결 문제다.
Archive에 출력 보관 선택을 추가하고 T05 호출부에서 기존 독립 기록 방식을 선택했다.
경로·해시 검사를 완화하지 않았으며, 그 뒤 업무 소스 변경은 이 두 파일에 한정했다.
수정 후 실패 14개를 포함한 관련 **140개 검사가 모두 통과**했다.
[재검사 결과](final-connections.xml), [최종 소스 해시](final-tree.json),
[전체/재검사 연결 대조](validation.json)를 보존한다.
변경 없는 기존 통과 범위와 수정 후 검사 결과를 합치면 **977개 고유 검사에 남은 실패가 없다.**
두 번째 전체 검사를 실행한 것으로 표현하거나 977+140을 서로 다른 검사 수로 합산하지 않는다.

| 실행 | 실제 결과 |
|---|---|
| 최초 연결 검사 | 108 통과·1 실패. 설명 문구를 경로로 취급한 검사 수정 |
| 전체 루트 회귀 | 963 통과·14 실패, 1,105.97초 |
| 수정 후 영향 범위 | 140 통과·0 실패, 1,091.85초 |
| Python | 루트 개발 환경 3.12.3 |
| 정적 검사·CLI | `ruff check packages scripts integrations/runtime tests`, `git diff --check`, `python -B -I scripts/run_job.py --help` 통과 |

전체 검사에는 임시 restic 저장소에서 실제 보관·검사·복원과 수집 시각 근거 추출도 포함했다.
`CLOCK_TEST_RESTIC`은 기존 `.cache/restic/0.19.1/restic`을 사용했다. 건너뛴 검사는 없었다.
기존 fork 잠금 회귀에서 Python의 다중 thread/fork 경고 1개가 기록됐으며 해당 검사는 통과했다.

가져온 원본 132개(중복 선택 포함)의 SHA-256은 모두 유지됐다. 선택한 업무 모듈 73개의
[정의 단위 대조](definition-review.json)에서 의도한 CLI 병합·경로/식별·유닛 출력·T05 연결 외의
함수/클래스 삭제는 없었다. 이 AST 대조는 실행 검사를 대신하지 않는다.

기존 후보의 실제 구버전 `/opt` CLI를 쓰는 A→B→A 시험은 원본 보고서에 보존한다.
루트 회귀에서는 독립 프로세스의 후속 관측·최신 표시 갱신 후 원래 요청 복구와
새 프로세스 backup/restore로 재연결했다. 이 실행을 구버전 운영 전환의 신규 검증이라고 표시하지 않는다.
Decimal 사건의 캡처 입력은 `tests/fixtures/decimal-recovery/`에 보존하고 운영 파일을 읽거나 종결하지 않는다.

## 범위 밖

루트 소스 통합은 운영 배포가 아니다. 새 고정 실행본의 최종 경로·환경·scope·요청/ledger 전환은
다음 배포에서 정해야 한다. 기존 deployment·repair 도구는 특정 operation/authority의 역사 기록으로 남긴다.
T19 장기 조회/보관 정책, T17 자료 복구와 과거 수집 공백, 남은 시각 정책은 변경하지 않는다.
[TODO의 별도 배포 검토·잔여 과제](../../../TODO.md),
[개발/운영 경로 안내](../../../docs/project-structure.md)에 연결했다.

## 최종 검토

사용자 요청에 따라 통합 경계와 배포·검사 의존성을 다시 검토했다.
최종 소스·구성 106개와 원본 출처 132개 해시는 유지됐고, 연결 검사 16개 및 생성 유닛 21개의
정적 구문 검사를 추가로 통과했다. 기존 전체 회귀는 반복하지 않았다.
기존 수집용 seal만으로 루트 소스까지 고정되지 않는 경계를 임시 가상환경에서 확인했으며,
다음 배포의 전체 manifest·설치/소스 일치 검증으로 연결했다.
검사 fixture와 요청 identity 범위 개선은 조건부 후속으로 남겼다.
[최종 검토·근거·한계](final-review.md)를 참고한다. 업무 구현과 운영 구성은 이번 재검토에서 변경하지 않았다.

## Git 보존 범위

기준 커밋 `bd39826` 이후의 루트 소스 통합·회귀·출처/검사 기록과 문서 정리를 함께 보존한다.
README에는 개발 위치와 검사 명령, 구조·운영 안내에는 실제 import와 미배포 경계,
TODO에는 배포 전 코드 고정 범위와 조건부 개선을 연결했다.
실패한 검사 결과도 원본으로 유지하며, 이번 커밋을 운영 전환이나 새로운 운영 관측으로 해석하지 않는다.

커밋 직전 staged 전체 공백 검사에서 `first-connections.xml`·`full-suite.xml`의 원본
pytest traceback에 있는 줄 끝 공백이 보고됐다. 두 원본 XML은 수정하지 않고 보존했으며,
이 두 파일을 제외한 staged 소스·문서 공백 검사, 정적 검사와 로컬 문서 링크 대조는 통과했다.
최종 소스·구성 106개는 검증 당시 해시와 같아 전체 회귀를 다시 실행하지 않았다.
