# 두 번째 SSD의 복구 키트 사용 메모

이 파일은 암호 값을 포함하지 않는다. 복구 키트·암호와 백업이 같은 SSD에 있으며,
다른 PC나 USB의 암호 사본은 아직 준비하지 않았다. 키트 전체를 공개 저장소로 옮기지 않는다.

| 항목 | 현재 호스트 경로 |
|---|---|
| 키트 | `/mnt/backup/marketdata-recovery-kit-20261003` |
| 복구 암호 | 키트의 `secrets/restic-password` (0600, 상위 0700) |
| 운영 restic | `/mnt/backup/marketdata-restic` |
| 이번 시험의 독립 복제 저장소 | `/mnt/backup/marketdata-independent-recovery-20261003/repository` |
| 원래 source root 문자열 | `/home/yoonhc/projects/algoTrade/historical_data` |
| 원래 checkpoint 전용 폴더 문자열 | `/mnt/backup/marketdata-historical/automatic` |
| scope | `historical-data-original-v1` |
| 파일시스템 UUID | `d90aaf81-2358-411f-8022-4e1a3f7b0973` |
| restic 저장소 ID | `b5a4434c95fcf4a9eb1339689cdea42c1da08bd34b845193dffce2aa8203e558` |
| 시험한 전체 snapshot ID | `d27a0a557e1795a4651132016491f1b2c6735e8181b71303f3adccd414f0f8ad` |
| 원래 backup ID | `e971e8b4b008fd5144f0ee6e8125ace882d2bdf1c6bf9f1fba23f0f5a1ac2e30` |

운영 저장소는 예약 작업으로 갱신된다. 시험 복제 저장소는 10/3에 복사한 고정 사본이며
그 이후의 daily가 자동 추가되는 백업 목적지가 아니다. 이번 복제·추출·복원·재백업 폴더는 보존한다.

## 포함물과 실행 환경

- `code/`: Git 커밋 `86561bee2b0a142db21221f786e2e6380af07f72`의 코드·설계·잠금 파일.
- `site-packages/`: 시험 당시 Python 3.12 패키지 복사본. `restic`: 고정 버전 0.19.1 실행 파일.
- `kit.json`: 커밋·코드 해시·저장소 및 snapshot 신원. 암호 값·암호 해시를 저장하지 않음.
- `profile.toml`: **이번 격리 시험 내부 경로용** 프로필. 다른 호스트에서 그대로 실행하면 안 됨.
- `isolation_entry.py`: 원본 경로 부재·빈 상태/캐시를 확인한 뒤 기존 복구 절차를 실행한 진입점.
- `verification/`: 완료 후 복사하는 시험 근거·조사 결과·검증 스크립트.
- `retention-policy.md`: 검토한 보관 정책. 삭제를 실행하는 설정 파일이 아님.

시험 환경은 Linux x86_64, Python 3.12.3, restic 0.19.1이다. 인터프리터와 OS 전체를
키트에 넣은 것은 아니다. 새 Linux에서는 호환되는 Python 3.12·시스템 라이브러리가 필요하며,
의존성 재설치가 필요하면 `code/`의 잠금 파일을 기준으로 준비한다. 복사한 `site-packages`의
editable `.pth`에 의존하지 않고 `python3.12 -S`와 명시적 `PYTHONPATH`로 코드를 선택한다.

## 원본 호스트 없이 복구하는 순서

1. 대상 디스크를 마운트하고 실제 UUID와 저장소 ID를 확인한다. 암호 파일은 사용자 소유 0600으로
   보호한다. 암호 값을 명령 인자에 넣거나 로그로 출력하지 않는다. 원본 저장소가 없다고 새 restic
   저장소를 초기화하거나, UUID·ID 검사를 생략하지 않는다.
2. 키트의 프로필을 **새 작업 프로필**로 복사한다. `store_root`, `backup_dir`, `archive_scope`는
   위 옛 출처의 문자열을 유지한다. 이때 원래 폴더가 실제로 존재할 필요는 없다.
3. 나머지 경로는 새 호스트에 맞춘다: `backup_mount`·실제 파일시스템 UUID, `restic_repository`·
   실제 저장소 ID, `restic_binary`, `restic_password_file`, 새 전용 `state_root`·`reports_root`·
   `cache_root`·`restore_work_root`. 입력 저장소와 출력 경로가 겹치지 않게 하고 충분한 공간을 준비한다.
   파일시스템을 새로 만들었다면 실제 새 UUID를 명시적으로 등록한다. 암호·출력·상태는 백업 입력에 넣지 않는다.
4. `code/`에서 기존 `scripts.archive_backups`의 `restore-check`에 위 전체 snapshot ID를 지정한다.
   `latest`를 사용하지 않는다. 실제 경로를 채운 명령 형식은 다음과 같다.

   ```bash
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/ABS/KIT/code:/ABS/KIT/code/packages/marketdata/src:/ABS/KIT/site-packages \
     python3.12 -S -m scripts.archive_backups \
     --profile /ABS/recovery-host.toml restore-check \
     --snapshot-id d27a0a557e1795a4651132016491f1b2c6735e8181b71303f3adccd414f0f8ad
   ```

5. 추출본 검증 → 새 root 복원 → 조회·전체 무결성 → 새 독립 백업 검증을 끝까지 확인한다.
   원래 `backup.json`·`source_root`를 수정하지 않는다. 새 root의 새 백업은 프로그램이 새 신원으로 만든다.
   실패한 결과는 보존하고 새 출력 경로로 재시도한다. 이 명령은 수집 서비스를 켜지 않는다.
6. 새 호스트에서 실제 수집을 시작할 때는 별도의 운영 프로필을 만든다. 새 `store_root`·전용
   `backup_dir`·새 `archive_scope`를 사용한다. 같은 실제 restic 저장소를 동시에 쓰는 운영 작업은
   저장소별 잠금을 공유해야 한다. 마운트·부팅·사용자 서비스 설정과 예약 재개는 따로 검증한다.

## 이번 시험 경로 해석

격리 환경의 `/mnt/backup`은 호스트의
`/mnt/backup/marketdata-independent-recovery-20261003`만 보이도록 연결했다.
따라서 결과 JSON의 `/mnt/backup/restore-work/...`는 호스트에서 그 시험 폴더 아래
`restore-work/...`에 해당한다. 원래 `source_root`는 그대로 보존했고, 새 백업의 source root는
프로그램을 실행한 격리 환경의 실제 경로다. 이 경로 문자열을 나중에 임의로 고치지 않는다.

이번 성공은 현재 호스트에서 원본 파일·운영 설정·기존 상태·캐시 없이 복구한 범위에 한한다.
PC 외부 암호 보관, 별도 장비의 실제 복구, Proxmox VM 부팅·서비스 재개는 후속이다.
