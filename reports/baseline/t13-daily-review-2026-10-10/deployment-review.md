# T13 보완 후보 적용·재개·원복

작업 시작 2026-10-10, 최종 검증 10/11 KST. **이 검토 당시 운영 설치·daily timer 변경은 미승인·미실행**이었다.
이후 별도 사용자 승인으로 10/11 운영 적용·첫 실행을 완료했다.
현재 상태와 중단/재개·원복 근거는 [실제 적용 기록](../t13-daily-deploy-2026-10-11/summary.md)을 따른다.
적용 대상은 이 디렉터리의 [manifest.json](manifest.json)에 고정한 최종 release다.
기존 `89912177f7b3adad`와 개발 중 임시 후보는 보존하며, 승인·검증 범위를 새 코드에 자동 승계하지 않는다.

## 구현한 재개 계약

`marketdata.daily_review`는 `inspect`와 `apply`를 제공한다. apply의 요청은 `reconcile` 또는 `retry`다.
운영 저장소의 개별 작업 재개는 아직 0건이며, 아래 예시는 배포 후 **점검한 개별 작업의 명시적 재개**에 사용한다.
스케줄러 반복 실행 승인은 모든 needs_review의 자동 해제 승인으로 해석하지 않는다.

| 동작 | 결과 |
|---|---|
| reconcile | 해당 작업만 저장·검증 근거로 완료 연결. HTTP 0건. acquire는 해당 범위의 완전한 저장 근거, refresh는 해당 확인 주기의 실제 원격 조회·성공 publication·현재 자료 일치까지 확인 |
| retry | 해당 작업만 추가 1–3회 작업 시도를 허용하고 retry_wait로 전환. 바로 HTTP를 보내거나 완료로 만들지 않음 |

작업 시도는 HTTP 요청 1회와 다르다. 한 작업 시도 안에서 기존 HTTP 계층의 제한된 재시도가 유지된다.
기존 실패·시도 횟수는 누적 보존하며, 추가 허용분은 `manual_retry`와 attempt의 `manual_request_id`로 구분한다.
준비 거부 또는 **요청 전 이미 알려진 호스트 냉각**은 추가 예산을 쓰지 않는다. 실행 시도를 원자적으로 기록한 뒤에는
404·새로 받은 429·검증 실패·중단도 1회로 센다. 결과가 불명확한 중단을 이유로 예산을 되돌리지 않는다.
추가 한도 소진 시 다시 needs_review가 된다. 충돌·영구 오류는 잔여 한도가 있어도 즉시 점검 대상으로 남을 수 있다.

기존 `daily_cooldowns`와 최초 실행 시각·strict gate·검증 기준·동일 대상의 미완료 의존 관계를 유지한다.
다른 미해결 acquire가 refresh를 막으면 재개 승인 뒤에도 그 관계를 건너뛰지 않는다.
과거 날짜의 metadata 재수집은 거부한다. 이미 실제로 관측·저장된 그 날짜의 근거가 있으면 reconcile만 가능하다.

### 실제 사용 절차

1. 새 release의 Python으로 작업을 조회하고 원인·자료·냉각·범위를 점검한다.

```text
<release>/.venv/bin/python -B -I -m marketdata.daily_review inspect --root <운영-root> --task-id <정확한-작업-ID>
```

2. 반환된 task와 `expected_task_sha256`를 검토하고 요청 파일을 고정한다.

```json
{
  "request_id": "daily-review-20261011-001",
  "task_id": "조회한 64자리 작업 ID",
  "expected_task_sha256": "조회한 64자리 작업 상태 해시",
  "action": "retry",
  "reason": "확인한 원인과 조치, 이 작업의 추가 재시도를 허용한 사유",
  "extra_attempts": 1
}
```

완료 연결은 `action="reconcile"`, `extra_attempts=0`을 사용한다. 요청 ID는 ASCII 영문·숫자·`_.-` 1–128자로 정한다.

3. 승인한 요청을 적용한다.

```text
<release>/.venv/bin/python -B -I -m marketdata.daily_review apply --root <운영-root> --config /etc/marketdata/daily-scheduler.toml --request <고정한-요청.json>
```

처리 기록은 `daily_reviews`에 요청·UID·설정 해시·처리 시각·변경 전후 작업 전체를 보존한다.
작업 상태·추가 예산·처리 기록은 같은 SQLite transaction에서 확정한다. 동일 요청 반복은 기존 처리 기록을 반환하며
예산을 늘리거나 진행된 작업을 이전 상태로 되돌리지 않는다. 같은 ID의 다른 내용은 거부한다.
조회 이후 작업 또는 설정 범위가 바뀌면 새 상태를 다시 검토한다. scheduler 잠금 경합은 종료 코드 75로 보류된다.
명령 응답이 유실돼도 동일 요청을 재실행해 이미 확정된 결과를 확인할 수 있다.

4. inspect로 처리 기록을 확인한다. retry 후 실제 자료 완료는 이후 scheduler 결과로 별도 확인한다.
기존 status/JSON 집계는 다음 scheduler 평가에서 갱신될 수 있으므로 즉시 확인에는 task와 처리 기록을 사용한다.

### 저장 근거 부족·충돌의 경계

refresh는 로컬 `checked_at`을 원격 확인 시각으로 쓰지 않는다. 정확한 범위의 published job,
해당 시기 실제 fetch, ZIP/CHECKSUM 또는 REST 페이지·종료 근거, 현재 자료와의 경제적 내용 일치를 대조한다.
동일 파일을 다시 받았어도 새 fetch와 성공 publication을 입증할 수 있으면 인정한다.
다운로드만 끝난 중단·과거 파일만 존재·현재 blocking issue·불충분한 이력은 거부한다.
이 도구는 충돌 자료를 승인하거나 검증 기준을 무시하는 명령이 아니다. 근거 부족이면 별도 원인 조치 후
새로 검토한 retry 또는 기존 자료 처리 절차를 사용하며, DB 직접 편집으로 통과시키지 않는다.

## 미리보기

```text
<release>/.venv/bin/python -B -I -m marketdata.daily_preview --source-root <운영-root> --destination /tmp/<존재하지-않는-전용경로> --config <검토할-설정.toml>
```

원본 연결은 `mode=ro`, `query_only=ON`이다. Online Backup API로 WAL의 커밋 내용까지 포함한 일관된 사본을 만들고,
계획 계산용 사본을 별도로 둔다. 변경되는 원장·scope·coverage, 잠금·JSON·journal은 시험 경로에 둔다.
원본의 기존 file-layout 잠금은 읽기 전용 FD로 열어 공유 잠금을 유지한다. 원문은 전체 복제하지 않고
그 잠금 안에서 기존 packed/loose·파일 해시 계약대로 읽는다. 원본 잠금이 없거나 pack 변경과 경합하면 보류한다.

SQLite 정렬용 임시 저장소는 미리보기 연결에서 MEMORY로 설정한다. 카탈로그 전체를 메모리에 올리는 방식은 아니다.
원본 SQLite의 WAL/SHM 열기와 동시 읽기를 위한 SHM 조정은 SQLite의 읽기 프로토콜에 따른다.
**운영 파일에 어떤 접근도 없다는 보장이 아니라, 운영 업무 데이터·원장·schema를 수정하지 않는 절차**다.
실제 파일 접근 검사 결과는 [preview-access-check.json](preview-access-check.json)을 따른다.

`plan()` 자체는 쓰기 함수이므로 운영 Store에서 직접 호출하지 않는다. 사본을 운영 DB에 되돌리지 않는다.
출력은 해당 as_of와 사본의 실행 자격을 보여 주며, 후속 다운로드 결과·처리 순서별 HTTP 건수·적용 시점의 상태를 예측한 것이 아니다.
실제 첫 실행에서 최신 자료·시각·냉각·작업 상태를 다시 판단한다. 이 미리보기 사본은 원문이 포함된 백업이 아니다.

## 운영 적용 범위

기존 [daily 적용·원복안](../t13-daily-scheduler-2026-10-10/deployment-review.md)의 서비스 범위를 유지하되,
release 경로는 새 manifest ID로 교체하고 아래 추가 파일·테이블을 포함한다.

- 고정 release: 기존 scheduler/probe와 새 `daily_review.py`, `daily_preview.py`. 개발 폴더와 분리된 비-editable 설치.
- 설정: `/etc/marketdata/daily-scheduler.toml`. 이전 후보의 일정·HTTP 정책·대상 범위와 바이트가 같다.
- 사용자 base `marketdata-daily.service`, `marketdata-daily.timer`만 전환. root clock drop-in의 기존 ExecStartPre 전체를 유지한다.
- 승인된 첫 실행 때 기존 5개 daily 테이블과 `daily_reviews`를 운영 카탈로그에 생성한다. 기존 자료·실패·revision은 삭제하지 않는다.
- 공개 관측기는 `enabled=false`. 두 실시간 수집기, Chrony/RTC, bootstrap ledger, pack/backup/restic, 자동 정리 mask는 전환 대상 밖이다.

실제 적용 전 현재 유닛·해시·작업 상태를 기록하고, daily timer만 보류한 뒤 진행 중 daily의 정상 종료를 기다린다.
고정 /opt 설치·import/권한·실효 유닛·guard를 확인하고 새 daily만 기동한다. 기존 보조 검사를 제거해 통과시키지 않는다.
시스템 manager 이전과 업무별 시각 정책 확대는 이 변경에 넣지 않는다.

## 원복과 남은 운영 검증

원복은 이전 daily base service/timer와 실행 경로만 복원한다. 새 테이블·처리 기록·원문·revision·오류 자료를 유지하고,
카탈로그를 과거 사본으로 덮어쓰지 않는다. 새 조회·재개 이력을 읽을 수 있는 고정 후보를 보존한다.
이전 일괄 실행기는 새 냉각·needs_review·추가 한도를 자동으로 따르지 않으므로, 원복 후 즉시 일괄 재실행해 우회하지 않는다.
현재 제한 상태와 대상 범위를 점검한 뒤 기존 작업의 재개를 결정한다.

이 문서의 최초 검증 범위는 후보·격리 검사·운영 사본 미리보기까지다. 이후 실제 /opt 설치·실효 유닛 실행과
첫 실행/처리 0건은 위 적용 기록에서 확인했다. 정오·저녁·날짜 이월, 새 원장과 처리 기록의
운영 checkpoint/restic 편입·복원 조회는 아직 남아 있다.
공개 관측기 활성화와 자동 checkpoint 삭제 재개는 별도다.
