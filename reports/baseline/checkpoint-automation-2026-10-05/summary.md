# 15:40 KST 작업용 checkpoint 자동 정리 후보

2026-10-05 구현·격리 시험·운영 삭제 없는 미리보기 결과다. **자동화 배포본 준비까지 완료했고,
타이머 설치·활성화와 반복 삭제 승인은 미완료다. 이번 작업의 운영 삭제는 0건이다.**
기존 수집·daily·restic 예약은 유지했으며 운영 editable 소스 94개 파일도 변경하지 않았다.

## 구현과 고정 배포본

기존 `Retirement.plan()`·`apply()` 앞에 당일 선행 작업과 반복 승인 검사를 추가했다.
정리기의 기존 무결성 검사·복구 증거 대조·잠금·중단 후 처리 방식은 유지했다.
원래 정리기 자체의 변경은 자동 실행 ID와 승인 범위 ID를 계획 해시에 선택적으로 포함하는 부분이다.

- 최신 검증 사본 3개와 완료 후 72시간 미만 사본을 합집합으로 보호한다.
- KST 당일 daily 완료·검증과 **동일 backup ID**의 restic 저장 및 `check --read-data` 성공을
  보고서·명령 receipt·snapshot ID로 연결한다. 정해진 시간이 지났다는 이유로 완료를 추정하지 않는다.
- 기존 maintenance → archive → 사본 부모 잠금 순서 안에서 검사한다. reader와 경합하면 보류한다.
- 미완료 정리 journal이나 적용을 시작한 뒤 완료 근거가 없는 자동 실행이 있으면 새 계획을 만들지 않는다.
  강제 종료가 정리 journal 생성 전에 발생한 경우도 점검 필요로 남긴다. 자동 재개·당일 재시도는 없다.
- 실행 사용자·코드·정책·프로필·경로·scope·저장소 ID·restic 실행 파일을 반복 승인 범위에 묶는다.
  기존 특정 수동 계획 승인은 자동 승인으로 받아들이지 않는다.

후보는 별도 Git 기준으로 고정하고, 운영 소스와 분리한 읽기 전용 `release/`에 138개 파일을 내보냈다.
전용 진입점이 이 배포본의 Python 모듈을 사용한다. Git 없는 배포본의 코드 식별자는
`git_commit=null`을 그대로 기록하며, 파일 해시로 고정한다. 후보 commit을 운영 commit으로 가장하지 않는다.

| 항목 | 식별자·근거 |
|---|---|
| 원래 기준 commit | `9d2d136e7aadd65c38937095daeb33f5211f94bf` |
| 별도 후보 commit | `e6b86224aec31db9ed42c223fbacb8791978d0b9` |
| 배포본 | `checkpoint-automation-79e29a3ee5152872` |
| code ID | `283dfc4596ccc8ffe81aab9a2b6e756bd5b835ad011bb82a96d51d674838456b` |
| 반복 승인 scope ID | `8e1b3059c148f2ca235ec44faf317f8d680f5a846d4445327363534cfb297c5b` |
| 파일별 해시 | [release.json](release.json) |
| 재현 자료 | [candidate.patch](candidate.patch), [source.tar.gz](source.tar.gz) |
| 구현 계약 | [배포본의 운영 문서](release/docs/checkpoint-scheduled-retirement.md) |

호스트에는 기존 파일을 덮어쓰지 않고 자동화 전용 설정 4개만 준비했다.
반복 승인 파일은 **`enabled=false`, `approved_at=null`, `approval_reference=null`**이다.
별도 자동화 정책의 `apply_enabled=true`는 미리보기와 향후 실행의 정책 해시를 같게 유지하는 최종 설정이며,
반복 실행 승인 자체가 아니다. `preview`는 `apply`를 호출하지 않는다.
정확한 파일 경로와 향후 승인 범위는 [승인 검토 자료](approval-review.md)에 있다.

## 격리 시험과 재현

최종 후보의 깨끗한 별도 checkout에서 관련 **69개 테스트가 모두 통과했다**(실패·오류·skip 0, 약 617초).
정리기·공유 잠금·코드 식별자·restic 예약의 기존 회귀 검사와 자동화 신규 10개 검사를 포함한다.
프로젝트 전체 시험을 다시 실행한 결과는 아니다.

| 확인한 상황 | 결과 |
|---|---|
| 승인 비활성·수동 승인 재사용·코드/정책/경로 변경·잘못된 예약 출처 | 자동 삭제 거부 |
| 당일 daily 미완료·저장/전체 검사 대기·잠금 경합 | 사유를 남기고 `deferred` |
| backup 경로·snapshot summary·검사 receipt 불일치 | 실제 오류로 `failed` |
| 미리보기 반복, 같은 시계에서 실행 | 실행마다 새 run ID·plan ID, 삭제 없음 |
| 정리 대상 이동 뒤 강제 종료, journal 생성 전 강제 종료 | 다음 자동 실행은 점검 필요로 보류 |
| 계획 뒤 승인 철회, 유지 사본 검사 오류 | 삭제하지 않음 |
| 격리 정리 → 남은 사본 검증 → 다음 실제 daily → restic 저장 → 재실행 | 정상 연결, 중복 저장·삭제 없음 |

격리 통합 시험은 임시 저장소에서 실제 restic을 사용했다. 시계·마운트 식별·systemd 예약 출처는
fixture에서 통제했다. 실제 운영 타이머가 실행됐다는 증거로 계산하지 않는다.
초기 9개 시험 중 2개 실패는 고정 시각의 계획 ID 중복과 시험의 잘못된 함수 인자였다.
각각 실행 식별자를 계획에 포함하고 시험 호출을 바로잡았다. 이후 68개 통과에 더해
journal 생성 전 강제 종료 검사를 추가한 최종 69개 결과를 기준으로 삼았다.
기존 fork 잠금 시험에서 DeprecationWarning 1건이 있었고 해당 시험은 통과했다.

[시험 기록](test-evidence.json), [최종 JUnit](clean-final-tests.xml),
[패치 재현 대조](patch-reproduction.json)에 근거를 남겼다. 별도 기준 checkout에 패치를 적용한
138개 파일이 고정 배포본과 일치했다. systemd 유닛 구문 검사도 통과했다.
[최종 산출물 대조](final-validation.json)는 배포본·패치 해시, 운영 소스 94개 불변,
실제 비활성 승인 파일과 새로 계산한 범위의 일치, 설정 파일 권한과 문서 링크를 확인한다.

## 운영 미리보기

**10/5 18:44:22~18:44:45 KST**, 삭제 없는 `preview`가 `preview_ready`로 종료했다.
판정 시각은 **18:44:35 KST**이며, 전체 계획 ID는 다음과 같다.

`96584dda25facbc8cd0f53bebaa599e4f82dee91b52710120da7e529b59a097c`

| 분류 | 사본 | 판정 |
|---|---|---|
| 정리 후보 | 10/2, 1개 | 완료 후 약 77.02시간, 최신 3개 밖 |
| 보호 | 10/3·10/4·10/5, 3개 | 각각 최신 3개에 포함되고 완료 후 72시간 미만 |
| 제외 | 9/25 2개·9/26 1개 | 운영 restic 편입 전 |

오늘 daily는 13:58:02 완료, 같은 사본의 restic 저장은 14:09:27 완료,
해당 snapshot을 포함한 전체 읽기 검사는 14:10:12 완료했다.
backup ID `46f4734b48f7e81c4ef805479c8d2c7b605215c6881b3d6b38a0a3a80d19bc1b`와
snapshot ID `19bde5fb81b7dde7d45e2bba3504a9bd7969c7b2d1414803a528a0b4f1f6d462`를 연결했다.
이것은 [앞서 확인한 정기 실행](../checkpoint-retirement-followup-2026-10-05/summary.md)의
실제 기록을 재확인한 것이며, 이번에 전체 읽기 검사를 새로 실행한 것은 아니다.

미리보기 전후 **로컬 manifest 7개·restic 지점 9개·archiver 사본 상태·정리 journal이 그대로**였다.
후보 논리 파일 크기는 22.16 GB, 하드링크를 고려한 예상 회수는 약 1.65 GB이며 실제 회수는 0이다.
원본·restic 지점·시험 공간·복구 키트를 삭제하지 않았고 `forget`·`prune`을 실행하지 않았다.
[전체 계획·당일 증거](preview.json), [전후 대조](preview-results.json), [정확한 목록](approval-review.md)을 참조한다.

미리보기는 기존 plan 검사를 수행했다. **유지 사본 파일 전체 해시·카탈로그·pack 검사는
실제 적용 직전의 기존 단계로 남아 있다.** 이번 운영 미리보기에서 이 검사를 다시 했거나
독립 복원·원본 전체 논리 검사를 수행했다고 확대하지 않는다.

## 예약 상태와 결과 구분

15:40 KST는 유닛의 `06:40 UTC`로 지정했다. `Persistent=false`, `Restart=no`이며,
놓친 예약의 부팅 직후 실행이나 보류 당일 재시도는 없다. 다음 날 새로 평가한다.

| 결과 | 의미 |
|---|---|
| `no_candidates` | 모든 계획 검사를 통과했고 후보 0개 |
| `deferred` | 조건 미충족·경합·미완료 정리 등으로 보류; 사유와 점검 필요 여부 기록 |
| `failed` | 대조·무결성·입출력 오류 등 실제 실패; 부분 완료와 미완료를 구분 |
| `complete` | 해당 새 계획의 정리 완료 |
| `preview_ready` | 후보 있는 미리보기 통과; 삭제하지 않음 |

각 평가를 `reports/restic-archive-runs/checkpoint-scheduled/<run_id>/run.json`에 남긴다.
계획 전에 보류되면 `plan_id=null`, `selection_assessed=false`이며 이를 처리 0개 성공으로 세지 않는다.
systemd는 예상된 보류 종료 코드 75도 success로 처리하므로 **receipt의 state가 결과 판단 기준**이다.

[18:51 서비스 조회](services.json)에서 새 service·timer는 모두 `not-found`였다.
기존 두 스트림은 실행 중이고 daily·restic·pack의 최근 실행은 exit 0이었다. 재시작은 하지 않았다.
[유닛 검사](unit-validation.json)의 10/6 15:40은 달력상 다음 시각 계산이며 실제 등록된 예약이 아니다.

향후 설치 후 새 정리 예약만 중지하려면 다음을 사용한다.

```bash
systemctl --user disable --now marketdata-checkpoint-retirement.timer
```

이는 이미 실행 중인 정리까지 중단하지 않는다. 진행 중 작업 중단이 따로 필요하면
`systemctl --user stop marketdata-checkpoint-retirement.service` 후 미완료 journal을 점검한다.
기존 수집·daily·restic 예약을 중지하거나 삭제된 사본을 복원하는 명령은 아니다.

남은 단계는 **이 배포본과 범위에 대한 반복 삭제 승인, 유닛 설치·활성화, 첫 실제 예약 결과 확인**이다.
그때도 오늘의 미리보기 계획을 적용하지 않고 새 계획을 만든다. 첫 자동 정리 뒤 다음 daily →
restic 연결 확인은 아직 발생하지 않은 후속 관찰이며, 기존 초기 3/3 관찰과 혼합하지 않는다.
