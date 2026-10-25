# 동일 계획의 검토된 잠금 보류 재개 — 별도 승인 대기

기존 사용자는 첫 수동 정리를 승인했고 계획 ID는
`31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08`이다.
후보 10개·보호 3개·제외 3개와 scope
`da7b03dba075a816d1c8277298dd123ce1db62e54aa9a85920711a18913a6c22`는 변경하지 않는다.

첫 시도의 유닛 링크 거부 `4a61c943702c41bcb4b5e854ac098440` 이후 동일 바이트 유닛 설치 보완은
완료됐다. 두 번째 시도 `d1c40fd557974ed9b462d1ea98e689ff`는 archive repository lock 획득 실패로
exit 75·삭제 0개로 보류됐다. 원본 run·root receipt·시각 로그·restic 조회·겹친 예약 실행은 보존한다.
설치 코드상 `deletion_attempted=true`는 실제 적용 함수와 잠금 진입 **이전**에 기록된다.
이번에는 journal·staging·적용 inventory가 없고 후보 root ID·모든 manifest도 일치한다.

현재 설치기는 이런 이전 run을 `unfinished_retirement`로 차단한다.
설치 CLI 계약은 **“Recovery is proposed and separately approved, never enabled by a run flag”**이며,
기존 승인 문서의 `resume_interrupted=false`를 자동으로 true로 바꾸지 않는다.

## 검토할 구체적 권한

`manual_resume.py --run --approve-plan <위 계획> --approve-recovery-run d1c40fd557974ed9b462d1ea98e689ff`
실행을 명시적으로 승인하면 다음 **한 번의** 서비스 시작에 한정한다.

- 설치된 `propose-manual --resume-interrupted`의 비활성 제안을 현재 scope·boot·같은 계획으로 대조한다.
- 과거 두 run과 root 결과의 ID·해시·보류 사유·삭제 0 및 비활성 복원을 대조한다.
  다른 시도, journal, staging, inventory, 변경된 후보/유지/제외 manifest가 있으면 거부한다.
- 유지되는 boot·구성·ledger·provider와 기존 미리보기의 시각 anchor를 새 관측으로 대조한다.
  보존 업무 유휴 및 실제 maintenance/archive 잠금의 비경합을 확인한다.
  잠금은 입장 검사 뒤 해제하며 서비스의 자체 잠금 획득을 대신하거나 우회하지 않는다.
- root manual.json에 같은 계획의 `resume_interrupted=true` 권한을 일시 설치하고,
  이미 설치된 UID 1000 시스템 수동 서비스를 한 번 요청한다.
- 설치된 엔진의 현재 시각·당일 백업/restic·근거·최종 대상 선택·유지 사본 전체 무결성·
  후보 전체 inventory·삭제 직전 시각 검사를 그대로 수행한다.
  기존 복구 모드는 오래된 preview 관측의 anchor를 매 삭제마다 요구하지 않는다.
  이 제안은 같은 boot/config/provider를 앞에서 대조하고, 실제 삭제에는 엔진의 새 관측과 감시를 사용한다.
- 완료 또는 보류/실패 후 서비스가 유휴이면 원래 manual.json의 비활성 바이트로 복원한다.
  서비스가 실행 중이면 강제 종료하거나 권한을 바꿔치기하지 않고 기록을 남긴다.
- 다시 보류되거나 새 시도가 생기면 자동 반복하지 않는다. 새 이력은 별도 검토한다.

유닛/코드 재설치·daemon-reload·다른 서비스/timer 제어는 수행하지 않는다.
반복 자동화, 다른 계획/부분 삭제의 범용 재개, 사본·DB·근거의 과거 상태 복원,
원본 실패 이력 수정, Chrony/보관 정책 변경은 승인 범위에 포함하지 않는다.

이 파일을 준비한 것은 운영 승인이나 권한 활성화가 아니다.
사용자가 위 명령 또는 이에 해당하는 범위를 승인하기 전에는 도구의 읽기 전용 점검만 수행한다.
