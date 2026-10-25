# T05 timer 복원 직후 시각 검사 실패 — 별도 후속

2026-10-25 16:02 T05 run `2483ead46a474a359556a514a93b90eb`는 system timer에서 시작됐지만 `Expected today's 15:40 trigger`로 **failed/1**이다. plan·selection 없음, `deletion_attempted=false`, 삭제 0개다. [당시 원본 대조](timer-restoration-results.json)와 [18:04 manager 상태](manager-after-archival.txt)에서 같은 실패 invocation `c5a0eeea15a9472d825ce60a5e1d6ebc`를 확인했다. 정상 보류·삭제 완료로 바꾸지 않는다.

실제 패키지는 `systemd 255 (255.4-1ubuntu8.16)`, `libsystemd0:amd64 255.4-1ubuntu8.16`이다. 설치 timer는 15:40 KST calendar, `Persistent=false`를 유지한다. 다음 실제 예약은 **2026-10-26 15:40 KST**다. 검사 완화·reset-failed·수동 재시작·systemd 업데이트를 하지 않았다.

## 실제 전환 기록

| 시각 KST | 기록 |
|---|---|
| 15:29 | original operation이 9개 timer 신규 예약 보류 후 빈 job JSON 처리에서 실패. 구성 before 유지. |
| 15:43 | 첫 recovery가 파일 5개 after·pending ledger까지 전환 후 누락된 `ExecStartPre` 조회에서 실패. timer는 보류. |
| 16:02:01–03 | 별도 승인한 manager recovery가 구성·ledger·grant를 확정하고 이전 active timer들을 start. service 직접 시작 없음. |
| 16:02:03.120950 | T05의 실제 start job `14518540` ActivationDetails가 지정 timer trigger를 기록. |
| 같은 실행 | T05의 15:40 조건 검사 거부. 삭제 진입 전 실패. |

구체적인 timer stop/start 전후 상태·명령과 구성 reload 연결은 original `/var/lib/marketdata-clock/t18-output-handoffs/27dd5101bfa0b7005ce0a931b075bc9cccad5a5331e9350a30c4765cbf1ee225/`의 `state.json`·`transaction.json`·실패 attempt들과, 성공 recovery `ab49fce3…/attempt-fe166c7a29ed48debc820fe398155cef.json`에 보존한다. 적용 도구와 engine이 각 단계에서 호출한 경로를 대조했으며, 이번에 별도 장애 재현이나 package source 원인 규명까지 수행한 것은 아니다.

## 설명의 정정과 다음 검토 조건

`Persistent`는 calendar timer의 마지막 발동 시각을 디스크에 저장하고 비활성 기간의 누락 실행을 보충하는 기능을 제어한다. 이를 모든 stop/start·reload 이후 비예약 시각 요청을 배제하는 보장으로 확대하지 않는다. [systemd v255 timer 공식 문서](https://raw.githubusercontent.com/systemd/systemd/v255/man/systemd.timer.xml).

공식 저장소에도 timer 재시작 뒤 off-schedule 시작이 보고돼 있다. 최초 보고 환경은 systemd 254.3/NixOS이며, **이번 Ubuntu 패키지의 동일 결함이라는 판정은 하지 않았다**. [systemd issue #31231](https://github.com/systemd/systemd/issues/31231).

다음 timer 복원 계약을 개선할 때 설치 패키지 구현과 당시 stop/start·reload·job/trigger 기록을 연결해 원인을 좁힌다. 실행 자격 실패의 표시와 배포 중 timer 복원 가능성을 다루되, 근거 없이 T05의 시각 검사를 완화하지 않는다. 이 조사는 여섯 사본 편입의 선행 조건이 아니다.

10/26의 자연 예약은 당시 당일 backup ID·동일 snapshot 전체 검사·현재 시각·후행 관계·잠금·새 plan과 최종 결과를 따로 확인한다. 이미 승인된 반복 범위는 유지하며 확인용 수동 실행이나 새 삭제 승인을 요구하지 않는다. 미완료/불명확 상태는 기존 반복 승인으로 우회하지 않는다.
