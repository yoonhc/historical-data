# T05 mount 수정·한 계획 재개 상태

**최신 상태 — 10/17 20:32 첫 수동 정리 완료, 후속 독립 대조 통과.**
승인 operation `821367d2…`의 실제 run `f57f334be39e4137a4b7785836b6d2bd`가
원래 plan `31766af4…`의 10개를 `complete`/exit 0으로 정리했다. 33분 51초가 걸렸고
현재 남은 사본은 보호 3개·제외 3개다. 원래 plan·inventory·실패 이력을 유지하며 journal에 성공 1개만 추가했다.
관측된 여유 공간 증가는 약 28.20GB다. manual 원본 비활성 복원·recurring 비활성·네 mask·기존 근거와
수집기/Chrony 신원·보호 입력 보존을 실제 호스트에서 확인했다.
**첫 수동 적용 완료이며 반복 자동화·T05 전체 완료는 아직 아니다.**
[완료 보고서](completion.md), [독립 감사 원본](completion-audit.json), [root 결과](completion-evidence/root-result.json).
아래는 이 성공 이전의 실패·승인·재개 준비 이력이다. 기존 설치/apply/resume 명령을 다시 실행하지 않는다.

**10/17 18:47 네 파일 수정 완료, 이어진 정리는 repository lock Busy·삭제 0개로 실패했다.**
새 run은 `2d9f7b8b4a434183a875dfa441171295`다. 공통 부모 mount와 비활성 recurring scope는
승인한 after로 설치됐으며 scope는 `84a0b92ae6f8bf0e38ac10ecb4a2615a32af72aebf1c8c28d591c02087cf4983`다.
기존 설치 operation `eced87a499269ebe5da84be1b0795ebfd2c109ce06f37db4e5e26f8027b9e231`의
시작 intent는 이미 사용됐다. **`repair_and_resume.py --action apply`를 반복하지 않는다.**

## 실제 실패와 보존 대조

| KST | 원본에서 확인한 동작 |
|---|---|
| 18:47:08 | 수동 정리 service 시작 |
| 18:47:12 | restic service 시작. reload 뒤 timer의 `OnActiveSec=5s` 요청과 연결됨 |
| 18:47:27 | 작은 근거 bundle 12회 조회 후 repository lock 획득 거부, exit 1 |
| 18:47:50 | restic service 정상 종료 |

설치 실행기는 reload 직후의 유휴·잠금만 확인해 5초 뒤 시작할 예약을 놓쳤다.
이번 원인은 첫 rename의 EXDEV가 아니라 잠금 입장 전 경합이다. 실제 rename에 도달하지 않았으므로
이번 실행을 운영 rename 성공으로 표시하지 않는다. `failed` 표시는 기존 미완료 journal을 발견한 종료 처리의 결과이며,
이번 실행이 journal에 새 삭제 시도를 추가했다는 의미가 아니다.
[업무 run](postapply-busy-evidence/run.json), [root 결과](postapply-busy-evidence/root-result.json),
[manager journal](postapply-busy-evidence/manager-journal.jsonl), [timer 설정·실효 시각](postapply-busy-evidence/restic-timer.txt).

18:52:34 실제 호스트의 [읽기 전용 감사](postapply-busy-audit.json)에서 다음을 확인했다.

- 네 파일 모두 정확한 after, root:root/0644 일반 파일, UID 1000의 설치 guard 통과.
- 원래 journal SHA-256 `b197674e221643d5e1fce0535865e03f937c29358b512d4f1884d83113a0a740` 동일.
  과거 EXDEV 시도 1개·10개 prepared·unlink 0·staging 비어 있음.
- 원래 사본 16개 manifest·후보 root·inventory 10개·원래 계획 동일. 유지 자료 전체 검사를 새로 반복하지 않았다.
- 88개 보호 입력·기존 근거 15개·Chrony/두 supervisor 신원·네 mask 유지.
  manual은 원래 비활성으로 복원됐고 recurring도 비활성이다.

## 재설치 없는 한 번 재개안

새 [재개 계약](resume-authorization.md)과 [고정 계획](resume-only-plan.json)은 **명시적 승인을 받았다**.
해시로 고정한 제안 문서는 그대로 보존하고 승인 사실은 [별도 기록](resume-approval-received.json)으로 연결한다.

- operation: `821367d24e43bd2423da568dfbca8cab47ac9c40fe405f09feadea567bec4824`.
- 도구 SHA-256: `b55955e9d5c436905df9b7fbc5ae800ad47b804286e0ca82f5cf64a3a0b2da85`.
- 원래 삭제 plan: `31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08`.
- 새로 검토한 Busy run: `2d9f7b8b4a434183a875dfa441171295`.

`resume_only.py`는 설치·daemon-reload를 하지 않는다. 이미 설치된 after와 정확한 네 과거 run,
원래 EXDEV journal·inventory·사본을 대조하고 동일한 10개 대상만 한 번 재개한다.
현재 pack/backup/restic 및 대기 job·잠금에 더해 세 timer의 다음 realtime/monotonic 기동을 조회한다.
60초 이내 예약이면 grant 활성화/서비스 요청 전에 보류한다. 이는 약 18.8초였던 시작 전 근거 조회의
입장 여유이며 T05의 시각 정확도·보관 기간 변경이 아니다. 외부 수동 시작까지 막는 예약 잠금은 아니며
실제 엔진은 동일한 잠금을 계속 직접 획득해야 한다.

새 격리 검사 **11개 통과**: 실제 예약 경계 대역, 정확한 Busy 한 건만 허용, 시작 intent 선기록,
worker 시작 전 runner lock 해제, 모호한 start 응답·중복 시작 거부, 가까운 예약이면 grant 전 거부.
[결과](resume-tests.xml), [검증 범위·해시](resume-validation.json).
원래 27개 검사에서 통과한 설치 T05·작은 restic·journal 재개·finalizer·원복 근거는 동일 코드로 재사용하며
이번 실행 수에 합산하거나 재실행했다고 표시하지 않는다.

**19:07 KST 실제 호스트의 새 도구 `--action check` 통과:** 네 after 파일·guard·원래 journal/사본·시각·유휴/잠금과
실제 bus timer 시각을 확인했다. 다음 restic 19:35·pack 19:45, backup 10/18 13:10이었으며
설정/grant 변경·reload·서비스 요청·삭제는 없었다. [원본 출력](resume-native-check.json).
실제 apply는 실행 직전에 같은 조건을 다시 확인한다.

승인 뒤 사용한 [재개·종료 처리 명령](resume-commands.md)은 완료됐으며 반복하지 않는다. 이번 실패까지 연결한 한 번 재개였으며
기존 계획·보관/시각/당일 backup-restic·무결성·잠금 조건을 유지한다.
긴 검증은 생략하지 않으므로 수십 분 걸릴 수 있다. 실패 또는 부분 진행이면 다시 자동 요청하지 않는다.
이 준비 단계에서는 첫 수동 정리가 미완료였으며 이후 성공은 위 완료 보고서를 따른다. 반복 자동화·T05 전체는 미완료이고 네 mask를 유지한다.

이전 승인 및 sudo 인증 대기 기록은 [승인 기록](approval-received.json),
[직전 대조·인증 원본](approved-preapply-check.json)에 보존한다.
