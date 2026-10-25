# 10/17 당일 checkpoint·restic 완료 확인

**15:32 KST 확인: 당일 checkpoint 생성과 동일 backup ID의 restic 저장·전체 읽기 검사가 완료됐다.**
기존 정기 실행의 기록을 읽어 대조했다. 추가 백업·restic 검사·복원·정식 미리보기·서비스 제어는 실행하지 않았다.

| 단계 | 실제 완료와 결과 |
|---|---|
| maintenance daily·checkpoint 생성 | 14:46:36 KST, 검증 2,044,850건·실패 0개, 새 사본 생성 |
| restic 저장 | 15:14:42 KST, 동일 backup ID의 새 snapshot 저장·exit 0 |
| `check --read-data` | 15:17:01.938 KST, 대상 snapshot 포함·오류 0개·exit 0 |
| restic wrapper·서비스 종료 | 요청 complete·감시 종료·오류 없음, 실제 service 15:17:02 KST exit 0 |

- backup ID: `9b916470eca7e5da1462dff9c39380869242bf6711861eaea754c010ccf00963`
- restic snapshot ID: `0911b25d3a270fad604eeb222748e950ea45383b68d9b451e6d785b92672c58f`
- archive run ID: `6845ad8849f2414aac798369806f6310`
- system invocation: `e0860d76785b4c7cb3dff705ffddb60c`

[checkpoint 완료 대조](backup-completion-20261017.json)와
[restic 원장·원본 check 출력·요청/저장 결과 대조](restic-completion-20261017.json)에
정확한 원본 경로·해시·시각·ID를 보존했다. 전체 읽기 검사 stdout은 `num_errors=0`, stderr는 비어 있었다.
요청이 참조하는 예약 실행 보고서의 바이트 해시도 일치했다.

archive receipt의 T05 bundle 해시 `63fe2366…`와 객체 14개 중 새 완료 객체 `2e242b0a…`가
maintenance 보고서와 일치했다. 이번 확인은 기록의 연결 대조이며 지정 snapshot의 bundle을 직접 읽는 복원 검사를 새로 수행한 것은 아니다.

`recurring.json`은 승인한 after 바이트와 같고 `enabled=false`·승인 필드 null을 유지한다.
user/system 정리 service·timer 네 mask도 유지한다. 당일 backup/restic 완료 조건을 확인한 것으로,
전체 inventory·유지 사본 무결성·현재 시각 등 정식 미리보기의 모든 조건을 검사한 결과는 아니다.
다음은 현재 구성의 정식 미리보기 단계이며, 실제 삭제와 반복 자동화 승인은 별도다.
T05 전체나 T16 전체의 완료로 표시하지 않는다.
