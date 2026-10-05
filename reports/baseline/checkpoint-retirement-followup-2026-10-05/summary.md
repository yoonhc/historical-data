# checkpoint 정리 후 첫 새 daily·예약 restic 확인

2026-10-05 14:38 KST 점검. **새벽에 작업용 사본 5개를 정리한 뒤, 새 daily 사본 생성 →
사본 검증 → 같은 backup ID의 예약 restic 저장 → 전체 읽기 검사가 정상 완료됐다.**
14:35 후속 예약도 처리 0개로 정상 종료했으며 중복 스냅샷은 추가되지 않았다.
[검증 결과](results.json)와 [대조 스크립트](verify_followup.py)에 근거를 남겼다.

시각을 구분하면 checkpoint 수동 정리는 03:25에 사후 확인까지 끝났다.
14:15 작업은 별도의 정기 파일 묶음 정리이며 checkpoint 삭제 예약이 아니다.
[14:15 실행](maintenance-hourly.json)도 14:15:56에 정상 완료했다.

## 실제 실행과 검사 범위

| 단계 | 실제 시각 KST | 확인 결과 |
|---|---|---|
| 정기 daily | 13:10:00~13:58:02 | 원본 논리 무결성 1,033,153건, 오류 0; 새 checkpoint 생성 |
| checkpoint 기준 시각 | 13:53:47.247287 | manifest의 `checkpoint_finished_at`; restic snapshot time과 일치 |
| 예약 archiver | 14:05:00~14:10:13 | `processed=1`, 성공; 현재 start Job의 timer 기동 근거 확인 |
| 입력 사본 검증 | 14:08:27 완료, 약 3분 16초 | 파일 48,288개·카탈로그 검증, 동일 backup ID |
| restic 저장 명령 | 14:08:27~14:09:27 | exit 0, 새 snapshot 1개; 약 60초 |
| restic 전체 읽기 검사 | 14:09:29~14:10:12 | `check --read-data`, exit 0, 오류 0; 약 43초 |
| 후속 예약 탐색 | 14:35:00~14:35:13 | 처리 0개, snapshot ID 목록 동일 |

daily의 원본 논리 무결성 단계는 journal 단계 시각 기준 약 **41분 2초**다.
입력 사본 검사와 restic pack 전체 읽기 검사는 별도 단계이며, 이 시간을 restic 저장 시간으로
합치지 않는다. 이번 점검은 이미 실행된 검사 결과를 확인했고 전체 검사·복원은 다시 실행하지 않았다.

[daily 결과](maintenance-daily.json), [입력 사본 검증](verify-input.json),
[예약 receipt](scheduled-receipt.json), [archive 실행](archive-run.json),
[ID·검사 연결](archive-proof.json), [서비스 journal](service-journal.jsonl)을 대조했다.
실제 `restic backup`과 `check --read-data`의 명령·종료 코드·출력도 별도로 보존했다.

| 식별자 | 값 |
|---|---|
| daily ID | `7a1b697d9a434f43a32660760b4b8b26` |
| backup ID | `46f4734b48f7e81c4ef805479c8d2c7b605215c6881b3d6b38a0a3a80d19bc1b` |
| restic snapshot ID | `19bde5fb81b7dde7d45e2bba3504a9bd7969c7b2d1414803a528a0b4f1f6d462` |
| archive run ID | `4187eaf0854346988d25875b697bda19` |
| scheduled run ID | `460689ce971341a1ab57e48b17a2382b` |

새 사본 경로는
`/mnt/backup/marketdata-historical/automatic/2026-10-05/8a0395aeb9084eddb0a97f97b9c0a0a5`다.
manifest의 hash·원래 source_root를 검사했고 snapshot의 태그·절대 경로·기준 시각 및
저장소에서 읽은 manifest hash와 일치했다. 기준 시각 13:53:47과 실제 보관 완료 14:09:27은
별도 필드로 유지한다.

이번에도 프로세스 환경의 timer 시각은 현재 start Job과 달랐다(`environment_matches_job=false`).
기존 구현대로 **현재 start Job의 ActivationDetails**에서 14:05 timer 기동을 확인하고,
journal의 PID·invocation·완료 메시지까지 연결했다. 환경변수만 보고 예약 성공으로 세지 않았다.

## 현재 저장소와 사본

[상태 조회](status.json)는 `healthy=true`, 미보관 사본·미해결 오류·검사 미포함 지점 0이다.
이 로컬 상태와 별개로, UUID·저장소 ID 검사를 거쳐 [실제 snapshot 목록](live-snapshots.json)을 읽었다.
기존 8개 전체 ID·메타데이터가 동일하고 오늘의 1개만 추가돼 총 **9개**다.
오늘 backup ID에 연결된 snapshot도 하나뿐이다.

[로컬 manifest 대조](local-manifests.json)에서 기존에 남긴 6개 hash는 동일했고 새 사본 하나만
추가돼 총 **7개**다. 이는 편입 전 사본 3개와 10/2~10/5 사본 4개이며, 추가 삭제하지 않았다.
신규 daily는 남아 있는 10/4 사본을 하드링크 재사용 기준으로 정상 선택했다.
전체 파일을 다시 읽는 검사가 아니라 manifest와 기존 정기 검사 근거의 대조다.

[14:35 후속 대조](scheduled-next-noop-result.json)는 별도 실제 예약 receipt와 journal에 근거한다.
이 처리 0개를 새 백업 성공으로 세지 않는다. 기존 초기 날짜 관찰 **3/3(9/29·9/30·10/1)**과
복구 증거는 변하지 않았으며, 오늘의 새 사본 검증은 **첫 checkpoint 정리 후 운영 연결 확인**으로
따로 기록했다.

## 다음 확인과 변경 범위

[서비스 조회](services-after.json)에서 daily·restic·파일 정리는 마지막 실행 success,
호가·aggTrade는 running이다. 해당 표본의 다음 예약은 restic 오늘 15:05,
파일 정리 15:15, daily 내일 10/6 13:10이다. 예약 시각 자체는 실행 완료 증거가 아니다.

**오늘 15:05 이후 호가 새 버전의 첫 정기 종료·다음 수집 시작 확인은 아직 미완료**다.
대상은 [배포 시 고정한 collection ID와 후속 항목](../depth-status-deploy-2026-10-05/followup.json)이다.
이번 백업 점검을 호가 세션 종료·재생 검증이나 Binance 일별 자료의 완전성 확인으로 확대하지 않는다.

점검 당시 HEAD는 `9d2d136e7aadd65c38937095daeb33f5211f94bf`이며 운영 소스는 해당 commit과 일치했다.
새벽 정리 이후 소스 차이는 이미 승인·배포한 `depth.py`·`depth_stream.py` 두 파일뿐이다.
이번에는 운영 소스·정책·서비스를 변경하지 않았고, 백업·검사·복원 수동 재실행,
checkpoint 추가 삭제·자동화·restic `forget`·`prune`도 하지 않았다.

점검 도구의 최초 journal 시각 표기 오류와 긴 MESSAGE 생략, restic 조회의 샌드박스 잠금 제한,
14:35 예약과 겹친 유휴 검사 중단은 [실행 메모](execution-notes.json)에 남겼다.
초기 journal과 잠금 실패 출력은 보존했으며, 최종 점검은 전체 journal 메시지·명시적 UTC·정상
restic 잠금을 사용해 통과했다. 이 점검 재시도를 운영 백업 실패로 집계하지 않는다.
