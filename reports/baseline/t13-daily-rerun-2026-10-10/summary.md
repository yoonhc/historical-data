# T13 — 10/9 UTC 공개 아카이브 daily 재실행 완료

사용자의 재실행 승인에 따라 기존 `marketdata-daily.service`를 한 번 시작했다.
**2026-10-10 18:52:54 KST 성공 종료**, daily 대상 **8개 모두 complete**다.
미완료였던 10/9 UTC의 선물 trade/mark/index bars와 OI archive **4종·4,608행**이 실제 편입됐다.
현재 날짜 보강과 검증은 완료했고, 공개 지연에 대한 예약·재시도 정책은 TODO의 조건부 검토에 유지한다.

## 실행과 소요 시간

- 명령: `systemctl --user start marketdata-daily.service`, 반환 코드 0.
- 기존 공통 clock-ready·ExecStartPre와 CLI writer 잠금을 사용했다.
- ExecStart: **18:52:47–18:52:54 KST**, 단조 시각 차이 **7.129067초**.
- 애플리케이션 보고 구간: **18:52:47.734034–18:52:54.681213**, **6.947179초**.
- `Result=success`, `ExecMainStatus=0`, `NRestarts=0`.
- invocation: `f8bb83e285dd45b69928759e0129c32b`.
- 오전 실패 이력은 보존했고 `reset-failed`·설정 변경은 사용하지 않았다.

이번에는 시각 준비 대기가 길지 않았다. 이 시간은 다른 실행의 gate·잠금 대기를 보장하지 않는다.
기존 `refresh_days=3`에 따라 10/7–10/9 자료를 재확인하고 funding·metadata를 포함한 8개 target을 처리했다.
실시간 aggTrade/depth 원문 보강이나 checkpoint 생성 명령은 아니다.

## 실제 새 자료의 확인

카탈로그를 읽기 전용으로 열고 네 active revision, 원본 ZIP·표준 Parquet의 해시,
행 수·정확한 하루 timestamp label 격자·보고서의 revision 연결을 대조했다.

| 10/9 UTC 대상 | 새 행 수 | timestamp label 범위 | revision ID 앞부분 |
|---|---:|---|---|
| 선물 trade bars 1m | 1,440 | 00:00–23:59 | `0f52e5c1e48f255e` |
| mark price bars 1m | 1,440 | 00:00–23:59 | `ab6811423d46fb3f` |
| index price bars 1m | 1,440 | 00:00–23:59 | `08c2b2c9e6b3891a` |
| OI archive 5m | 288 | 00:00–23:55 | `40c25cf9a8cc7757` |

네 대상의 운영 범위 coverage는 missing rows/ranges·blocked days가 0이다.
OI 원본의 `source_order_reversed` 경고(`adjacent_count=23`)는 revision 품질 기록에 보존했다.
표준 파일은 정렬된 288개 label을 충족하며, 별도의 사건 시각 의미를 인증하지 않는다.
funding complete도 원천 조회 범위 충족이며 모든 사건 존재의 독립 증명은 아니다.

이번 네 파티션의 실제 파일과 8개 target 완료 기록을 확인했으며 전체 저장소 전수 검사는 반복하지 않았다.
정확한 revision·source ID·SHA-256·coverage와 실행 보고서는 [검증 결과](verification.json)에 있다.
[실행 전](before-daily-latest.json)과 [실행 후](after-daily-latest.json) 보고서도 보존했다.

## 유지한 범위와 후속

- daily 설정·timer·clock gate와 운영 runtime/Chrony 프로필의 전후 해시는 일치한다.
- Chrony PID 1245와 수집 supervisor PID 224785/224784는 기존 시작 시각·invocation을 유지한다.
- 자동 checkpoint 정리 service/timer는 **masked·비활성**이다.
- 다음 daily timer는 관측 당시 **10/11 12:23:33 KST**였다. 랜덤 지연이 있는 다음 예약이며 완료 시각이 아니다.
- 오늘 checkpoint/restic이 끝난 뒤 편입한 자료다. **이번 보강분의 checkpoint/restic 편입은 아직 확인하지 않았다.**
  기존 백업 대상에 들어가며 이번 작업에서 별도 백업·restic 실행을 추가하지 않았다.
- 공개 지연과 짧은 재시도 조합은 별도 조건부 검토다. 이번 성공을 예약 정책 개선 완료로 세지 않는다.

[실행 전 상태·설정 해시](before.json), [systemd 결과와 journal](host-result.json),
[파일 검증·상태 수집 스크립트](verify_rerun.py)를 함께 보존한다.
기존 [18:40 점검](../t13-daily-followup-2026-10-10/summary.md)은 당시 관측으로 유지한다.
