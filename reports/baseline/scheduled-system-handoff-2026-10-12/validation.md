# T15 추가 검증 범위

## 통과한 격리 검사

| 검사 | 수 | 실제 사용한 부분 / 대역·한계 | 원본 |
|---|---:|---|---|
| 인계 단계·재개·원복·명령 제한·파일/링크 writer·실제 CLI 진입점 | 61 | 실제 격리 파일 transaction, 두 manager의 명령 경계는 모델 | [최근 63개 통합 결과](handoff-preflight-tests.xml), [인계 61개](handoff-tests.xml) |
| 시험 의존성·명령 렌더링·실패 진단·사용자 계정/기존 근거 재사용 | 5 | native systemd verifier, 진단 출력 대역. 서비스 기동 없음 | [결과](preflight-units-tests.xml), [native 9개 결과](preflight-native-verification.json) |
| 기존 supervisor 연속 실행·새 시작 보류·적용/원복 | 4 | 실제 설치된 supervisor/gate, 격리 clock/child/authority/filesystem | [결과](collector-connection-tests.xml), [입력](test_collector_connection.py) |
| system 기록 보존 후 user 경로의 추가 백업/보관 | 1 | 실제 설치 T05/backup와 작은 실제 restic 저장소·전체 읽기 검사. bus 응답만 대역 | [결과](record-rollback-tests.xml), [입력](test_record_rollback.py) |
| 새 구성 계획의 원래 transaction 회귀 | 152 재검증 | 기존 검사 재사용. 새로운 152개로 합산하지 않음 | [결과](corrected-transaction-tests.xml) |

추가로 부분 원복 요청 대조·수동 재요청 **1개**를 확인했다.
[결과](rollback-runbook-tests.xml), [입력](test_rollback_runbook.py). 실제 격리 전환 기록과 manager 모델을 사용했다.
자동 재발행 기능을 추가한 검사가 아니라, 원래 취소 기록을 보존하고 원복 후 미발행 요청을 정확히 다시 고르는 절차의 검사다.

추가 고유 검사 72개. 기존 후보의 240개 및 별도 설치 152개 재현은 당시 결과로 유지한다.
시험 수를 합쳐 실제 운영 이전의 검증 수로 표현하지 않는다.

네 수정 CLI는 실제 `--help` 진입을 검사했고, 나머지 daily·backup·restic·network도 실제 설치본/고정 소스의
`--help` 반환 0을 확인했다. [나머지 네 진입점](remaining-entrypoint-help.json).
`--help`는 인자 파싱 후 Store·HTTP·업무 실행 전 종료한다. 운영 DB의 plan/run을 실행한 결과가 아니다.

## 인계 오류 입력

- 8개 진행 경계, 사용자 유닛 16개 mask 경계, 시스템 timer 8개 시작 반환 경계에서 중단 후 재개.
- 부분 mask·pending·commit·기동 후 원복. 새 manager 자료를 흉내 낸 별도 파일 보존.
- 실행 중 oneshot은 ActiveState뿐 아니라 MainPID/ControlPID로 판정. PID가 0이어도 running job이면 unknown으로 대기.
- waiter 뒤 waiting job 또는 auto-restart만 persistent mask 재확인 후 취소.
- 정상 종료 대기 만료는 hold이며 업무 stop/kill로 전환하지 않음.
- 시작 요청을 기록한 뒤 중단된 경우 새 invocation/기동 근거 없이 자동 재발행하지 않음.
- 반복 Exec 속성 보존, 단일값 중복 거부, 실제 이 호스트의 plain list-jobs 형식 검사.
- 승인 경로만 파일·링크 교체, 부모 symlink 거부, leaf symlink 대상에 쓰지 않음.
- collector·Chrony·시각 명령은 manager mutation allowlist에 없음.

격리 예외 주입은 내구성 경계의 재개 시험이다. 실제 전원 차단이나 운영 manager job 경합 전체를 재현한 것은 아니다.

## 관리자 시험

첫 root 실행 `e4b453a62d8342d7`은 시험 의존성 결함으로 첫 서비스 start 전에 중단됐다.
이 결과의 `operating_unchanged=true`와 cleanup 성공은 확인됐지만 **8개 환경·timer·drain 시험 통과는 아니다**.
그때 실행한 도구 `1d0b7392d86b5168`은 [아카이브](tool-history/scheduled-handoff-20261012-1d0b7392d86b5168.tar.gz)에 보존했다.

관리자 점검 전체 범위:

1. nonce `/opt` 실행본·8개 최종 서비스의 설정/보호 옵션에서 UID 1000 import·설정/암호 읽기·공유 잠금.
2. 실제 system timer의 job/PID/invocation/trigger 대조, manual service와 다른 unit/timer의 부인.
3. 임시 Persistent calendar 따라잡기와 OnActive 실행의 출처.
4. 두 manager의 oneshot 실행·waiter 뒤 대기·Restart 대기 분류, persistent mask·대기 취소·정상 종료.
5. 양 manager의 동일 시험 잠금 충돌, 명시 wants 링크 생성/제거와 base 보존.
6. 운영 before/after 파일·ledger·신원·목록 동일, 시험 유닛·경로·stamp 정리.

운영 근거/암호 파일은 읽기만 하며 암호 내용·길이·해시는 보고하지 않는다. 쓰기와 잠금 충돌 입력은 시험 경로다.
운영 service stop·timer 전환·clock 제어·자료 삭제·backup 추가 실행은 없다.
시각 readiness와 업무 본문은 이번 접근 시험에서 시험용 준비 유닛/`--help`로 대체하며, 운영 정책 검증과 혼동하지 않는다.

두 번째 [97f22b03bbf940c3](system-preflight-result-97f22b03bbf940c3.json)에서는 1–3과 5의 wants 링크 확인을 통과했다.
사용자 시험 유닛의 명시적 User가 보조 그룹 설정을 시도해 `216/GROUP`으로 실패했고, 양 manager 종료·잠금·mask/원복은 미완료였다.
후보 system 서비스가 아니라 사용자 임시 유닛의 배치 오류다. 운영 상태 보존·19개 시험 유닛/경로 정리를 확인했다.

사용자 시험 유닛은 기존 manager 계정으로 실행하고 시스템 시험 유닛만 User=yoonhc를 지정하도록 수정했다.
새 도구 `scheduled-handoff-20261012-b48b6beb0b9b72b9`의 `--scenario lifecycle`은 남은 4–5를 실행한다.
통과한 부분은 원본 SHA256 `d1eee4235541062185ea19575c56da1410486be51003927466e9e6785732493b`,
동일 운영 before/after, 변경하지 않은 소스·실행기·worker 해시를 대조해 재사용한다.
실패한 전체 보고서를 성공으로 바꾸지 않으며 실패한 lifecycle 결과를 통과로 세지 않는다.
기존 13개 결과 묶음과 새 lifecycle 결과의 출처를 최종 보고서에서 구분한다.

**23:24:53 KST 잔여 시험 통과.** [a6d642d447dc4c69](system-preflight-result-a6d642d447dc4c69.json)의
`passed=true`, `operating_unchanged=true`, cleanup errors 0을 원본에서 대조했다.
양쪽 hold worker는 UID 1000·normal_completion=true, 취소한 queued 본문은 미실행,
양 방향 lock-try는 lock_conflict=true였다. mask 전후 같은 실행 PID를 확인한 뒤 release 파일로 정상 완료시켰고,
시험 unit 원복 후 loaded를 확인했다. 마지막 10개 시험 unit은 cleanup 후 모두 not-found다.

앞선 13개 결과 묶음은 원본 연결, 이번 4개 결과 묶음(두 lock·두 drain)은 신규 실측이다.
운영 94개 node·boot ledger·Chrony/두 supervisor PID와 invocation·양 manager 유닛 목록이 전후 동일했다.
세 실행의 실패/통과 이력은 각각 보존한다. 일부 성공의 재사용을 전체 실패 보고서의 통과로 표현하지 않는다.

[최종 계획](host-plan-preflight-passed.json)은 실제 before 소유권 46개와 통과 보고서 해시를 고정했다.
운영 approval은 false이며 실제 설치/인계·첫 운영 결과는 이 시험에 포함되지 않는다.
