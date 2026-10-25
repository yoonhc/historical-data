# T16 1차 정책의 운영 검증 완료

**2026-10-18 23:43 KST, 합의한 T16 1차 범위의 운영 검증을 완료했다.** 유효한 현재 Chrony 관측 아래 reference age만으로 8개 예약 업무를 일괄 차단하지 않는 정책, Decimal 수정과 정확한 요청 복구, 실제 daily 처리, 정기 backup → T05 완료·순서 근거 → system restic 저장·전체 검사를 연결했다. 현재 시각 근거가 없는 진단, 수집기/부팅의 남은 600초 의존성, polling 복원과 보조 기록의 독립 복구까지 완료했다는 의미는 아니다.

이번 점검은 운영 읽기 전용이다. 새 수집·백업·전체 무결성 검사·restic 조회/복원·서비스 제어·삭제·시각 변경을 실행하지 않았다. 정오/저녁의 정확한 원장·입력·revision 76개 파일, 합계 21,541,981바이트의 해시는 별도 대조했다. 이는 전체 저장소 검증이 아니다. 관측은 파일별 읽기 시점이며 모든 운영 상태의 동시 snapshot으로 해석하지 않는다.

## 완료 기준과 실제 근거

| 합의한 항목 | 확인 결과 |
|---|---|
| 수정 실행본과 운영 구성 | `scheduled-clock-policy-20261016-686bb70c9ee4ef11`, T16 구성 13개의 승인 after·root 소유권·모드 일치. 8개 시스템 timer enabled/active, 각 service의 UID 1000·고정 variant·새 waiter 연결 확인 |
| 요청 종결과 저장 근거 | 23:37 KST 읽은 8개 현재 request 모두 complete. 설치된 읽기 전용 계약으로 설정/코드 scope·보고서/DB행/JSONL 구간·업무별 종료 판정 재대조, 감시 종료·오류 없음 |
| 정오 실제 daily | 10/17 12:20:14–12:20:20 KST, 시도 10개·HTTP 18건·관련 작업 12개 완료 |
| 저녁 실제 daily | 10/17 18:00:10–18:00:21 KST, 시도 15개·HTTP 30건·관련 작업 19개 완료 |
| 불필요한 반복 방지 | 12:25와 18:05 다음 실행은 시도 0건. 선택한 31개 작업의 완료와 저장 근거 유지, 처리한 각 작업의 직접 시도는 1회. 같은 성공으로 완료된 refresh는 별도 시도 없음 |
| age 600초 초과의 실제 처리 | 저녁 실행의 유효 관측에서 reference age **830.004초**, U **0.138861초**. 600초만을 이유로 차단하지 않고 실제 자료 처리 |
| 정기 backup/T05/restic | 10/17 새 checkpoint·완료 객체와 같은 backup ID의 snapshot·전체 읽기 검사·system timer 출처·wrapper 종결 확인. 기존 직접 조회 bundle도 재사용 |
| 기존 운영 보존 | 같은 boot/closed ledger·Chrony·두 supervisor PID/invocation 유지. T05는 별도 승인된 현재 활성 상태와 10/19 15:40 KST 예약 유지 |

[현재 요청 대조](current-requests-review.json), [host 구성·유닛·권한](host-review.json), [host 명령 원본](host-commands.json), [통합 대조](integration-review.json)에 결과가 있다. 설치 5,523개 객체·세 variant import 및 초기 자연스러운 collector child 경계는 [10/17 적용 완료 근거](../scheduled-clock-repair-deploy-2026-10-16/status.md)를 재사용했다. 이번에 전체 설치 시험·child 시험을 반복한 것으로 세지 않는다.

## daily의 정확한 범위

| 구간 | daily run | wrapper request / invocation |
|---|---|---|
| 정오 | `209157f2a455427a94ab15033958d92a` | `a72331c7e2904ce1966e107374f7af58` / `74ec8a85ac104c39bf514d51d9164eb3` |
| 저녁 | `f27385967718423dbe93f1360ca054fd` | `85525dd1f0324760ae06c1c7950ef3e6` / `edc6b2a1f2f6423c8576caadb963ddab` |
| 정오 후 재평가 | `35f3459b23194203a687a45989589e66` | invocation `79f3b3e8efc749469761162ccdd495ef` |
| 저녁 후 재평가 | `4c2d80de76d04b859d074083ad63b61e` | invocation `8b629cda48e84f8e873de3b893338849` |

정오는 acquisition 2개·metadata 관측 2개·refresh 6개를 실제 처리했고 같은 성공으로 refresh 2개도 완료했다. 저녁은 acquisition 4개·refresh 11개를 처리했고 refresh 4개를 함께 완료했다. acquisition은 UTC **10/16**, metadata는 **10/17 실제 관측**, refresh는 KST **10/17 확인 주기**와 해당 과거 UTC 자료 날짜를 각각 유지한다. 처리 결과 행 합계 23,918에는 기존 자료의 refresh가 포함되므로 신규 추가 행 수로 표시하지 않는다.

25개 attempt·31개 관련 task·25개 query·25개 revision과 51개 원문 artifact의 참조 및 파일 해시를 대조했다. 실제 판단 하한이 최초 실행 가능 시각 이후인지 확인했고, [system journal](daily-noon-system-journal.jsonl)과 [저녁 journal](daily-evening-system-journal.jsonl)의 같은 invocation·UID·고정 명령·job 완료를 wrapper 결과와 연결했다. 원문 MESSAGE가 큰 journal 항목은 생략될 수 있으며 stdout 전체 비교를 했다는 주장은 하지 않는다.

[원장과 정확한 ID/해시](daily-records.json), [처리·주기·중복 대조](daily-review.json), [파일 해시](selected-file-hashes.json), [wrapper 대조](daily-wrapper-review.json), [선택 요청의 판정 로그](daily-decisions.json)를 보존했다. 10/18 새 확인 주기의 metadata 2개·refresh 23개 완료는 별도 작업이며, 기존 acquisition을 취소하거나 다시 처리한 것이 아니다. 전체 원장 작업 수를 과거 값으로 고정하지 않았다.

## 기존 OI 점검 필요는 별도 후속

23:35 실행은 **408 complete·1 needs_review·3 planned**, `runner_ok=true`, `data_complete=false`다. 남은 acquire는 UTC **10/15 BTCUSDT 선물 OI archive 5m**, task `c24159c0b9f2893037ac67c41e52d5bbdfd0a9ed2c3f62ecdae43d6c7b32b6e9`다.

실패 시도 `0af32466cb6e44efbee0fe5ba85c5282`와 `39f32b6665984af28a7e3b84e73b13da`는 **10/16 18:00·20:00 KST**, 최초 T16 적용 전이다. 사유는 `Task has no complete verified publication proof`, 검증 실패 2회 후 needs_review이며 [원래 기록](preexisting-oi-attempts.json)을 유지했다. 10/17 00:10 수정 전 보고서에도 동일 상태가 있다.

동일 대상의 10/16·17·18 refresh 3개는 기존 scheduler의 미완료 acquisition/점검 필요 보호 때문에 대기한다. 따라서 저녁 시도는 16개가 아닌 15개다. 새 시각 정책이나 Decimal 수정의 장애로 분류하지 않으며, 자료가 완료됐다고도 표시하지 않는다. **TODO의 T17에서 원문·범위·저장 근거를 별도로 조사한다.** 이번에는 해제·재시도·재수집하지 않았다. OI live의 partial과 품질 보고서의 needs_review도 유지했다.

## backup → T05 → restic

| 근거 | 정확한 값 |
|---|---|
| maintenance | `1de6965ae3844c999a672653571a5c8c`, 10/17 14:46:36 KST 완료, 검증 2,044,850건·실패 0 |
| backup ID | `9b916470eca7e5da1462dff9c39380869242bf6711861eaea754c010ccf00963`, `reused=false` |
| backup wrapper | request `ea8e68bd64d743f5b3ae5b080c0f318c`, invocation `fda07748026c4dfd8a2e7d0a1544075b`, complete/0 |
| T05 새 완료 객체 | `2e242b0a5af450545f5dd7277d7eb4444de3f3cc854d256cd83705363380b92f` |
| restic snapshot | `0911b25d3a270fad604eeb222748e950ea45383b68d9b451e6d785b92672c58f` |
| archive run / invocation | `6845ad8849f2414aac798369806f6310` / `e0860d76785b4c7cb3dff705ffddb60c` |
| 저장·전체 검사 | 10/17 15:14:42 저장, 15:17:01.938 전체 `read_data` 검사 완료, 오류 0·exit 0 |
| bundle SHA-256 | `63fe236673bcb857908c3e3e3e7068abfd6bfc4a7d143753478dd5326a12d1fb` |

[10/17 완료 대조](../t05-disabled-scope-2026-10-17/backup-archive-status-20261017.md)의 원본 보고서·marker·request 결과와 전체 검사 stdout/stderr 해시를 다시 연결했다. [정식 미리보기에서 직접 읽은 snapshot bundle](../t05-formal-preview-2026-10-17/verification.json)은 42,618바이트·객체 14개·선행 관계 12개이며 이번에는 그 기록과 보존 바이트 해시를 재사용했다. 기준 baseline과 완료 상한·생성 순서를 유지한다.

이 checkpoint는 저녁 자료 처리 전 생성된 지점이다. **10/17 저녁 자료가 이 snapshot에 포함됐다고 주장하지 않는다.** T16의 합의 기준은 실제 daily 처리와 새 wrapper의 정기 보존 연결이며, 고정한 저녁 자료의 새 대규모 복원을 추가 완료 조건으로 만들지 않았다. T13에서 완료한 별도 복원 계약도 다시 열지 않았다.

## 실패 이력과 자연 재개

8개 업무의 기존 JSONL prefix에서 `request_finished`를 대조했다. 알려진 Decimal 직렬화 실패 `a702240f…`는 그대로 남아 있고, [기존 복구 receipt](../scheduled-clock-repair-deploy-2026-10-16/operating-evidence/recovery-receipt.json)의 업무 호출 0회·원래 scope/pins·과거 exit 1 보존을 완료 근거로 사용한다. 현재 품질 요청도 비어 있지 않은 저장 결과와 일치하며 새 직렬화 실패는 확인되지 않았다.

추가로 발견한 두 restic 종료는 [실제 journal](restic-lock-system-journal.jsonl)의 `lock_busy`다. 10/17 **17:05 → 17:35**, **20:05 → 20:35**의 다음 system timer에서 같은 request·identity·scope·pins로 재개돼 complete/0으로 종결됐다. 요청은 각각 `3ec22a5fbf1b418e8c97e0034f7e57f7`, `475cba150a7647abbd9d55535118f479`다. 후속 실행은 processed=0이며 새 backup 성공으로 세지 않는다.

현재 wrapper의 원래 표기는 두 번 모두 **failed/75, reason=work_failed**이고 감시·기록 errors는 없다. 이를 정상 완료나 `deferred`로 고쳐 쓰지 않았다. [실패·후속 attempt 원본](restic-lock-recovery/)과 [종료 이력 대조](terminal-events-review.json), [같은 요청의 복구 검증](integration-review.json)을 연결했다. 상태 표시를 더 명확히 나누는 검토는 TODO 조건부 후속으로 남기며 이번에 코드를 바꾸지 않는다. 이 검사는 기록된 종료 이력에 한정하며 기록 없이 중단된 모든 사건의 부재를 증명하지 않는다.

## 완료 밖의 범위와 다음 작업

T16 활성 항목은 이 보고서와 [이전 TODO 이력](todo-t16-history.md)을 남기고 제거한다. TODO의 조건부 목록에 **현재 시각 미확인 진단/독립 status, 수집·부팅의 남은 600초 의존성, polling 복원, 보조 request/attempt/JSONL 백업·회전·독립 복구, restic 잠금 보류의 표시 구분**을 유지한다. T14 계측도 별개다. 호스트 내 복구 성공을 백업에서의 독립 요청 복구 보장으로 확대하지 않는다. 새 구성의 실제 재부팅 시험은 이번 범위에 없다.

**T05는 별도 승인된 설치·활성화 상태를 유지했다.** active recurring 해시 `3b436c4f…`, 시스템 timer 활성·다음 **2026-10-19 15:40 KST**, 사용자 자동 유닛 두 mask·manual 비활성이다. 최초 예약 평가와 자연스러운 자동 삭제/보존 관찰은 여전히 T05의 미완료 항목이다. T16의 과거 네 mask 기록을 현재 상태로 잘못 적용하지 않는다.

재현용 읽기 전용 대조기는 [자료·요청 capture](capture_readonly.py), [실제 host 조회](capture_host_readonly.py), [기존 근거 연결](reconcile_evidence.py)이며, 보고서 파일만 쓴다. 이번 실행의 원본 사본과 참조 파일 해시는 [source 목록](source-copies.json), [통합 근거](integration-review.json), [보고서 artifact 목록](artifacts.json)에서 확인할 수 있다.
