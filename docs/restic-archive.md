# 완료 사본의 restic 병행 보관

`scripts/archive_backups.py`는 기존 daily가 완성한 사본을 restic에도 저장한다.
원본 수집·daily·원래 하드링크 사본과 별도로 실행한다. 9/27 수동 검증 후
[별도 타이머와 날짜별 관찰](restic-scheduled-operations.md)을 적용했다.
[설계와 단계](restic-operations-plan.md)의 1단계 도구다. 기존 백업 대체나 공간 절감 전환이 완료된 것이 아니다.
[9/27 수동 검증 기록](../reports/baseline/restic-archive-2026-09-27/summary.md)에 테스트 70개와
실제 사본 저장·전체 검사·중복 없는 재실행 결과를 남겼다.

## 호스트 준비

[운영 프로필 예시](../configs/restic-archive.example.toml)를 Git 밖의 호스트 설정 파일로 복사한다.
과거 시험용 `backup-host.toml`은 그대로 보존한다. 경로는 모두 절대 경로를 사용한다.

- 백업 SSD를 먼저 마운트하고 `findmnt --mountpoint /mnt/backup -o TARGET,UUID`의 UUID를 등록한다.
- 시험 저장소와 분리된 운영 restic 저장소를 준비 단계에서 한 번 초기화한다. 같은 암호 파일을
  사용할 수 있으며, 파일은 사용자 소유 0600이어야 한다. 암호를 명령 인자나 Git에 넣지 않는다.
- `restic cat config`의 전체 64자리 ID를 `expected_repository_id`에 고정한다.
  도구에는 `init` 동작이 없다. 저장소 부재·다른 디스크·잘못된 암호를 자동 우회하지 않는다.
- `archive_scope`는 출처 저장소를 나타내는 안정된 이름이다. 날짜·실행 시도·현재 호스트명으로
  매번 바꾸지 않는다. VM에 다른 root로 복원한 저장소는 새 범위와 새 전용 `backup_dir`를 사용한다.
- `state_root`와 `reports_root`는 백업 SSD 외부에 둔다. 같은 restic 저장소를 사용하는 같은 호스트의
  모든 운영 프로필은 **같은 state_root**를 사용해야 저장소별 로컬 flock이 공유된다.
  다른 호스트와 동시에 실행하는 분산 작업은 이번 범위가 아니다.
- `enroll_from_utc_date`는 checkpoint 기준 시각의 UTC 날짜다. 포함된 원자료의 시작 연도를 뜻하지 않는다.
  `max_checkpoints_per_run`은 1~3이며 오래된 미처리 사본부터 처리한다.

초기화는 새 경로임을 확인하고 restic 공식 CLI로 명시적으로 수행한다. 운영 도구가 대신 하지 않는다.
명령에는 아래처럼 절대 프로필 경로를 전달한다.

## 실행

프로젝트 루트에서 다음 형식으로 실행한다. `plan`·`status`는 로컬 파일을 읽기만 한다.

```bash
.venv/bin/python -m scripts.archive_backups --profile /home/collector/.config/marketdata/restic-archive.toml plan
.venv/bin/python -m scripts.archive_backups --profile /home/collector/.config/marketdata/restic-archive.toml run
.venv/bin/python -m scripts.archive_backups --profile /home/collector/.config/marketdata/restic-archive.toml status
.venv/bin/python -m scripts.archive_backups --profile /home/collector/.config/marketdata/restic-archive.toml verify
.venv/bin/python -m scripts.archive_backups --profile /home/collector/.config/marketdata/restic-archive.toml restore-check --snapshot-id FULL_64_HEX_ID
```

`run`은 완료 manifest의 해시·source root·시각을 확인하고 기존 `verify_backup`으로 카탈로그,
파일, pack을 검증한 후 저장한다. 정상 종료 코드·전체 snapshot ID·태그·절대 원본 경로·snapshot time·
원본 manifest를 대조해야 저장 완료가 된다. 새로 보관한 배치에는 `check --read-data`를 수행한다.
처리할 것이 없으면 기존 저장/검사 시각을 갱신하지 않는다.

현재 `status`의 저장소 검사 결과는 마지막 실행 기록이다. `repository_live_checked=false`를 명시하며
조회 때 restic 자체를 실행하거나 캐시/잠금을 만들지 않는다. 마운트·UUID·암호 파일 권한·여유 용량과
inode, 입력 탐색 가능 여부는 현재 상태를 확인한다. 저장소 ID의 실제 대조는 `run`·`verify`·
`restore-check` 시작과 끝에 수행한다. 현재 restic 내용을 다시 확인하려면 `verify`를 실행한다.
조회 JSON의 `healthy`, `daily_stale`, `archive_late`, `pending_count`, `unchecked_snapshot_ids`,
`checkpoint_errors`, `last_operation`을 함께 본다. 마운트/입력 조회가 실패하면 대기 개수는 null이다.
`lock_busy`는 현재 전용 잠금 상태이며 `active_operation_record`·`last_phase_started`는 마지막으로
기록한 작업/단계다. 비정상 종료로 기록이 남을 수 있으므로 기록만으로 현재 실행 중이라고 단정하지 않는다.

로컬 잠금이 잡혀 있으면 `lock_busy`와 종료 코드 75로 끝낸다. 해당 시도의 별도 보고서를 남기며
진행 중인 작업의 상태 파일을 덮어쓰지 않는다. 서비스 예약 시 다음 탐색에서 재시도할 수 있다.
나머지 오류는 종료 코드 1이다. 현재 CLI의 JSON 출력은 단계 진행 줄과 마지막 결과 줄로 구성된다.

## 시각·범위·재개 계약

- snapshot `time`: manifest의 `checkpoint_finished_at`을 UTC로 변환한 값. 시간대 없는 입력은 거부한다.
  restic 0.19.1의 `--time` 파싱을 위해 자식 환경의 `TZ=UTC`를 고정하고 소수초까지 대조한다.
- 처리 시각: `archive_started_at`·`archive_finished_at`. checkpoint의 날짜와 별개다.
  종료 기록을 잃고 추출 검증으로 채택한 사본은 원래 호출 시각을 꾸며내지 않고 null과 `adopted_at`을 기록한다.
- 선택 범위: `archive_scope=...`; 개별 식별: `backup_id=...`, `attempt_id=...`.
  상대 경로 `.`로 넣어도 metadata의 paths에는 절대 경로가 남는다. parent 지정은 보관 그룹을 만들지 않는다.
- 정상 재실행: 기존 전체 ID의 존재와 범위·경로·시각·manifest를 대조하고 중복 생성하지 않는다.
- 성공 기록 분실: 후보를 새 디렉터리에 추출한 뒤 전체 `verify_backup`을 통과해야 채택한다.
  여러 후보가 모두 검증되면 ID 순으로 하나를 선택하고 후보 전체와 검증 결과를 보존한다.
  미검증 후보가 하나라도 있으면 자동 채택·추가 저장을 멈추고 점검을 요구한다.
- 알려진 종료 코드 3 등 실패 후보: 보존하고 성공으로 올리지 않는다. 다음 run에서 입력을 다시 검증해
  새 시도로 저장한다. 종료 결과를 모르는 불완전한 사본은 manifest만으로 채택하지 않는다.
- 저장소 검사 실패/중단: 저장된 사실은 유지한다. `verify` 성공 전까지 후속 저장을 막는다.
  자동 unlock/repair는 없다. 잠금이 남았다면 실제 작업 여부와 장애 원인을 확인해야 한다.

상태는 `<state_root>/<repo ID>/<scope>/state.json`, 전용 flock은 `<state_root>/<repo ID>/archive.lock`,
시도 보고서는 `<reports_root>/<run ID>/`에 남긴다. 상태 파일은 fsync와 원자 교체로 기록한다.
restic 명령 인자·종료 결과·실행 시각, 검증 단계 소요 시간, 10초 간격 수집 관측을 보존한다.
상속된 `RESTIC_*` 변수는 제거하고 프로필에 지정된 설정만 사용한다.

## 복원과 후속 단계

`restore-check`는 전체 snapshot ID를 지정해야 한다. 추출 → 원래 사본 검증 → 새 root에 프로그램 복원 →
표본 조회 비교·전체 저장소 검증 → 새 전용 폴더에 독립 첫 백업 → 새 백업 검증을 수행한다.
각 단계의 파일 inode도 분리되어야 한다. 옛 manifest나 source_root를 수정하지 않는다.
원래 완료 사본과 상태 기록이 없어도 snapshot에 있는 manifest와 경로·태그를 검증해 실행할 수 있다.
이때 프로필에는 옛 출처의 문자열 경로와 범위를 유지하고, 복원 출력은 `restore_work_root`에 새로 만든다.
복원 후 실제 새 root의 daily 운용은 새 프로필·전용 백업 폴더를 준비한다. 수집 서비스를 자동 기동하지 않는다.

복원 작업 공간·실패/중복 snapshot·기존 사본을 자동 삭제하지 않는다. 1·2단계에서는 모든 사본을
유지한다. 원본·revision 보존 정책과 날짜별 백업 보관 정책은 별개이며, 최종 보관 기간·작업용 사본
정리는 3단계에서 결정한다. 향후 보관 그룹은 안정된 단일 범위를 선택한 후 검토한다.
현재 도구에는 forget/prune 실행 경로가 없다.

격리 테스트와 실제 사본의 수동 처리·재실행 확인, 별도 타이머 적용까지 마쳤다.
서로 다른 새 checkpoint 날짜 3개의 실제 예약 저장·검사와 새 사본 복원은 현재 관찰 항목이다.
[상태 조회와 롤백](restic-scheduled-operations.md)에 실제 증거의 집계와 복원 실행 규칙을 정리했다.
병행 보관 성공을 주 백업 전환 또는 전체 디스크 공간 절감 완료로 부르지 않는다.
