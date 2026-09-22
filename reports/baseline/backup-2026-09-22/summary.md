# 실시간 수집 확장 전 백업·복원 검증 — 2026-09-22

등록된 원본·모든 표준 revision·스냅샷·카탈로그의 일관된 로컬 백업을 만들고, 별도 경로에
복원하여 검증했다. 이 단계는 aggTrade·depth 구현에 앞선 운영 보완이다. 두 수집기 자체는
아직 구현하지 않았다. 기존 수집기는 중지하지 않았다.

- 전체 테스트 **111개 통과**. 백업·복원·용량 관련 16개를 추가했다.
- Ruff 검사·형식 검사 및 diff 검사 통과.
- 백업 파일 **10,487개 / 865,586,632바이트**: 원본 3,557개, 표준 revision 6,909개,
  스냅샷 20개, 카탈로그 1개. 모든 파일의 해시와 카탈로그 참조를 검사했다.
- SQLite checkpoint는 UTC **13:07:50.143886~13:07:50.213930**에 만들었다.
  writer 잠금 유지 시간은 **0.070049초**, 전체 백업 완료 시각은 **13:08:00.652273 UTC**다.
- 복원 완료 시각은 **13:09:34.668594 UTC**다. 복원본의 원본·표준 파일 10,466개를
  기존 `validate`로 검사해 실패가 없었다. 스냅샷·카탈로그는 백업 검증에서 별도로 검사했다.
- 스냅샷 **20개**, 데이터셋별 **107개 구간**에서 모든 열의 값과 시각을 원본과 대조했다.
  각 스냅샷의 각 데이터셋을 처음 최대 24시간씩 조회한 검사이며, 전체 파일 해시 검증과 구분한다.
- 수집 운영 상태는 전후 모두 9개 항목 `healthy`였다. REST OI 최신 라벨은 13:05에서
  13:10 UTC로 갱신되었다. 이 결과가 알려진 과거 결측·격리의 해소를 뜻하지 않는다.
- 백업·복원 이후 **13:12:21 UTC**의 디스크 여유 공간은 약 **228.84 GiB**였다.

백업 ID:

`4c84f3105897629157f2982edbd8155c3065d9f13f8e0e993b00ef250c5eb086`

로컬 경로는 프로젝트 루트 기준 다음과 같다.

- 백업: `backups/pre-streaming-2026-09-22/`
- 복원 검증본: `restore-checks/pre-streaming-2026-09-22/`

원본과 독립된 파일 복사본이며 하드링크를 사용하지 않았다. 백업은 같은 파일시스템에
있으므로 장치 고장에 대비한 외부 백업을 완료한 것은 아니다. 정기 백업 예약·외부 전송·
외부 알림은 설정하지 않았다. 파생 Freqtrade 출력, 결과 ZIP, 보고서, 코드·환경, 런타임
state JSON 및 미등록 고아 파일은 백업 범위에서 제외했다. 정확한 범위와 절차는
[백업·복원 문서](../../../docs/backup-and-storage.md)에 있다.

## 보존한 근거

- [backup.json](backup.json): 백업 ID·경로·파일 수·크기·카탈로그 행 수·잠금 시간.
- [backup-verify.json](backup-verify.json): 완성된 백업의 독립 검증.
- [restore.json](restore.json), [restored-integrity.json](restored-integrity.json): 복원 결과와 파일 검사.
- [snapshot-reads.json](snapshot-reads.json): 스냅샷별 조회 범위·행 수·행 내용 해시·일치 여부.
- [status-before.json](status-before.json), [status-after.json](status-after.json): 운영 구간 상태.
- [storage-before.json](storage-before.json), [storage-after.json](storage-after.json): 저장 용량 실측.
- [validation.json](validation.json), [tests.xml](tests.xml): 검사 결과, 실행 명령, 소스·manifest 해시.

이 디렉터리의 `backup.json`은 결과 요약이다. 전체 파일 목록을 담은 백업 manifest는 실제
백업 경로의 `backup.json`에 있으며, ID와 manifest 파일 해시로 식별한다. Git의 검증 기록은
백업 파일 자체를 대신하지 않는다.
