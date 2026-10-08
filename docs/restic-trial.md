# restic 시험과 호스트 설정 분리

이 시험은 **완료된 marketdata 사본 두 개**를 restic에 보관하고 복원하는 독립 도구다.
운영 백업을 restic으로 교체하거나 예약 작업을 추가하지 않는다. 현재 비교 대상은 매번
전체 복사하는 방식이 아니라 `/mnt/backup`의 **하드링크 증분 백업**이다.
실행 결과는 [9/27 시험 기록](../reports/baseline/restic-trial-2026-09-27/summary.md)에 남긴다.
시험 다음 단계의 예약·실패 재개·상태 판정은 [운영 연결 설계](restic-operations-plan.md)에
정리했다. 후속 [병행 보관 도구](restic-archive.md)는 별도 구현이며,
9/27 [예약 운영](restic-scheduled-operations.md)을 적용했다. 날짜별 관찰은 최초 시험 결과와 구분한다.

10/5에는 사용이 끝난 pilot 저장소·작업 공간·시험 프로필의
[정리 계획](../reports/baseline/trial-artifact-retention-expanded-2026-10-05/summary.md)을 준비했다.
아직 삭제·프로필 해제 전이며, 아래 9/27 명령은 당시의 실행 기록이다. 새 시험은 보존된 입력과
새 프로필·출력 경로를 사용한다. 이미 정리된 원래 9/27 checkpoint 경로를 그대로 재사용하지 않는다.

## 설정과 실행 환경

- [호스트 설정 예시](../configs/backup-host.example.toml)는 Git에 보관한다.
- 실제 설정은 `~/.config/marketdata/backup-host.toml`, 암호는 별도의 `0600` 파일이다.
  설정에는 암호 값이 아닌 암호 파일 경로만 넣는다. 설정과 암호를 Git에 넣지 않는다.
- 원본, 기존 사본, restic 저장소, 복원 작업 공간을 분리한다. 원본 경로는 `data/`와
  `state/`를 포함한 저장소 루트다. 보고서에는 경로가 기록되지만 암호는 기록되지 않는다.
- 필수 마운트가 없거나 출력 경로가 입력과 겹치면 중단한다. 다른 디스크의 경로로
  자동 대체하지 않는다. 파일시스템 마운트 검사는 물리 장치의 신원 검사와는 다르다.
- restic은 시스템 패키지를 바꾸지 않고 `.cache/restic/0.19.1/restic`에 설치했다.
  [설치 스크립트](../scripts/install_restic.py)는 Linux x86_64 공식 릴리스의 고정 SHA-256과
  공식 `SHA256SUMS`를 확인한다. GPG 서명 검증을 수행했다는 의미는 아니다.

실제 프로필은 이 시험에서만 사용한다. 기존 systemd 서비스의 경로를 읽어 자동으로
변경하지 않는다. 향후 VM 이전 때는 VM에서 보이는 경로로 새 프로필을 만들고, 코드와
의존성·서비스 설치는 별도로 진행한다. 이전 시험의 프로필은 결과 재현을 위해 보존한다.

restic 암호를 잃으면 저장소를 복호화할 수 없다. VM 이전이나 운영 전환 전에 암호도
별도 안전한 장소에 보존하고 복구를 확인한다. 현재 시험은 같은 PC의 두 번째 SSD를
사용하므로 별도 장비·원격 백업이 추가된 것은 아니다.
[공식 저장소·암호 설명](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html)

## 입력과 재현

[입력 명세](../configs/restic-trial-2026-09-27.toml)는 기존 백업 폴더에 대한 상대 경로와
두 사본의 `backup_id`를 고정한다. 실행 시 manifest의 해시·목록·카탈로그와 `source_root`를
검사한다. 이후 단계는 최초 입력·프로필·restic 바이너리가 바뀌면 거부한다.
기존 `backup.json`과 원본 경로 검사는 수정하지 않는다.

```bash
# 각 단계가 성공한 뒤 다음 단계로 진행한다.
.venv/bin/python scripts/restic_trial.py \
  --profile /home/yoonhc/.config/marketdata/backup-host.toml \
  --inputs configs/restic-trial-2026-09-27.toml prepare
```

마지막 인자만 순서대로 `backup-first`, `backup-second`, `check`, `recover-first`,
`recover-second`로 바꾼다. `prepare`는 저장소·작업·보고서 경로가 모두 새 경로일 때만
실행한다. 같은 단계의 출력이 이미 있으면 덮어쓰지 않는다. 실패 결과를 먼저 확인하고,
재시험은 새 프로필과 새 경로에서 진행한다. 자동 삭제나 `forget`·`prune`은 없다.
기본 재현 순서는 위와 같다. `check` 이후 날짜별 복원은 출력 경로가 서로 독립적이다.
시간 비교에는 실제 로그의 시작·종료 시각을 사용한다.

명령은 `nice +10`, `ionice` best-effort 7, restic의 Go 실행 스레드와 읽기 동시성 2로
실행한다. 이는 CPU·메모리·디스크 사용량의 강제 상한이 아니다. 단계별 restic 로그와
종료 코드·벽시계 시간·CPU 시간·최대 RSS·I/O 블록 수를 보존한다. 두 번째 사본은 첫 번째
snapshot ID를 명시적으로 parent로 지정한다. restic 기본 압축 `auto`를 사용한다.
[공식 백업·parent·중복 제거 설명](https://restic.readthedocs.io/en/stable/040_backup.html)

## 복원 검증

두 날짜 각각 다음 절차를 수행한다.

1. 완료된 백업을 restic에 저장하고 **64자리 snapshot ID**를 기록한다.
2. `restic check --read-data`로 저장소 구조와 모든 데이터 pack을 읽어 검사한다.
3. 날짜별 명시적 snapshot ID를 **새 추출 경로**로 `restore --verify`한다.
4. 추출된 `backup.json`의 바이트·SHA-256·backup ID·source root가 원본과 같은지 확인한다.
   marketdata `backup-verify`와 같은 함수로 전체 파일·카탈로그·묶음 구성원을 검사한다.
5. marketdata `restore`와 같은 함수로 **또 다른 새 경로**에 독립 저장소를 복원한다.
6. 백업 시점의 입력과 복원본을 조회 대조한다. 모든 연구 snapshot manifest와 각 key의
   첫 최대 24시간, 처음·마지막 이벤트 세그먼트, 원문 표본, 최근 10분 aggTrade,
   종료된 호가 세션 한 개의 재생을 비교한다. 전체 파일 무결성도 별도로 검사한다.
7. 복원본에서 새 전용 시험 폴더에 **첫 독립 백업**을 만들고 다시 전체 검증한다.
   새 manifest에는 새 저장소 경로와 새 backup ID가 자연스럽게 기록되어야 한다.
8. 원본 사본·추출본·복원본·새 사본의 파일 inode가 서로 독립적인지, 기존 manifest가
   그대로인지 확인한다. 복원본의 수집 서비스는 시작하지 않는다.

[restic 전체 데이터 검사](https://restic.readthedocs.io/en/stable/045_working_with_repos.html#checking-integrity-and-consistency),
[restic 복원](https://restic.readthedocs.io/en/stable/050_restore.html)

보관된 사본은 `Store`로 열지 않는다. 시험의 `ArchiveReader`는 닫힌 카탈로그를
`mode=ro&immutable=1`로 읽고, 배치 잠금 파일도 생성하지 않는다. 운영 수집 상태는
WAL을 반영하는 별도 `mode=ro` 연결로 읽고 즉시 닫는다. 각 실행 단계 중 약 10초 간격으로
aggTrade·호가 수신 시각과 오류 카운터를 기록한다. 단계 사이와 입력 사전 검사 시간은
이 샘플링에 포함되지 않는다. 표본 관측만으로 무중단·무손실을 보장하지 않는다.

이전 경로에 속한 자동 백업 폴더의 재사용 거부는 격리 테스트에서 당일 사본과 전날 사본
양쪽으로 검사한다. 운영 폴더에 잘못된 쓰기를 시도해 확인하지 않는다. 복원 후 새 전용
자동 백업 폴더를 사용하면 기존 source-root 검사 그대로 정상 동작한다.

## 공정한 용량·시간 비교

하드링크 사본은 `(device, inode)`별 `st_blocks * 512`를 한 번만 합산한다. 디렉터리와
`backup.json`도 포함한다. 첫 사본, 두 사본의 합집합, 두 번째 사본으로 늘어난 양을 각각
측정한다. restic은 동일한 두 복구 지점을 보관한 저장소의 실제 할당량을 측정한다.
이는 ext4 하드링크 환경의 계산이며 reflink·압축 파일시스템의 공유량 측정법은 아니다.

restic 저장소 크기, 로컬 cache, 추출본·복원본·재백업 작업 공간, 보고서 크기는 따로
기록한다. 기존 백업을 그대로 보존하므로 **시험 중 디스크 전체 사용량은 증가**한다.
두 날짜에서의 절감률을 장기간 증가율로 단정하지 않는다.

restic 백업 시간은 **이미 완료된 checkpoint를 읽어서 restic에 넣는 시간**이다. 현재
daily 작업의 전체 무결성 검사·checkpoint 생성 시간과 직접 비교해 속도 개선이라
주장할 수 없다. 운영 교체 검토에는 일관된 checkpoint 준비·검증 비용을 포함해야 한다.
이 시험은 실행 중인 SQLite와 데이터 폴더를 restic으로 바로 복사하는 설계를 검증하지 않는다.

검사 통과는 파일 보존과 지정한 조회의 일치를 뜻한다. 기존 OI 격리, 과거 호가 실패·공백,
수집 중이던 세션 상태는 백업 당시 상태로 보존되며 복원 과정에서 정상으로 바꾸지 않는다.

```bash
# 마지막 검사는 restic 0.19.1이 설치된 환경에서 실제 외부 도구까지 실행한다.
.venv/bin/pytest -q tests/test_restic_trial.py tests/test_backup.py tests/test_incremental_backup.py
```

restic 미설치 환경에서는 해당 외부 도구 통합 검사 한 개만 건너뛴다. 이 시험 환경에서는
실제로 실행했고, 두 복구 지점의 전체 절차와 원래 경로를 옮긴 뒤 복원도 확인한다.

## VM 이전 때의 복원 경로

시험 스크립트는 살아 있는 원본 수집기를 관측하고 원본과 결과를 대조하는 도구이므로
`store_root`와 입력 사본의 출처가 일치하는 현재 호스트를 전제로 한다. 원본 호스트가
없는 장애 복구에서는 이 시험 스크립트 대신 restic과 marketdata의 복원 명령을 사용한다.
그 명령들은 예전 `source_root` 경로가 실제로 존재할 것을 요구하지 않는다.

1. 새 VM에 코드·의존성·restic, 백업 디스크 마운트와 암호 파일을 준비한다.
2. restic 저장소에서 기록된 snapshot ID를 새 추출 경로로 복원한다.
3. 추출한 사본을 `marketdata backup-verify`로 검증하고 `marketdata restore --output`으로
   새 저장소에 복원한다. `backup.json`을 수정하지 않는다.
4. 새 저장소의 무결성과 조회를 확인한 뒤 새 프로필의 `store_root`를 그 경로로 지정한다.
   `backup_dir`도 이전 출처 사본이 들어 있지 않은 새 전용 폴더를 사용한다.
5. 그 새 저장소에서 첫 독립 백업을 만들고 검증한다. 수집 서비스·타이머 경로와
   마운트 의존성은 별도로 설치·확인한 다음 운영을 넘긴다.

이번 시험은 같은 Linux 호스트 안에서 경로 이전과 원본 경로 부재를 검증한다.
실제 Proxmox VM의 마운트·부팅·서비스 재개 시험은 해당 환경을 만든 뒤 진행한다.
