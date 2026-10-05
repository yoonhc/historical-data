# 백업·복원과 저장 용량 점검

`marketdata backup`은 카탈로그에 등록된 원본, 모든 표준 revision, 스냅샷 manifest와
일관된 SQLite 사본을 새 디렉터리에 보존한다. 비활성 revision, 격리 원본, 반복 HTTP
응답 기록, 품질 문제와 운영 이력도 포함한다. 실행 중인 수집기를 정지할 필요는 없다.
aggTrade·호가 depth 이벤트 세그먼트와 수집 이력도 포함하며, 이벤트 테이블이 없던 이전 백업의 검증도
지원한다. 이벤트 세그먼트는 고정 스냅샷에 포함되지 않았더라도 카탈로그 등록분을 모두 복사한다.

카탈로그를 복사할 때만 기존 writer 잠금을 잡고 SQLite backup API를 사용한다. 잠금을
반납한 다음 복사된 카탈로그의 목록으로 불변 파일을 복사하므로, 이후 도착한 데이터가
중간에 섞이지 않는다. 카탈로그 기준 시각 구간과 실제 잠금 유지 시간은 manifest에 남긴다.
이 방식은 원문 바이트와 모든 revision·스냅샷을 유지하는 저장 정책을 전제로 한다.
이벤트 파일의 검증된 중복 사본을 정리하는 동안에는 공유 파일 배치 잠금으로 백업의 입력을
고정한다. 묶음이 있는 저장소는 ZIP과 논리 파일 인덱스를 함께 보존하는 format 2로 백업하며,
이전 format 1도 계속 검증·복원할 수 있다. [묶음 저장·무기한 보관 정책](file-packing-and-retention.md)
[Python SQLite backup API](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup),
[SQLite 온라인 백업 설명](https://www.sqlite.org/backup.html)

기본 여유 공간 기준은 10 GiB이며 `--min-free-gib`로 바꿀 수 있다. 복사할 파일 크기와
최소 여유 공간을 합한 용량이 없으면 완료 처리하지 않는다. 백업 생성 명령 자체는 옛 사본을 삭제하지 않는다.
데이터 보관 기간은 무기한이다. `pack-prune`은 원문 바이트가 동일하게 보존된 개별 중복 사본만
정리하며, 오래된 논리 데이터의 삭제나 용량 부족 시 자동 삭제를 수행하지 않는다.
원본·revision 보존과 날짜별 백업 복구 지점의 보관 정책은 별개다. restic 병행 운영의 1·2단계에서는
어떤 백업 사본도 삭제하지 않았으며, 작업용 사본 정리는 별도의
[3단계 전환](restic-operations-plan.md)에서 다룬다.
10/3의 [독립 복구·보관 정책 검토](restic-transition-and-retention.md)에서 작업용 최근 3개와
날짜별 restic 보관, 정리 조건·제외 범위를 구체화했다. 이후 10/5 승인한 첫 수동 정리를 마쳤고,
19:38에는 **최근 검증 3개 + 완료 후 72시간 미만 보호**를 유지하는 별도 자동 정리 예약을 활성화했다.
[활성화 근거와 중지 절차](../reports/baseline/checkpoint-automation-activation-2026-10-05/summary.md)에
첫 예약 10/6 15:40 KST와 아직 미완료인 운영 관찰을 남겼다. 기존 daily는 유지하며,
restic `forget`·`prune`과 시험 공간 삭제는 적용하지 않았다.

프로젝트 루트에서 새 이름을 선택해 실행한다.

```bash
# 데이터 크기, 파일 수, 디스크 여유 공간과 inode 확인
.venv/bin/marketdata storage --report reports/storage.json

# 매번 새 경로를 사용한다. 아래 checkpoint-01은 예시 이름이다.
.venv/bin/marketdata backup --output backups/checkpoint-01
.venv/bin/marketdata backup-verify backups/checkpoint-01

# 기존 데이터 저장소를 덮어쓰지 않고 별도 경로로 복원한다.
.venv/bin/marketdata restore backups/checkpoint-01 --output restore-checks/checkpoint-01
.venv/bin/marketdata --root restore-checks/checkpoint-01 validate
```

백업과 복원은 기존 디렉터리를 거부한다. 기본 백업은 독립 사본이며, 아래의 재사용 옵션을
지정한 백업끼리만 하드링크를 공유한다. 원본과 복원본은 항상 독립적으로 복사한다.
`data/`와 `state/` 내부에 백업을 만들 수 없으며, 복원 위치는 백업 밖의
새 경로여야 한다. 복원한 저장소의 수집 서비스는 자동으로 시작하지 않는다.

## 증분 저장과 외부 디스크

```bash
# 이전의 완료된 사본과 같은 파일시스템에 새 복구 지점을 만든다.
.venv/bin/marketdata backup --output backups/checkpoint-02 --reuse-from backups/checkpoint-01

# 실제 운영 목적지. 전용 디렉터리는 미리 만들어 두어야 한다.
.venv/bin/marketdata maintenance --mode daily --incremental \
  --backup-dir /mnt/backup/marketdata-historical/automatic --backup-mount /mnt/backup
```

`--reuse-from`은 경로·종류·SHA-256·크기가 같은 불변 파일을 이전 백업과 하드링크로 공유한다.
새 파일과 일관된 SQLite 카탈로그는 새로 복사한다. 카탈로그와 전체 파일 목록은 각 복구 지점에
독립적으로 들어 있으므로 이전 디렉터리 경로가 없어도 현재 사본을 검증·복원할 수 있다.
파일 단위 중복 제거이며, 파일 일부의 중복 제거는 아니다. 개별 파일을 ZIP으로 묶는 배치 변경은
새 파일 저장으로 계산된다. 하드링크가 불가능하면 실패하며 전체 복사로 조용히 전환하지 않는다.

`--incremental`은 지정한 전용 폴더에서 이전 UTC 날짜의 최신 완료 사본을 선택한다.
이전 사본이 없으면 첫 전체 사본을 만들고, 당일 완료 사본이 있으면 전체 검증 후 재사용한다.
새 사본의 manifest와 결과에는 `storage.mode`, `new_bytes`, `reused_bytes`, 파일 수와
재사용한 백업 ID가 남는다. 바이트는 생성 당시 파일 내용 기준이며 manifest·파일시스템
메타데이터는 제외한다. 저장 공간을 줄여도 원본·백업의 해시와 묶음 검증은 생략하지 않는다.
전체 검증에 필요한 읽기 시간은 데이터 누적량에 따라 증가한다.

명시한 `--backup-dir`은 존재해야 하며, `--backup-mount`는 실제 마운트 지점이어야 한다.
목적지가 해당 마운트 아래에 있는지도 확인한다. 연결이 빠지거나 공간이 부족하면 실패하며
프로젝트의 로컬 경로로 대체하지 않는다. `maintenance-status`도 마지막 daily 설정의 목적지와
마운트·여유 공간을 점검한다. 다른 저장소와 목적지를 공유하지 않으며, 원본 루트를 이사하면
새 전용 폴더로 시작한다. 원격 전송 기능은 없고 OS에 마운트한 파일시스템을 사용한다.

하드링크를 공유한 파일 자체가 손상되면 이를 공유하는 여러 복구 지점이 함께 영향을 받는다.
전체 검증으로 손상은 검출하지만 하드링크가 독립 사본을 대신하지는 않는다. 별도 장치에
추가 독립 사본을 만들 수 있으며, `restore`는 하드링크를 재사용하지 않는다. 백업 트리를
옮길 때 하드링크를 보존하지 않으면 파일 수에 비례해 실제 저장량이 늘어난다.
기존 사본과 실패한 시도는 자동 삭제하지 않고, 불완전한 시도는 재사용하지 않는다.

백업 완료 표시는 마지막에 원자적으로 기록하는 `backup.json`이다. manifest와 각 파일의
SHA-256, SQLite 무결성·외래키, active revision 및 snapshot 참조를 검사한다. 복사된
파일은 디스크에서 다시 읽어 해시를 대조하며, 완료 표시 전에 파일과 디렉터리를 fsync한다.
백업 manifest의 해시는 파일 변경 감지용이며 서명이나 외부 인증 수단은 아니다.

중단·디스크 부족·손상 시 일부 디렉터리가 남을 수 있다. `backup.json`이 없는 디렉터리는
완료된 백업이 아니며 복원 입력으로 사용할 수 없다. 복원 완료 표시는 `restore.json`이다.
실패한 출력 경로에 덮어써서 재시도하지 않고 새 경로를 사용한다.

보관된 백업은 `Store`로 직접 열거나 수집 루트로 사용하지 않는다. `backup-verify`는
카탈로그를 읽기 전용으로 검사하고 기존 작업 디렉터리에 저장소를 새로 생성하지 않는다.
조회·수집 재개는 복원본에서 수행한다. 서비스를 연결하기 전에 최신 상태와 OI 요청 제한을
확인해야 한다. 원래 프로세스의 잠금·타이머·HTTP cooldown 상태는 복원되지 않는다.

작업용 사본 정리와 동시에 실행해도 입력이 사라지지 않도록 `backup --reuse-from`,
`backup-verify`, `restore`는 입력을 사용하는 전체 시간 동안 사본 부모 디렉터리에 공유 잠금을
유지한다. 정리 도구는 같은 디렉터리의 배타 잠금을 비차단으로 요청한다.
[정리 도구의 보호·미리보기·재개 계약](restic-transition-and-retention.md)을 참고한다.

범위는 다음과 같다.

| 포함 | 별도 보존하거나 재생성할 항목 |
|---|---|
| 등록된 모든 원본과 수집 응답 이력 | 카탈로그에 등록되지 않은 고아·임시 파일 |
| 모든 표준 revision과 활성 버전 선택 | 파생 Freqtrade 출력과 백테스트 결과 |
| 등록된 스냅샷 manifest와 참조 데이터 | 보고서, 코드·설정·의존성 환경 |
| 품질 문제·수집 작업·운영 범위가 담긴 카탈로그 | 런타임 잠금·state JSON·이전 백업 |

파생 출력은 고정 스냅샷과 해당 버전의 코드로 다시 만든다. 이미 내보낸 파일 자체와 실행
결과까지 보존해야 한다면 별도 백업이 필요하다. Git에는 코드·설정·검토된 보고서가 들어가며
`backups/`, `restore-checks/`와 실제 시장 데이터는 포함하지 않는다.

프로젝트 안의 `backups/`는 로컬 복구 지점이다. 같은 장비·디스크의 고장에 대비하려면
완성된 백업을 별도 장치나 원격 저장소에도 보존해야 한다. `same_filesystem_as_source`는
파일시스템 식별자만 비교하며, 값이 false여도 물리 장치 분리를 증명하지 않는다.
백업 생성·검증·복원 명령과 매일 UTC 04:10의 자동 검증·백업을 구현했다.
현재 자동 백업 목적지는 두 번째 SSD의 `/mnt/backup/marketdata-historical/automatic`이다.
기존 프로젝트 내부 백업은 그대로 보존한다. 두 번째 SSD는 원본 SSD 고장에 대비한 사본이지만
같은 PC의 사고까지 분리하는 원격·별도 장비 사본은 아직 없다. 예약과 재시도 규칙은
[상시 운영 문서](stream-services.md)에 있다.

운영 확인에는 용량과 수집 상태를 함께 사용한다.

```bash
.venv/bin/marketdata storage
.venv/bin/marketdata status --table
```

`storage`는 디렉터리별 논리 크기와 할당 크기, 파일 수, 파일시스템의 가용 바이트·inode를
표시한다. `unique_allocated_bytes`는 같은 장치·inode를 한 번만 세고,
`hardlink_duplicate_paths`는 중복 계산에서 제외한 경로 수다. 기존 `allocated_bytes`는
경로별 합계라 하드링크 공유분이 여러 번 포함될 수 있다. reflink·파일시스템 스냅샷의 공유량은
판별하지 않는다. 외부 목적지는 이 저장소 순회에 포함되지 않으므로 `maintenance-status`의
`backup_capacity`와 외부 디스크의 `df`도 확인한다.
실행 중인 저장소를 순회한 근사 시점의 값이며, 원자적인 파일 목록이나 데이터
완전성 검사 결과는 아니다. 여유 공간이 설정 기준 미만이거나 가용 inode가 전체의 5% 미만이면
종료 코드 2와 경고를 반환한다. 정상은 0, 실행 오류는 1이다. 외부 알림은 보내지 않는다.

`status`는 별도로 운영 범위의 누락·수집 지연·실패를 확인한다. 과거 결측과 격리는
백업·복원을 통해 사라지지 않으며 그대로 보존된다.

최초 실데이터 검증은 [2026-09-22 백업·복원 보고서](../reports/baseline/backup-2026-09-22/summary.md)에 있다.
증분 저장·두 번째 SSD 전환과 복원 검증은
[2026-09-25 보고서](../reports/baseline/incremental-backup-2026-09-25/summary.md)에 있다.

기존 하드링크 사본을 대상으로 한 restic 비교와 VM 이전을 위한 호스트 설정 분리는
[독립 시험 절차](restic-trial.md)에 있다. 원래 manifest와 source-root 검사를 유지하고,
다른 경로로 복원한 저장소의 첫 백업은 새 전용 폴더에 만든다. 이 시험으로 운영 예약이
restic으로 바뀌는 것은 아니다.
