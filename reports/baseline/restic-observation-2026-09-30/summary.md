# 2026-09-30 restic 예약 관찰 2/3

20:49 KST까지의 실제 영수증·systemd journal을 대조했다. 오늘 새 daily 사본은
**14:05 예약에서 저장·전체 검사에 성공**했고, 서로 다른 날짜의 예약 성공은 **2/3**이다.
복원 검증은 9/28의 통과 결과를 유지한다. 남은 날짜 1개가 있어 전체 관찰은 아직 미완료다.

## 백업과 restic 실제 결과

| 항목 | 실제 기록 — 9/30 KST |
|---|---|
| daily 백업 | 13:10:00–13:36:02, 정상 종료, 약 26분 2초 |
| 저장소 무결성 | **607,815개 논리 검사 항목, 실패 0** |
| 무결성 단계 시간 | 약 22분 6초, journal의 validate→backup 경계 |
| checkpoint 기준 시각 | 13:33:35.779067 |
| 완료 사본 | 38,161개 파일, 논리 크기 17,326,656,464 bytes |
| 하드링크 사본 생성 시 | 기존 파일 14,082,893,665 bytes 재사용, 3,243,762,799 bytes 신규 저장 |
| 13:35 restic 탐색 | daily 사본 완료 전이어서 처리 0개, 날짜 집계 제외 |
| 실제 보관 실행 | 14:05:00–14:07:48, 새 사본 1개 처리 |
| restic 저장 명령 | 14:06:50–14:07:26, 약 35.98초, 종료 코드 0 |
| `check --read-data` | 14:07:28–14:07:48, 약 19.89초, 종료 코드 0 |

백업 ID는 `11b8326f40d3c00e3526fad36e7377b5ae211e7375baa76745ec644fd01186cf`다.
새 restic snapshot 전체 ID는 다음과 같다.

```text
648d7c93eadab36b79295808bfa54b0e6f45b22e4e38e5ed363b540572361f9e
```

snapshot 기준 시각은 checkpoint의 UTC 시각 `2026-09-30 04:33:35.779067`을 사용하며,
실제 보관 시작·완료는 별도로 기록한다. 저장소 전체 읽기 검사는 기존 snapshot을 포함한다.
이번 점검이 전체 검사나 복원을 다시 실행한 것은 아니다.

## 날짜 집계와 증거

원래 백업 상태·보관 시도·검사 영수증으로 오늘 날짜 자격을 다시 계산했고 observation의
증거와 일치했다. 원본 manifest 해시도 일치하며 수정하지 않았다.

- 현재 start Job `588020`, invocation `aebd5ed979fe46a391f0b6f87fbb2ca3`.
- 판별 `verified_timer`, 방식 `current_start_job_activation_details`.
- 실제 trigger 시각 `14:05:00.294243 KST`.
- journal의 전체 결과 JSON과 영수증 파일이 동일하며 PID·invocation도 일치한다.
- 같은 Job의 완료 결과 `done`을 확인했다.

지난 점검인 9/29 17:14 이후 9/30 20:35까지 **55회 예약이 모두 성공**했고, 모두
`verified_timer`였다. 새 사본 처리 **1회만** 날짜에 추가했고, 처리 0개 **54회는 제외**했다.
완료 날짜는 **9/29·9/30**, 나머지 날짜와 과거 판별 실패 실행은 임의로 채워 넣지 않았다.

`scheduled-receipt.json`, `archive-run.json`, `archive-proof.json`,
`qualifying-journal.jsonl`, `scheduled-runs-since-previous.json`, `results.json`에 근거를 보존했다.
`restic-journal.jsonl`의 넓은 기간 조회는 journalctl 기본 크기 제한으로 큰 MESSAGE가 null일 수 있어,
실제 집계 대상 실행은 `--all`로 다시 읽은 `qualifying-journal.jsonl`과 대조했다.

9/28에 명시적 ID로 통과한 추출→프로그램 복원→조회·무결성→독립 백업 검증 결과는
이전 기록과 동일하다. 복원 결과 수 1개·선택한 ID를 유지했으며 오늘 새 시험으로 세지 않았다.
현재 보관 상태는 healthy, 대기 사본·불완전 폴더·checkpoint 오류 0개다.

## 일별 데이터 갱신의 별도 미완료 항목

**13:10의 `marketdata-backup.service`와 정오의 `marketdata-daily.service`는 다른 작업이다.**
전자는 저장된 자료의 무결성 검사·백업이고, 후자는 공개 시장 자료를 갱신한다.
백업 성공이 모든 데이터셋의 최신 날짜 수집 완료를 의미하지 않는다.

정오 갱신은 12:25:42 KST에 종료 코드 2, systemd `failed` 상태다. 실제 보고서의 사유는
9/29 UTC 일자의 선물 자료 4종 `waiting_source`이며, 처리 오류 목록은 비어 있다.

| 대기 자료 | 아직 수집되지 않은 원천 범위 | 20:44 HTTP HEAD 확인 |
|---|---|---|
| 거래 캔들 1분 | 9/29 UTC 1일, 1,440행 | 200, Last-Modified 17:21 KST |
| mark price 1분 | 같은 범위, 1,440행 | 200, Last-Modified 17:42 KST |
| index price 1분 | 같은 범위, 1,440행 | 200, Last-Modified 16:53 KST |
| OI archive 5분 | 같은 범위, 288행 | 200, Last-Modified 15:53 KST |

현재는 네 파일 모두 공개 응답을 확인했다. Last-Modified는 서버가 응답한 수정 시각이며
최초 공개 시각의 직접 증명은 아니다. 정오 실행 당시의 대기 기록과 이후 응답을 구분한다.
현물 캔들·funding·metadata는 정오 갱신에서 완료였다. OI archive의 대기는 실시간 OI 수집 중단을
뜻하지 않는다. 이번에는 본문 다운로드·재수집 없이 HEAD만 확인했으므로 로컬 누락은 아직 남아 있다.

`daily-update-latest.json`, `waiting-source-head.json`, `daily-journal.jsonl`에 근거를 남겼다.
현재 설정의 다음 정기 갱신에서 이전 날짜를 다시 수집할 대상이며, 실제 수집·검증 전에는 완료로
표시하지 않는다. 오후 공개 후 재탐색을 예약할지는 후속 운영 보완 항목이다.

## 유지 상태와 다음 점검

호가·체결 수집은 실행 중이고, 20:15 정기 파일 정리도 정상 완료했다. 기존 유닛·백업 manifest
22개와 restic 예약 판별 소스 해시가 유지됐다. 수집 재시작·예약 변경·복원 재실행·사본 삭제·
forget/prune·기존 백업 대체는 수행하지 않았다. 호가 24시간 자료의 별도 검증은
[호가 점검 기록](../depth-observation-2026-09-30/summary.md)에 있다.

다음은 **10/1 14:15 KST 이후** 확인한다. 새 daily는 13:10 예약이며, 완성된 새 사본이 실제
restic 예약에서 저장·전체 검사되면 3/3이 된다. 3/3이 되더라도 기존 방식 대체나 삭제를
자동으로 시작하지 않는다. 정오 갱신에서 위 9/29 대기가 해소됐는지도 함께 확인한다.
