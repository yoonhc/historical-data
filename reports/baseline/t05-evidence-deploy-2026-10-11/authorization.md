# T05 근거 생성 기능 설치·기존 사본 등록 승인 범위

2026-10-11 사용자 지시: **“오케이 다음단계로 넘어가자”**.
직전 결과에서 다음 단계를 고정 근거 생성 기능 설치와 기존 사본의 사후 근거 등록으로
명시했고, 사용자가 진행을 승인했다. 관리자 인증 요청은 이 범위를 다시 승인받기 위한 것이 아니다.

대상 후보: `t05-completion-evidence-20261011-c9d413cfe11ec0e7`.
설치 전 시스템 미리보기 `5dde262e40b34192`에서 확인한 **동일한 7개**의 backup ID·manifest 해시·
source_root·scope를 최종 설치 경로의 새 inventory와 대조한다. 목록이 달라지면 등록 전에 중단한다.
임시 미리보기 plan ID를 운영 승인으로 재사용하지 않는다.

허용:

- 고정 `/opt` 소스·비-editable 환경, `/etc/marketdata/checkpoint-clock-retirement` 설정,
  `/var/lib/marketdata-checkpoint-retirement` 상태, 비활성 삭제 승인과 시스템 유닛 설치.
- 기존 집합의 불변 baseline과 최초의 유효한 사후 완료 상한 등록. 같은 ID·manifest와 연결.
- 사용자 backup/restic의 ExecStart 두 곳만 전환. 기존 시작 검사·timer·manager·기타 옵션 유지.
- 기존 코드/설정/자료를 보존한 실제 설치·권한·연결 확인과 필요시 두 명령만 원복.

허용하지 않음:

- checkpoint/원문/restic 데이터 삭제, 삭제 승인 활성화, 정리 timer 활성화.
- Chrony/수집 중지·재시작, 시각 step, RTC·Windows·boot ledger 변경·재등록.
- 조건을 앞당기기 위한 추가 백업, 기존 completed_at·manifest·과거 근거 수정.

기존 사용자 정리 service/timer와 새 시스템 자동 정리 service/timer는 계속 persistent masked다.
실제 삭제와 반복 자동화는 각각 별도 승인 단계다.
