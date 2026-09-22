# 백업·복원과 저장 용량 점검

`marketdata backup`은 카탈로그에 등록된 원본, 모든 표준 revision, 스냅샷 manifest와
일관된 SQLite 사본을 새 디렉터리에 보존한다. 비활성 revision, 격리 원본, 반복 HTTP
응답 기록, 품질 문제와 운영 이력도 포함한다. 실행 중인 수집기를 정지할 필요는 없다.

카탈로그를 복사할 때만 기존 writer 잠금을 잡고 SQLite backup API를 사용한다. 잠금을
반납한 다음 복사된 카탈로그의 목록으로 불변 파일을 복사하므로, 이후 도착한 데이터가
중간에 섞이지 않는다. 카탈로그 기준 시각 구간과 실제 잠금 유지 시간은 manifest에 남긴다.
이 방식은 원본·revision·스냅샷을 수정하거나 삭제하지 않는 현재 저장 정책을 전제로 한다.
[Python SQLite backup API](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup),
[SQLite 온라인 백업 설명](https://www.sqlite.org/backup.html)

기본 여유 공간 기준은 10 GiB이며 `--min-free-gib`로 바꿀 수 있다. 복사할 파일 크기와
최소 여유 공간을 합한 용량이 없으면 완료 처리하지 않는다. 자동 삭제·보관 기간 정책은 없다.

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

백업과 복원은 기존 디렉터리를 거부하고 파일을 독립적으로 복사한다. 원본과 하드링크를
공유하지 않는다. `data/`와 `state/` 내부에 백업을 만들 수 없으며, 복원 위치는 백업 밖의
새 경로여야 한다. 복원한 저장소의 수집 서비스는 자동으로 시작하지 않는다.

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
현재 백업 생성·검증·복원 명령을 구현했고, 정기 예약과 외부 백업 전송은 설정하지 않았다.

운영 확인에는 용량과 수집 상태를 함께 사용한다.

```bash
.venv/bin/marketdata storage
.venv/bin/marketdata status --table
```

`storage`는 디렉터리별 논리 크기와 할당 크기, 파일 수, 파일시스템의 가용 바이트·inode를
표시한다. 실행 중인 저장소를 순회한 근사 시점의 값이며, 원자적인 파일 목록이나 데이터
완전성 검사 결과는 아니다. 여유 공간이 설정 기준 미만이거나 가용 inode가 전체의 5% 미만이면
종료 코드 2와 경고를 반환한다. 정상은 0, 실행 오류는 1이다. 외부 알림은 보내지 않는다.

`status`는 별도로 운영 범위의 누락·수집 지연·실패를 확인한다. 과거 결측과 격리는
백업·복원을 통해 사라지지 않으며 그대로 보존된다.

최초 실데이터 검증은 [2026-09-22 백업·복원 보고서](../reports/baseline/backup-2026-09-22/summary.md)에 있다.
