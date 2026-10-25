# 운영 디스크 사용량 분해 — 2026-10-19

10/19 21:50 KST 집계 기준 `/home` 사용량 약 **277.73GB** 중 이 프로젝트가 **229.43GB**를 차지한다. 가장 큰 항목은 `reports/restic-archive-runs`의 **137.15GB**이며, 그중 **136.27GB는 기존 snapshot의 `/backup.json`을 반복 조회해 영구 저장한 stdout**이다. 시장 자료 본문이 그만큼 새로 증가한 것으로 해석하지 않는다.

이번에는 디렉터리 할당량·작은 receipt·실제 설치 코드와 지정한 출력 두 개를 읽었다. 운영 파일 삭제·이동·절단, 서비스 제어, 용량 보호/시각 정책 변경은 하지 않았다. 수집 장애 복구는 미실시다.

## 파일시스템과 사용량

GB는 10억 바이트, GiB는 2^30 바이트다. 현재 운영 작업을 유지한 채 측정했으므로 숫자는 조금씩 다른 시점의 관측값이다.

| `/home` 파일시스템 | 값 |
|---|---:|
| 장치 | `/dev/nvme0n1p7` |
| 전체 | 301.24GB |
| 사용 | 277.73GB |
| 일반 계정 가용 | 8.13GB / 약 7.57GiB |
| 일반 계정 가용량에서 제외된 예약 공간 | 약 15.38GB |

| 사용처 | 할당량 | 의미 |
|---|---:|---|
| `reports/restic-archive-runs` | 137.15GB | 대부분 반복 manifest 조회 출력 |
| `data` | 56.13GB | 운영 자료. 이 중 packs 약 50.58GB |
| 프로젝트 내부 `backups` | 14.46GB | 기존 로컬 백업. 현재 `/mnt/backup` checkpoint와 별도 |
| `restore-checks` | 12.28GB | 기존 복원 시험 사본 |
| 나머지 프로젝트 | 약 9.42GB | state·기타 보고서·캐시·코드 등 |
| 프로젝트 밖 및 파일시스템 차이 | 약 48.30GB | 다른 프로젝트·앱·다운로드·Timeshift 등 |

프로젝트 전체는 229,431,721,984바이트다. 다른 주요 항목은 Timeshift의 읽기 가능한 부분 약 13.16GB, `.codex` 10.27GB, 다른 프로젝트 약 7.47GB, Downloads 5.57GB, `.config` 4.04GB, 사용자 `.cache` 3.49GB 등이다. Timeshift 일부 root 전용 디렉터리는 조회하지 못해 하한값으로 남긴다. 사용자 홈 264.49GB와 Timeshift의 확인분 13.16GB로 전체 사용량의 대부분이 설명된다.

`/opt`가 속한 `/`와 `/mnt/backup`은 각각 다른 파일시스템이다. 현재 checkpoint/restic 보관 디스크의 사용량은 이 277.73GB에 포함되지 않는다. 따라서 T05가 백업 디스크에서 확보한 공간은 `/home` 가용량을 늘리지 않는다.

[전체 집계·한계](disk-usage.json), [프로젝트 du](usage-project.txt), [사용자 홈 du](usage-home-user.txt), [보고서 du](usage-reports.txt), [기존 백업/복원 시험 du](usage-old-backups-restores.txt), [자료/state du](usage-data-state.txt).

## 가장 큰 증가 원인

실제 서비스가 사용하는 설치 파일은 다음과 같다.

```text
/opt/marketdata/releases/scheduled-clock-policy-20261016-686bb70c9ee4ef11/preserve/scripts/archive_backups.py
SHA-256: 69455f8e2e29387acfc2ab416550d8c656ada8f4d1f93dc833731d5adac4c045
```

설치 코드의 연결을 확인했다.

1. 시스템 restic timer는 매시 UTC 05·35분에 평가한다.
2. `_run()`의 기존 성공 대조(587행 부근)는 **로컬 checkpoint를 이미 정리한 경우도 포함해** 저장된 모든 checkpoint를 순회한다.
3. `match()`(454행)는 `restic dump <snapshot-id> /backup.json`으로 manifest 바이트와 저장 해시를 대조한다.
4. `Restic.command()`(326·361행)는 명령마다 새 UUID의 `.stdout`을 만들고 출력 전체를 저장한다. 검증 뒤에도 그 파일이 남는다.

이번 집계에서 snapshot ID **24개**에 대해 `/backup.json` stdout **13,182개**, 할당량 **136,266,092,544바이트**가 확인됐다. 해당 경로의 JSON receipt는 상대적으로 작다. 큰 원인은 백업 목록을 반복 저장하는 구현이며, restic 저장소의 실제 백업 본문 크기와 구분한다.

최근 완전한 UTC 날짜별 stdout 할당량은 다음과 같다. 이는 다른 작업까지 합친 디스크 전체 증가량이 아니다.

| UTC 날짜 | stdout 할당량 |
|---|---:|
| 10/15 | 10.39GB |
| 10/16 | 11.80GB |
| 10/17 | 11.80GB |
| 10/18 | 12.81GB |

[명령·경로별 집계](restic-report-usage.json), [snapshot별 반복 횟수·지정 표본 대조](dump-repetition.json).

## 실제 중복 표본과 검증 한계

10/19 21:05·21:35 KST에 같은 snapshot `1ed87515008161601d3bdfdf055544fd3e8c079bc5c37fb34b8beb234f734400`에서 읽은 두 파일을 직접 해시했다.

```text
reports/restic-archive-runs/dc629088aae349ef8654213592fbb701/647c71dad5854662a976cf4411c721d5.stdout
reports/restic-archive-runs/aee1d3ee7216446f893087770c5c1e01/fdc4990e37114dce91fcf206820a0ee8.stdout
각 16,095,274바이트
SHA-256: da562891990897b6f4792d97719404cdb0b4a5768733bb771514853cb2b13c88
```

두 출력은 서로 같고 원래 snapshot의 manifest 해시와도 일치한다. 나머지는 정확한 명령의 snapshot/path·파일 크기·할당량으로 분류했다. 136GB 전체 내용을 새로 해시하거나 모든 파일의 삭제 가능성을 검증한 것은 아니다.

`du`는 한 호출 안에서 hardlink를 한 번만 세고 측정 중 예약 작업도 계속 실행된다. 따라서 별도 하위 경로 집계와 프로젝트 전체 집계의 합에는 차이가 있으며, 할당량을 그대로 삭제 시 회수 가능한 용량으로 약속하지 않는다. 시각 로그 `state/runtime-clock` 자체는 약 124MB로, 이번 대용량 누적의 주원인이 아니다.

## 다음 조치 범위

T18의 사용량 원인 조사는 완료했다. 다음은 반복 출력의 계속되는 증가를 막을 보완과, 필요한 검증·복구 근거를 보존하는 공간 확보안이다. receipt·요청·T05/T16 기록이 참조하는 출력과 불변 원본의 의존성을 확인해 정확한 정리/이전 범위를 정한다. 보고서 전체를 일괄 삭제하거나 기존 백업 검증을 생략하는 방식으로 처리하지 않는다.

기존 `backups`·`restore-checks`도 검토 후보지만 이번 조사만으로 삭제를 승인한 것은 아니다. 조치 후 실제 수집 재개·저장·시각 근거와 interrupted collection/공백을 확인해야 T18 복구를 완료할 수 있다. [장애 원인·진행 상태](summary.md), [TODO](../../../TODO.md).

## 보관 기간·조회 주기 개선 검토 — 미적용

사용자가 반복 출력의 최근 며칠 보관과 30분 조회 주기의 필요성을 질문해 실제 호출부를 추가 대조했다. 아래는 검토 제안이며 구현·삭제·운영 변경 승인이 아니다.

- **현재 자동 run:** 30분 예약은 완료 checkpoint 발견·미보관 사본 처리·잠금 보류 후 다음 평가에 사용된다. 기존 모든 snapshot의 manifest 대조가 이 경로에 함께 들어 있다. 전체 읽기 검사 `check --read-data`는 `_run()` 끝에서 저장 snapshot 집합에 기존 검사에 포함되지 않은 ID가 있을 때 실행한다(설치 `archive_backups.py:673`). 매 30분 전체 pack을 다시 검사하는 구조로 표현하지 않는다. 명시적인 verify 명령은 별도다.
- **보관 제안:** 반복 성공 manifest의 본문을 매번 영구 보존하지 않고, 검증한 snapshot ID·manifest 해시·결과·시각 등 작은 기록을 남긴다. 별도 보존이 필요한 원문은 동일 내용 한 벌로 유지하는 방안을 검토한다. 일반 진단 출력은 예를 들어 최근 7일과 총량 상한으로 제한하되, 미해결 오류·미완료 요청·현재 복구/정리 근거가 참조하는 파일은 나이만으로 삭제하지 않는다. 현재 발생률에서 7일치 전체 dump만 남겨도 약 70–90GB이므로 보관 일수 제한만으로 해결됐다고 보지 않는다.
- **조회 제안:** 먼저 출력 누적을 해결하고, 30분 주기는 새 사본·미완료 작업·저장소 신원/존재의 가벼운 확인으로 유지하는 방향을 권한다. 이미 확인한 과거 manifest 전량 재조회는 하루 한 번 또는 명시적인 점검 단계로 분리하는 후보를 검토한다. 신규 보관·중단 복구·불일치 발견에 필요한 검증은 그 처리 시점에 수행하고, 새 snapshot을 포함하는 전체 읽기 검사와 T05 정리 직전의 기존 검증은 유지한다. timer만 1시간/하루로 늦추는 것은 새 사본 편입·잠금 재시도와 15:40 T05 당일 조건을 지연시킬 수 있으므로 별도 효과를 대조해야 한다.
- **근거의 보존 역할:** 설치 T16 `policy/request_contract.py:308`은 restic 요청의 정확한 완료 receipt를 대조한다. 설치 T05 `scripts/retire_checkpoints.py:332,365`는 고정 복원 receipt의 파일/해시, archive state의 검사 성공·snapshot 포함·미완료 여부와 현재 snapshot을 대조한다. 큰 반복 stdout과 이 작은 필수 근거의 보관 기간을 같은 것으로 취급하지 않는다. 모든 과거 참조의 전수 분류나 기존 출력 정리 가능성 검증을 완료한 것은 아니다.

Restic의 [dump](https://restic.readthedocs.io/en/stable/050_restore.html#printing-files-to-stdout)는 snapshot 파일을 stdout으로 내보내고, [check --read-data](https://restic.readthedocs.io/en/stable/045_working_with_repos.html#checking-integrity-and-consistency)는 실제 pack 데이터를 읽어 검사한다. 반복 dump를 실행별 영구 파일로 남기는 것은 현재 자체 실행기의 동작이다.
