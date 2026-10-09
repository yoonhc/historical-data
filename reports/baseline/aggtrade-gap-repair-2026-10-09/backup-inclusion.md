# aggTrade 보강분의 예약 백업 편입 확인

2026년 10월 9일 REST로 보강한 collection `852ddace89cc418bb1b2e1aaa839519b`가
새 checkpoint와 예약 restic 저장·전체 검사에 포함됐다. T03의 완료 조건을 충족했다.

| 항목 | 실제 결과 KST |
|---|---|
| checkpoint 작업 시작 | 10월 9일 13:30:32. 예약 시각 13:10과 실제 시작을 구분 |
| checkpoint 기준 시각 | 14:28:08.608307 |
| 완료 사본 확정 | 14:33:29.214073 |
| backup ID | `6ba0fadd2ede16951c0448f6a7174c53e4cf22d9e19ee26c92e0be44b125b145` |
| restic 저장 | 14:48:30~14:49:46 |
| snapshot ID | `be8d132a6f36336b131849eb15b9952e418bf5ef0fecc1794c2adc5b14fbb09a` |
| 예약 전체 데이터 검사 | `check --read-data`, 14:49:49~14:50:55, 종료 코드 0 |
| 예약 보관 실행 | `fb23abb147984902be7a5574d620024c`, 새 사본 1개 처리 |

완료 사본의 카탈로그에서 collection·265개 segment와 기존 검증 목록을 대조했다.
원문 265개와 표준 파일 265개의 논리 바이트 해시가 일치하며, 물리 파일도 checkpoint manifest에 포함됐다.
합계 264,070행은 보강 대상 264,069개 ID와 왼쪽 경계 1행이다.
명시적 restic snapshot에서 읽은 `backup.json`은 이 checkpoint의 manifest와 바이트 단위로 같았다.

해당 snapshot을 포함하는 기존 예약 전체 검사 receipt와 실제 systemd 시작 작업의
`verified_timer` 기록을 대조했다. 처리 0개의 다른 실행을 편입 증거로 세지 않았다.
이번 점검에서 전체 검사를 다시 실행하거나 530개 파일을 restic에서 새로 추출한 것은 아니다.

최초 점검은 샌드박스가 restic 조회의 임시 저장소 잠금 쓰기를 막아 중단됐다.
일반 사용자 권한으로 재실행한 최종 점검은 통과했고 archive 상태 파일 해시는 유지됐다.
checkpoint 자동 정리 service/timer의 `/dev/null` mask도 유지했다.

- [편입 대조 원본 결과](../chrony-source-candidate-2026-10-09/backup-observations/20261009T073637Z/result.json)
- [530개 논리 파일 대조](../chrony-source-candidate-2026-10-09/backup-observations/20261009T073637Z/verified-inclusion.json)
- [예약 실행과 mask 근거](../chrony-source-candidate-2026-10-09/backup-observations/20261009T073637Z/schedule-and-mask.json)

이 결과는 보강분의 백업 편입 확인이다. T02의 첫 세션 전체 점검·24시간 관찰이나,
아직 백업 계약에 들어가지 않은 기존 runtime-clock 로그의 보존을 완료했다는 뜻은 아니다.
