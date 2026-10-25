# T05 정식 미리보기 완료 — 수동 적용은 별도 승인

**2026-10-17 15:46:25–15:49:30 KST**, 설치된 시스템 preview가 UID 1000으로 실행돼
`preview_ready`, `ok=true`, exit 0으로 끝났다. 소요 시간은 **184.87초**다.
현재 관리 대상 13개 중 **후보 10개·보호 3개**, 등록 범위 이전 **제외 3개**를 확정했다.
삭제는 0개이며 자동 정리 네 mask와 비활성 승인을 유지했다. **T05 전체 완료나 삭제 승인이 아니다.**

[승인 범위](authorization.md), [실행 원본 사본](run.json), [독립 대조](verification.json),
[정확한 사본별 판단](selection-table.md)을 함께 따른다.

## 실행·계획 식별자

| 항목 | 값 |
|---|---|
| 설치 실행본 | `t05-completion-evidence-20261011-c9d413cfe11ec0e7` |
| 시스템 유닛 | `marketdata-checkpoint-retirement-preview.service` |
| UID / PID | `1000` / `2497680` |
| Invocation | `808d3be4ca3548d38572568fa9ce3c3c` |
| Run ID | `9690925e44194b73bfba206ba40360e7` |
| Plan ID | `31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08` |
| Scope ID | `da7b03dba075a816d1c8277298dd123ce1db62e54aa9a85920711a18913a6c22` |
| Plan 파일 SHA-256 | `c928892ace0bb7ddd5cc4751467408d9bd8b10c33ebaeee74194a5e40cd2a25d` |

[plan.json](plan.json)은 실제 control state의 계획과 같은 바이트다. 계획 내용으로 ID를 다시 계산하고,
설치 코드·scope·시각 관측·근거 graph로 후보/보호 선택과 완료 상한을 재계산해 일치를 확인했다.
원본은 `/var/lib/marketdata-checkpoint-retirement/retention/plans/<Plan ID>.json` 및
`/var/lib/marketdata-checkpoint-retirement/runs/<Run ID>/`에 보존한다.

## 현재 후보와 보호 사유

| 구분 | 범위 | 개수 |
|---|---|---:|
| 후보 | 10/06–10/14 사본. 10/08은 두 사본 | 10 |
| 보호 | 10/15·10/16·10/17 사본 | 3 |
| 범위 밖 | 09/25 `initial`, `linked`와 09/26 사본. `before_enrollment` | 3 |

전체 경로·backup ID·manifest/snapshot/tree 해시·완료 상한·기간 여유·후행 사본 ID는
[inventory.csv](inventory.csv)와 [selection.json](selection.json)에 있다.
제외된 세 사본도 이번 정리 대상이 아니므로, 향후 이 계획을 적용하더라도 전체 로컬 사본이 세 개만 남는다는 뜻은 아니다.

후보 모두 최종 유지할 **10/15·16·17 사본 세 개보다 확실히 선행**하며 보수적 `72시간+60초`를 충족한다.
10/14 사본의 완료 상한은 **10/14 14:54:36.002101 KST**, 판단 하한은 **10/17 15:47:14.751411 KST**다.
경과시간 **72시간 52분 38.749초**로 최소 조건보다 **51분 38.749초** 길다.
10/17 사본까지 보존돼 후행 세 개도 갖춰졌으므로 새벽의 검토 대상 9개에서 이번 정식 후보가 10개가 됐다.
기존 7개의 첫 사후 상한과 baseline은 바꾸지 않았다.

판단 관측의 **U는 0.138018739초**로 5초 상한을 통과했다.
20개 현재 관측의 reference age는 **1,850.16–2,030.88초**이며 원값으로 보존했다.
T05는 이를 600초 차단으로 사용하지 않는다. 보호 세 개는 각각 기간 부족과 후행 세 개 미확보 사유가 있다.

예상 회수 공간은 **28,197,318,656바이트, 약 28.20GB / 26.26GiB**다.
후보 파일의 논리 크기 **389.54GB**와는 다르다. hardlink와 할당 블록을 반영한 시점별 추정이며,
열린 파일·파일시스템 공유·동시 변경 때문에 실제 삭제 후 여유 공간 증가는 달라질 수 있다.

## 새 snapshot에서 근거 직접 조회 완료

| 항목 | 값 |
|---|---|
| Backup ID | `9b916470eca7e5da1462dff9c39380869242bf6711861eaea754c010ccf00963` |
| Restic snapshot ID | `0911b25d3a270fad604eeb222748e950ea45383b68d9b451e6d785b92672c58f` |
| 직접 읽은 경로 / 크기 | `/checkpoint-evidence.json` / 42,618바이트 |
| Bundle SHA-256 | `63fe236673bcb857908c3e3e3e7068abfd6bfc4a7d143753478dd5326a12d1fb` |
| 객체 / 관계 | 14개 객체, 최신 사본의 선행 사본 12개 |
| 최신 완료 객체 | `2e242b0a5af450545f5dd7277d7eb4444de3f3cc854d256cd83705363380b92f` |
| 최신 완료 상한 | 10/17 14:46:36.814826 KST |

실제 설치 코드가 명시적 snapshot에서 bundle을 읽고 archive receipt·로컬 sidecar·backup ID/manifest·scope와 대조했다.
반환된 bundle만으로 baseline `5ec056db…`, 완료 상한과 선행 12개 관계를 재구성했다.
이 graph 재구성에 운영 registry나 선행 checkpoint 폴더를 사용하지 않았다.
[반환된 bundle](bundle-07a8273ab9a640bab89e6c6eb7629bc6.json)과 [명령 receipt](command-receipts.json)를 보존한다.

실행 전체의 restic 명령 36건은 모두 exit 0이다. `/backup.json` 조회 22건,
`/checkpoint-evidence.json` 조회 12건(기존 audit·최종 확인에서 여섯 snapshot을 각각 두 번),
scope snapshot 목록과 저장소 설정 조회 각 1건이다. 42,618바이트는 최신 bundle 하나의 크기이며 전체 읽기량은 아니다.
시장 자료 전체 복원이나 `check --read-data` 재실행은 하지 않았다.
같은 snapshot이 포함된 **15:17:02 전체 검사 성공**은 [이미 확인한 당일 보존 근거](../t05-disabled-scope-2026-10-17/backup-archive-status-20261017.md)를 사용했다.

## 실제 검사 범위와 유지 상태

- preview가 maintenance·저장소·checkpoint 잠금 아래 후보의 no-follow 파일 메타데이터 inventory,
  manifest 경로/크기, tree hash와 공간 추정을 기록했다. 이번 결과 대조에서 같은 inventory를 다시 생성하지 않았다.
- **유지 사본 세 개의 전체 물리 내용 해시 검사는 아직 하지 않았다.** 설치된 `Retirement.apply()`가
  별도 승인 후 적용 직전에 유지 사본 `verify_backup()`과 후보 inventory 재대조를 수행한다.
  긴 검사 뒤 최초 삭제 직전에도 새 시각·기간·보호 조건을 확인한다. 이번에는 apply를 호출하지 않았다.
- [전](before.json)·[후](after.json) 대조에서 보호 입력 **91개**, 기존 근거 객체 **14개**와 등록 receipt **1개**가 동일했다.
  새 run 하나만 추가됐고 근거 등록·기존 상한/생성 순서 변경은 없었다.
- `recurring.json` SHA-256은 `14523bb85f8dcdbe3cd4fa40d21e3c11ca8a7bae181071464c68579d4a24fc65`이며
  root:root/0644, `enabled=false`, `approved_at=null`, `approval_reference=null`을 유지했다.
- 사용자·시스템의 자동 정리 service/timer **네 mask**, Chrony 및 두 collector supervisor의 PID·invocation이 동일했다.
  원래 scope 갱신 도구·승인 문서·보존 정책은 바꾸지 않았다.

처음 도구 세션의 sudo 인증이 없어 기동하지 못한 [대기 기록](start-authentication-pending.json)은 보존했다.
이후 사용자 터미널에서 승인된 preview를 시작했다. `--no-block` 반환만으로 성공 처리하지 않고,
[실제 journal 대조](journal-verification.json)와 [system manager의 정상 종료 기록](unit-journal-summary.json)을 확인했다.
큰 journal JSON 메시지는 기본 조회에서 생략되고 두 줄로 분할돼 있었다. `--all` 결과에서 같은
PID·invocation·stream의 두 조각만 연결한 내용이 저장된 run과 정확히 일치했다. 업무를 다시 실행한 것은 아니다.

## 다음 단계

이 계획의 정확한 후보와 유지 범위를 검토해 **첫 수동 적용을 별도로 승인**한 뒤,
적용 직전 현재 시각·당일 보존·구성·잠금·유지 사본 전체 무결성·후보 inventory를 다시 확인한다.
조건이 달라지거나 경합하면 보류하고, 과거 관측이나 비활성 recurring 문서를 삭제 권한으로 사용하지 않는다.
첫 수동 삭제/보존 결과를 확인한 뒤 **반복 자동화는 별도 승인**한다.

이번 결과는 삭제 없는 정식 미리보기 완료에 한정한다. T16의 정기 backup/T05/restic 연결 근거로는 재사용할 수 있지만,
T16의 실제 daily 처리 등 다른 남은 관찰까지 확인했거나 T05 전체가 완료됐다고 표시하지 않는다.
