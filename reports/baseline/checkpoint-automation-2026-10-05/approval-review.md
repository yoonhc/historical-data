# 자동 checkpoint 정리의 반복 승인 검토 자료

2026-10-05 준비한 범위다. **이 문서는 승인 요청의 구체적 대상이며 승인 완료 기록이 아니다.**
현재 반복 승인 파일은 `enabled=false`, 승인 시각·근거는 `null`이고 새 유닛은 설치되지 않았다.
현재까지 허용된 구현·시험·운영 미리보기만 수행했다. 기존 수동 정리 승인은 이 권한으로 사용하지 않는다.

## 승인할 코드와 정책

| 항목 | 값 |
|---|---|
| 배포본 | `checkpoint-automation-79e29a3ee5152872` |
| code ID | `283dfc4596ccc8ffe81aab9a2b6e756bd5b835ad011bb82a96d51d674838456b` |
| 반복 승인 scope ID | `8e1b3059c148f2ca235ec44faf317f8d680f5a846d4445327363534cfb297c5b` |
| 정책 SHA256 | `005ccc904a8f3cde5e8180812a3b1740f30d1f3ea9dedbdc49620cf0f5eccd88` |
| 기존 archive 프로필 SHA256 | `3dff81754b14edef5b03c1827a9d1654ae5d694433caa34550c9195c0bf138be` |
| 사용자 | `yoonhc`, UID `1000` |
| 범위 | `historical-data-original-v1` |
| restic 저장소 ID | `b5a4434c95fcf4a9eb1339689cdea42c1da08bd34b845193dffce2aa8203e558` |
| 보호 정책 | 검증된 최근 3개 **또는** 완료 후 72시간 미만인 사본 |
| 예약 | 매일 15:40 KST, 당일 재시도·부팅 시 누락 실행·자동 재개 없음 |
| 허용 동작 | 기존 작업용 checkpoint 정리기의 새 plan 생성과 apply |

배포본 루트는
`/home/yoonhc/projects/algoTrade/historical_data/reports/baseline/checkpoint-automation-2026-10-05/release`다.
파일별 해시·재현 기준은 [release.json](release.json), 전체 승인 제안은
[authorization JSON](proposed-settings/checkpoint-retention-authorization.json)에 있다.
배포본에 Git 이력이 없으므로 실제 코드 식별자의 `git_commit=null`을 유지한다.
별도 후보 commit `e6b86224aec31db9ed42c223fbacb8791978d0b9`은 재현 기준으로 구분한다.

## 경로와 실행 환경

| 역할 | 고정 경로 |
|---|---|
| 원래 source root·store | `/home/yoonhc/projects/algoTrade/historical_data` |
| 작업용 사본 입력 범위 | `/mnt/backup/marketdata-historical/automatic` |
| 필수 마운트 | `/mnt/backup` — 기존 프로필의 파일시스템 UUID 확인 유지 |
| restic 저장소 | `/mnt/backup/marketdata-restic` |
| archive 프로필 | `/home/yoonhc/.config/marketdata/restic-archive.toml` |
| Python 진입점 환경 | `/home/yoonhc/projects/algoTrade/historical_data/.venv/bin/python` |
| 해석된 Python 실행 파일 | `/usr/bin/python3.12`, 현재 코드 식별자에 버전 포함 |
| restic 실행 파일 | `/home/yoonhc/projects/algoTrade/historical_data/.cache/restic/0.19.1/restic` |
| 암호 파일 경로 | `/home/yoonhc/.config/marketdata/secrets/restic-password` |
| archive 상태 | `/home/yoonhc/projects/algoTrade/historical_data/archive-state` |
| 실행 기록 | `/home/yoonhc/projects/algoTrade/historical_data/reports/restic-archive-runs/checkpoint-scheduled` |

실행 소스·의존성 선언·Python 버전과 경로, 정책·프로필의 해시, 모든 프로필 경로 및
restic 실행 파일 해시를 함께 확인한다. 암호 값은 승인 파일이나 이 보고서에 넣지 않는다.
경로·scope·사용자·코드·정책이 바뀌면 이 반복 승인을 이어 쓰지 않는다. Proxmox 이전도 새 범위를 검토한다.

기존 설정을 덮어쓰지 않고 다음 파일을 새로 준비했다. 모두 사용자 소유 0600이다.
실제 파일과 [보관한 사본](proposed-settings/checkpoint-schedule.toml)을 비교한 근거는
[준비 기록](profile-preparation.json)에 있다.

| 새 파일 (`/home/yoonhc/.config/marketdata/` 아래) | 역할 |
|---|---|
| `checkpoint-schedule.toml` | 전용 정책과 승인 파일 연결 |
| `checkpoint-retention-automatic.toml` | 최종 보호 정책, `apply_enabled=true` |
| `checkpoint-retention-authorization.json` | 반복 권한 **비활성**, 승인 시각·근거 없음 |
| `checkpoint-retirement-service.env` | 고정 배포본 진입점·Python·설정 경로 |

정책의 `apply_enabled=true`는 기존 정리기에 전달할 최종 설정이다. 이것만으로 자동화 권한이
생기지 않는다. 자동 `run`에는 활성화된 별도 반복 승인과 지정 timer의 실제 실행 출처가 모두 필요하다.
`preview`는 해당 권한 없이도 삭제 없이 계획만 계산한다. 수동 CLI 또는 수동 service 시작으로
반복 권한을 사용하는 경로는 거부한다.

## 매 실행에서 다시 확인할 조건

당일 KST daily의 완료·논리 검사·backup 성공 기록과 같은 backup ID의 입력 검증·restic 저장을
확인한다. 실제 snapshot ID, 명령 summary, 저장 후 전체 읽기 검사 receipt와 대상 포함 여부를
연결한다. 원래 정리기의 마운트·UUID·저장소·scope·복구 증거·manifest·파일 목록 검사도 유지한다.
실제 삭제 전에는 남길 사본의 전체 파일 해시·카탈로그·pack 무결성을 검사한다.

같은 유지 조건·잠금을 적용하면서 후보·보호·편입 전 제외 목록을 새로 계산한다.
각 실행의 새 run ID·plan ID와 정확한 경로·backup/snapshot ID·완료 시각·경과시간·보호 사유를
기록한다. 오늘 목록의 날짜를 반복 삭제 조건으로 쓰지 않는다.

잠금 경합·선행 작업 미완료는 `deferred`이며, 다음 날 다시 평가한다.
미완료 정리 또는 계획을 기록한 실제 자동 적용의 불명확한 중단은 **점검 필요로 보류**하고
새 계획을 만들지 않는다. 검사 실패·입출력 오류는 `failed`, 정상 검사 후 후보 없음은
`no_candidates`, 정리 완료는 `complete`다. 부분 완료 후 오류를 성공이나 처리 0개로 덮지 않는다.

원본·편입 전 사본·restic 지점·시험 공간·복구 키트는 반복 삭제 범위에서 제외한다.
`forget`·`prune`·기존 daily 대체·수집 설정 변경은 포함하지 않는다.

## 이번 미리보기의 정확한 대상

아래 목록은 10/5 18:44:35 KST 판정의 [preview.json](preview.json)을 기준으로 한다.
계획 ID `96584dda25facbc8cd0f53bebaa599e4f82dee91b52710120da7e529b59a097c`,
run ID `34bf48d592f14e8780feb4a9cf1c744d`이며 **삭제 0건**이다.
이 계획을 향후 자동 apply에 넘기지 않는다. 향후 반복 승인은 위 고정 범위에서 새 계획을
계산하고 기존 검사를 통과한 대상에 한정하여 적용하도록 허용하는 별도의 결정이다.

### 후보 1개

- 경로: `/mnt/backup/marketdata-historical/automatic/2026-10-02/7b570c997da346f28e0708d04289e9af`

  backup ID: `801cfce84d4a4b862c8578f8358ac1ff29c104d8ce25b9f98629cf43b7c96263`

  restic snapshot ID: `0c9f8bc23d1125cbfd7e0d3c442af4376298e22c8116f5526170797f0d9b9135`

  checkpoint 기준 시각: `2026-10-02T13:40:34.273336+09:00`

  완료 시각: `2026-10-02T13:43:39.323607+09:00`

  완료 후 경과: `277255.848238`초, 약 **77.02시간**.

  후보 사유: 최신 검증 3개 밖이며 완료 후 72시간 이상.

### 보호 3개

- 경로: `/mnt/backup/marketdata-historical/automatic/2026-10-05/8a0395aeb9084eddb0a97f97b9c0a0a5`

  backup ID: `46f4734b48f7e81c4ef805479c8d2c7b605215c6881b3d6b38a0a3a80d19bc1b`

  restic snapshot ID: `19bde5fb81b7dde7d45e2bba3504a9bd7969c7b2d1414803a528a0b4f1f6d462`

  checkpoint 기준 시각: `2026-10-05T13:53:47.247287+09:00`

  완료 시각: `2026-10-05T13:57:57.417653+09:00`

  완료 후 경과: `17197.754192`초, 약 **4.78시간**.

  보호 사유: 최신 검증 3개에 포함 **및** 완료 후 72시간 미만.

- 경로: `/mnt/backup/marketdata-historical/automatic/2026-10-04/a28359289b804f4e82d900878a820f23`

  backup ID: `70fc23fed2fdba282d5a1adfbd168df8666077f4b4f99984ce56831b74c48b9f`

  restic snapshot ID: `057cd08adc8e07dda2a93eb97f4ecfacce76a60971beb5c0d795566d1202f57b`

  checkpoint 기준 시각: `2026-10-04T13:47:56.871489+09:00`

  완료 시각: `2026-10-04T13:51:41.756915+09:00`

  완료 후 경과: `103973.414930`초, 약 **28.88시간**.

  보호 사유: 최신 검증 3개에 포함 **및** 완료 후 72시간 미만.

- 경로: `/mnt/backup/marketdata-historical/automatic/2026-10-03/7e178c962a2d4869903423e3d62fe93d`

  backup ID: `e971e8b4b008fd5144f0ee6e8125ace882d2bdf1c6bf9f1fba23f0f5a1ac2e30`

  restic snapshot ID: `d27a0a557e1795a4651132016491f1b2c6735e8181b71303f3adccd414f0f8ad`

  checkpoint 기준 시각: `2026-10-03T13:47:03.758567+09:00`

  완료 시각: `2026-10-03T13:50:27.280109+09:00`

  완료 후 경과: `190447.891736`초, 약 **52.90시간**.

  보호 사유: 최신 검증 3개에 포함 **및** 완료 후 72시간 미만.

### 편입 전 제외 3개

- 경로: `/mnt/backup/marketdata-historical/automatic/2026-09-25/initial`

  backup ID: `e6d4a8f8936990f158cb5a8cc91f419a933eb0f19610e5c4ad30c71a877a2240`

  checkpoint 기준 시각: `2026-09-25T09:20:42.974288+09:00`

  완료 시각: `2026-09-25T09:22:00.094665+09:00`

  제외 사유: `before_enrollment`. 운영 restic snapshot ID가 있다고 가정하지 않음.

- 경로: `/mnt/backup/marketdata-historical/automatic/2026-09-25/linked`

  backup ID: `a3f90efe758c2bc584d7e57e5ec1b5e98b8384637e65e5e547591699d5b9636c`

  checkpoint 기준 시각: `2026-09-25T09:22:38.200297+09:00`

  완료 시각: `2026-09-25T09:23:19.647654+09:00`

  제외 사유: `before_enrollment`. 운영 restic snapshot ID가 있다고 가정하지 않음.

- 경로: `/mnt/backup/marketdata-historical/automatic/2026-09-26/d4e81e6b027349cdb2e372da4f47288d`

  backup ID: `e6e2e02c95a0498aa88c6199866cddde05f3a0bba67b81d0566f57289dddfac7`

  checkpoint 기준 시각: `2026-09-26T13:20:34.361121+09:00`

  완료 시각: `2026-09-26T13:21:56.003362+09:00`

  제외 사유: `before_enrollment`. 운영 restic snapshot ID가 있다고 가정하지 않음.


## 활성화 후 조회와 새 예약만 중지

아직 활성화하지 않았다. 향후 승인·설치 시에는 고정 파일과 scope를 재확인하고, 실제 승인 시각·근거를
기록한 뒤 유닛과 실행 환경을 점검한다. 로그인과 무관한 사용자 서비스 실행 조건도 그때 재확인한다.
최초 실제 예약의 실행 출처·결과와 다음 daily/restic 연결은 운영 관찰로 따로 기록해야 한다.

```bash
systemctl --user status marketdata-checkpoint-retirement.timer
systemctl --user status marketdata-checkpoint-retirement.service
journalctl --user -u marketdata-checkpoint-retirement.service
```

`deferred`의 종료 코드 75도 systemd에서는 success일 수 있다. 위 journal과
`reports/restic-archive-runs/checkpoint-scheduled/<run_id>/run.json`의 state·사유·점검 필요 여부를 함께 읽는다.

**새 정리 예약만 중지**하는 절차는 다음 한 명령이다.

```bash
systemctl --user disable --now marketdata-checkpoint-retirement.timer
```

이미 실행 중인 정리는 계속될 수 있다. 진행 중 작업까지 중단할 필요가 별도로 있으면
`systemctl --user stop marketdata-checkpoint-retirement.service`를 사용하고 미완료 journal을 점검한다.
다음 자동 실행으로 무조건 재개시키지 않는다. 이 중지는 삭제를 되돌리지 않으며 기존 수집·daily·restic은 유지한다.
