# T13 보완 검증 범위

최종 `t13-daily-scheduler-20261011-703fb0d455523c1b`:
**보완 35개 + 기존 scheduler 47개 + 관련 기존 회귀 131개 = 213개 통과, 실패/skip 0**.
[검사 원본](tests.xml), [깨끗한 경로 재현 검사](reproduced-tests.xml).
재현 실행은 같은 213개이며 검사 수를 더하지 않는다.

## 새로 확인한 경계

- 기존 CLI로 채운 needs_review acquisition을 HTTP 없이 완료 연결하고 이전 시도 기록을 유지한다.
- 과거 성공만으로 refresh를 완료시키지 않으며, 같은 원문을 새 주기에 재확인·정상 저장한 근거는 인정한다.
- ZIP/CHECKSUM 다운로드만 끝나고 publication이 중단되면 refresh 완료를 거부한다.
- funding의 전체 페이지·종료 빈 페이지 및 manifest, metadata 실제 관측과 과거 재시도 거부를 확인한다.
- 추가 1–3회 제한, 잘못된 요청, 같은 ID의 다른 내용, 변경된 상태 해시/설정 범위를 거부한다.
- 반복 요청은 기존 receipt를 반환하고 사용한 예산을 복원하지 않는다. 실패 횟수와 attempt는 누적 유지한다.
- 냉각/strict 준비 보류 시 HTTP와 추가 예산 소모가 없고, 냉각 만료 후 정상 저장한다.
- 충돌 자료를 승인하지 않고 기존 active revision을 유지한다. 다른 acquire의 보류를 우회하지 않는다.
- transaction 중간 예외·receipt INSERT 실패·claim 실패에서 부분 예산/상태를 남기지 않는다.
- 실제 별도 프로세스의 SIGKILL을 commit 전/후에 주입하고, 다시 실행해 한 요청·한 예산만 존재함을 확인한다.
- 실제 CLI 두 개의 동시 실행과 scheduler 잠금 경합, inspect 조회·기존 결과 반환을 확인한다.
- 추가 시도로 publication 직후 중단하면 무요청 복구한다. 다운로드만 하고 중단하면 소모한 예산을 되돌리지 않는다.
- 기존 backup/verify/restore 후 추가 처리 기록과 예산·반복 요청 의미가 유지된다.
- WAL 사본의 일관성, 사본 획득 뒤 원본 collector의 추가 commit, 파일 배치 보호·경합을 구분한다.
- 미리보기는 원본에 daily 테이블/새 잠금을 만들지 않는다. 기존 잠금이 없으면 실패한다.
- packed/loose 읽기와 pack 손상 거부, 사본 계획의 기존 review/cooldown 보존을 확인한다.

HTTP와 시각 입력은 격리 대역이다. 운영에서 실패 상태를 만들거나 원격 재다운로드로 오류를 주입하지 않았다.
실제 운영 자료를 대상으로 한 것은 읽기 전용 사본 미리보기이며, 개별 작업의 운영 재개는 0건이다.

## 재현

```bash
python3 -B -I reports/baseline/t13-daily-review-2026-10-10/prepare_reproduction.py --destination /tmp/t13-daily-new-check
cd /tmp/t13-daily-new-check
uv sync --frozen --no-editable --link-mode=copy
.venv/bin/python -B -I -m pytest tests/test_daily_review.py tests/test_daily_schedule.py tests/test_operations.py tests/test_ingestion.py tests/test_transport.py tests/test_backup.py tests/test_incremental_backup.py tests/test_maintenance.py tests/test_maintenance_wait.py tests/test_packing.py tests/test_backfill.py tests/test_recovery.py -q
.venv/bin/ruff check packages/marketdata/src/marketdata/daily_schedule.py packages/marketdata/src/marketdata/daily_review.py packages/marketdata/src/marketdata/daily_preview.py packages/marketdata/src/marketdata/daily_probe.py tests/test_daily_review.py tests/test_daily_schedule.py
```

최종 재현 경로는 `/tmp/t13-daily-review-sealed-20261011`이다. baseline 139개 파일을 복원하고 10개 추가 파일의
패치를 적용해 총 149개 해시를 확인했다. 기존 공통 Collector·Store·backup 등은 baseline과 바이트가 같다.
유닛·설정은 기존 후보와 바이트가 같아 앞선 구문 검사 근거를 재사용한다. 실제 /opt 설치·서비스 기동 결과로 확대하지 않는다.

## 미리보기 격리 검증

SQLite [Online Backup API](https://www.sqlite.org/backup.html)를 사용하며, 원본의 WAL을 무시하는
`immutable=1`이나 본체 파일만의 복사는 사용하지 않는다. SQLite [temp_store](https://www.sqlite.org/pragma.html#pragma_temp_store)
연결 설정으로 정렬 임시 파일을 메모리에 유지한다. 실제 운영 사본에서는 /var/tmp 생성이 사라졌음을 strace로 확인했다.

strace의 `%file,%network`는 파일 접근·전송 호출을 확인한 범위다. 모든 메모리 변경/시스템 호출을 추적한 것은 아니다.
WAL/SHM의 읽기 조정용 열기, 라이브 수집기의 정상 DB 변경과 미리보기의 업무 데이터 쓰기를 구분한다.
`mode=ro`·`query_only=ON`, 원본 업무 테이블의 미생성, 쓰기 경로 및 테스트에서의 원본 상태 보존을 함께 근거로 사용했다.

최초 sandbox는 ptrace를 허용하지 않아 실행이 시작되지 않았고, 별도 허용된 읽기 관찰로 실행했다.
root 설치나 보호 옵션 약화로 통과시킨 시험은 아니다. 개발 중 기본/환경변수 임시 경로 실험은 실패 근거로 보존하고,
최종 연결별 MEMORY 설정의 결과와 구분한다.
