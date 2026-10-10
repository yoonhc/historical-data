# T13 후보 검증 근거

최종 대상: `t13-daily-scheduler-20261010-89912177f7b3adad`.
**최종 178개 = 신규 47개 + 기존 관련 131개. 실패/skip 0, 10.54초.**
[JUnit 원본](tests.xml), [최종 설치·import 근거](reproduction.json),
[테스트 코드](candidate/tests/test_daily_schedule.py)를 함께 본다.

## 시험 범위

| 경계 | 실제 시험 |
|---|---|
| 일정·대상 선택 | 정오/저녁 분리, due 아닌 대상·당일 완료 대상 요청 생략, 놓친 20/22시 슬롯의 중복 재생 방지 |
| 이월·수정 확인 | 6일 이상 미공개 같은 ID 유지, 오래된 미완료 낮은 빈도 재확인, 최근 3일 refresh 주기 분리 |
| 중복 작업 | acquisition과 refresh의 같은 성공 근거 연결, 다음 날 새 refresh가 기존 재시도 시각·한도를 우회하지 않음 |
| 통신·제한 | 4 HTTP 요청 × 3 작업 시도 제한, 429 host 냉각/재시작 유지, Retry-After 누락·잘못된 값·HTTP-date, 영구 4xx 구분 |
| 검증·저장 | checksum 부재/불일치 구분, 같은 실패 입력과 서로 다른 수신 시각 보존, short grid 불완전 판정, conflict에서 기존 active revision 보존 |
| 완료의 근거 | catalog 참조 유실 시 needs_review, 중단 복구에서 실제 Parquet 손상 검출, 기존 완료 자료는 예정된 수정 확인 때만 다시 요청 |
| 중단 | publish 직후·완료 상태 직전 중단, 다운로드만 완료한 중단, 실제 별도 Python 프로세스 SIGKILL 후 무요청/무중복 복구 |
| 상태 transaction | 실패 상태 기록 transaction을 SQLite trigger로 중단한 뒤 작업/시도 상태가 함께 남고 복구에서 재시도 횟수가 소실되지 않음 |
| metadata·funding·OI | metadata 실제 관측 시각 및 놓친 날 구분, funding 원문 manifest/종료 페이지, 기존 OI 시간 격자·품질 의미 유지 |
| 잠금·시각 | 두 runner 동시 진입 거부, 준비 대역 거부 시 HTTP/시도 증가 없음. 실제 운영 gate 변경은 하지 않음 |
| 백업·원복 호환 | pending-only와 populated store를 기존 backup/verify/restore, 복원 후 동일 주기 무요청 재개, 이전 Collector가 새 원장을 가진 저장소에 기록 가능 |
| 기존 상태 조회 | 새 per-key 집계로 old status가 partial→complete 확인, 이전 행 보존, 처리 0건에서 성공 시각/행 수 불필요 갱신 없음 |
| 공개 관측 | HEAD만 전송, ZIP/CHECKSUM 따로 404→200, HTTP 오류를 404로 오인하지 않음, 창 종료·기간/간격 제한·비활성 시 요청 0 |

기존 회귀 대상은 operations, ingestion, transport, backup, incremental_backup, maintenance,
maintenance_wait, packing, backfill, recovery다. Collector·Store·backup 등 기존 모듈은
기준 파일과 바이트가 같으며, 새 모듈이 해당 모듈을 실제 호출했다.

HTTP 응답·시각은 대역이다. 운영 Binance 자료를 새로 다운로드하거나 시계를 움직이지 않았다.
종료 시험에는 실제 SIGKILL을 사용했으나 운영 서비스가 아닌 임시 시험 프로세스다.
실제 timer 기동/서비스 배포·최종 `/opt` 권한 시험, 실시간 공개 시각 관측, 운영 restic 편입 시험은 하지 않았다.

## 깨끗한 배포본 재현

기준 139개 파일을 고정한 source bundle에서 복원한 다음, 추가 7개 파일의 패치를 적용했다.
최종 146개 파일 해시를 대조하고 lock 기반 비-editable 설치 후 `python -I`로 시험했다.
새 모듈 두 개 모두 `.venv/lib/python3.12/site-packages/marketdata/`에서 import됐고
설치 파일 해시가 manifest와 일치했다. 현재 개발 workspace의 패키지를 import한 결과가 아니다.

재현 예시(목적지 디렉터리는 새 경로로 지정):

```bash
python3 -B -I reports/baseline/t13-daily-scheduler-2026-10-10/prepare_reproduction.py --destination /tmp/t13-daily-recheck
cd /tmp/t13-daily-recheck
uv sync --frozen --no-editable --link-mode=copy
.venv/bin/python -B -I -m pytest tests/test_daily_schedule.py tests/test_operations.py tests/test_ingestion.py tests/test_transport.py tests/test_backup.py tests/test_incremental_backup.py tests/test_maintenance.py tests/test_maintenance_wait.py tests/test_packing.py tests/test_backfill.py tests/test_recovery.py -q
.venv/bin/ruff check packages/marketdata/src/marketdata/daily_schedule.py packages/marketdata/src/marketdata/daily_probe.py tests/test_daily_schedule.py
```

유닛 구문 검사는 후보 Python 실행 경로만 `/tmp`로 치환하고 기존 이름의 임시 파일을
`systemd-analyze --user verify`에 전달했다. 최초 sandbox에서 소켓 제한 메시지가 나와
샌드박스 밖의 읽기 전용 검사로 다시 확인했고 return code 0, stderr 없음이었다.
[최종 결과](unit-verification-final.json), [최초 제한 기록](unit-verification.json).
타이머의 `*-*-* *:00/5:00 UTC`는 systemd calendar parser로 확인했다.
이는 운영 사용자 manager에 유닛을 설치·시작한 시험이 아니다.

## 개발 중 결과와 구분

개발 중 SQLite 점검의 응답 `received_at` 비교와 Store context-manager 사용에 테스트 작성 오류가 있어
시험을 고쳤다. 전자는 같은 본문을 나중에 다시 받은 관측 시각이 달라지는 정상 동작을 반영했고,
후자는 기존 Store의 명시적 close 방식으로 수정했다. 운영 코드 결함이나 운영 장애로 보고하지 않는다.

기존 status 연동을 추가하기 전 후보 `1c3026ebb3f0fcf1`의 manifest·177개 검사·재현 기록은
`pre-status-bridge-*`로 구분한다. 더 앞선 175개 실행은 `development-tests.xml`이다.
이는 개발 이력이며 최종 178개에 합산하지 않는다. 적용 대상은 최종 `89912177f7b3adad`다.

최종 회귀 이후 source bundle/candidate 사본/설치 코드/운영 baseline 해시를 다시 대조한다.
원문 형식·Book 계산·시각 정책·기존 backup/restore 검증을 바꿔 시험을 통과시키지 않았다.
