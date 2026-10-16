# T15 시스템 manager 이전 운영 검증 완료

**2026-10-13 22:12 KST, 합의한 T15 1단계의 운영 검증을 완료했다.**
00:32:58 이전 이후 남겨 둔 정오 daily 실제 처리와 정기 backup → T05 완료/순서 근거 →
system restic 저장·전체 검사·근거 bundle 연결을 확인했다.
[최종 판정과 근거 해시](completion.json), [이전 적용·초기 8개 업무 확인](../scheduled-system-deploy-2026-10-13/status.md)을 함께 따른다.
업무별 시각 정책 변경·polling 복원·T05 삭제 활성화는 완료 범위에 포함하지 않는다.
아래 시각은 KST이며 JSON 원본은 UTC다.

## 정오 daily의 실제 처리

| 확인 | 실제 결과 |
|---|---|
| system invocation / UID | `29da05b7ced34a12a98de652f09bfad7` / 1000 |
| 실행 | `3e9b9c059eaf4624902a3c4286efb168`, 12:20:20.254749–12:20:24.460843 |
| 실제 시도 10건 | UTC 10/12 현물 봉·펀딩 acquire 2건, 실제 관측 metadata 2건, 10/9–10/11 봉·펀딩 refresh 6건 |
| 성공 근거 공유 | UTC 10/12의 refresh 2개는 같은 acquire의 성공 근거로 충족. 추가 시도 없음 |
| 원장·자료 | 고정한 시도 10개·관련 작업 12개·실행 기록이 운영 원장과 checkpoint에서 일치. 표준 자료 5,774행과 원문 artifact 15개의 연결·해시·내용 대조 통과 |
| 실행 후 상태 | `runner_ok=true`, complete 232개·저녁 예정 전 20개. `data_complete=false`는 그 예정 전 범위를 포함한 결과 |
| 12:25 후속 실행 | 새 system invocation `a5c495a11ac04de6a5d1833813df7ce7`, `no_due_tasks`, 처리 0건. 완료·예정 전 상태 유지 |

[작업·시도·저장 근거](noon-ledger.json.gz), [원문/표준 대조](noon-data.json),
[실제 system journal과 원장/보고서 일치](execution-linkage.json)를 보존했다.
시도 횟수 10건과 하위 HTTP 요청 기록 18개는 다른 수치다. metadata는 과거 날짜의 상태를 복구한 것이 아니라
그 실행에서 관측한 자료다. 기존 완료 acquisition·정기 refresh·새 날짜의 acquisition을 구분했다.
정오와 12:25 모두 공개 HEAD 관측기는 disabled·요청 0건이었다.

## 첫 system 정기 backup과 restic

| 단계 | 실제 근거 |
|---|---|
| maintenance | `8e6536f123274b81906a476d3d45646b`, **13:10:51.766298–14:26:29.608191**. 약 75분 38초, 검증 1,703,976개·실패 0개 |
| system backup invocation | `462ef57f62e342f0909a698839176e8a`, UID 1000, journal 최종 내용과 저장 보고서 일치 |
| 새 checkpoint | `reused=false`, 파일 60,248개·45,451,656,804 bytes. DB 기준 구간 14:19:28.288349–14:19:33.460918 |
| system archiver | 예약 실행 `4e629fc0390345c793c94ae0107c4cd5`, **14:35:01.278972–14:44:15.700610** |
| 예약 출처 | `manager=system`, `verified_timer`, 현재 start job 575725·timer trigger·PID 1336890·invocation `8f08dc1a9b884110ab14d47a941544c7` 대조 |
| 실제 restic 저장 | archive `aa7d682ee1b2462cb24df324a0925d33`, 새 snapshot 저장 성공 |
| 기존 정기 전체 검사 | `check --read-data`, **14:44:14.979397 완료**, exit 0·ok=true·read_data=true. 해당 snapshot이 검사 대상 목록에 포함 |

정확한 식별자는 다음과 같다.

```text
checkpoint:
/mnt/backup/marketdata-historical/automatic/2026-10-13/9d4a3f83517044ebaf08b045514f15c5
backup ID:
10dd21822fcc0371772f3cbd3dc5b0ec0a8d086829fb78e1e0a1cab023685665
manifest SHA-256:
d8395deaa2b4a10ceb2e2771df01d2b6bde4025312c55e3f647b9b4ccb13eb5b
restic snapshot ID:
a2dbcdd442459fbde79cb29a7244e3acf95f40c5ac69e37d5b3337a16adba378
```

[maintenance 원본](maintenance.json), [예약 실행 receipt](scheduled-archive.json),
[같은 backup ID의 저장·전체 검사 근거](archive-evidence.json),
[실제 전체 검사 stdout](scheduled-full-check.stdout)을 연결했다.
snapshot의 시각은 기존 정책의 checkpoint 기준 시각이며 restic 저장 완료 시각과 다르다.
보고된 예약 실행 성공만으로 판단하지 않고 실제 새 snapshot과 검사 결과까지 대조했다.

## T05 완료·순서 근거와 지정 snapshot 조회

완료 객체 `6fb5fa7aab402f81ebed8c9db1f4648a3ac2ffa78422cfb932425fb2cfdb1b39`는
`after_completion`, `clock_error=null`이다. 실제 완료 표시 확정 뒤 얻은 관측의
**완료 상한은 14:26:29.724714 KST, U는 0.1382557355초**다.
이 값은 manifest의 `completed_at`을 고쳐 쓴 값이 아니며 조건부 시각 근거다.

- 기존 baseline `5ec056db51157f58a4537cabf374eee65148dfd901007c1138c1cba481b6cf0b` 유지.
- 10/12 객체 `b976e8b04f2bfccbf68a2bc988faac2ed95c1e5dc9172a512396fa9f70dc9192`를 선행 관계로 연결.
- 기존 객체 9개를 그대로 보존하고 새 완료 객체 1개를 추가. 기존 사후 상한·등록·보호 시작점을 재설정하지 않음.
- 현재 승인된 bootstrap/ledger/binding에 연결된 관측임을 확인.

지정 snapshot에서 **29,825 bytes의 `/checkpoint-evidence.json`만 읽어**
로컬 sidecar와 SHA-256 `95fed71989ea0f5a8c18b6b97f2a54bc0f70747b2e2574e938339e8fb7a24192`가 일치함을 확인했다.
복원된 bundle의 객체 10개와 그 해시만 사용해 완료 상한·선행 관계를 재구성했다.
이 재구성은 원래 registry나 선행 checkpoint 폴더를 읽지 않았다.
[명시적 snapshot 조회·재구성 결과](snapshot-bundle-verification.json)를 보존한다.

이번 읽기는 근거 bundle의 선택 조회다. 시장 자료 전체 복원이나 새 전체 읽기 검사를 반복하지 않았다.
정오 자료는 해당 checkpoint의 원문/표준 파일·카탈로그에서 검증했고,
그 checkpoint와 같은 backup ID의 restic 저장·전체 검사 근거를 연결했다.
이미 완료한 T13 대규모 복원 범위를 다시 열거나 저녁 자료의 다음 백업까지 범위를 늘리지 않았다.

## 현재 실행 환경과 검증 한계

[22:06 실효 상태](host-state.json)에서 승인된 구성 46개·ledger·실효 유닛 일치,
user 서비스/timer 16개 masked·유휴, system timer 8개 enabled·active를 다시 확인했다.
boot/closed/보정 증거, Chrony PID 1245와 두 supervisor PID 224785/224784·invocation을 유지했다.
자동 정리 네 mask와 공개 관측기 비활성도 유지했다.

OI 3종·network·pack 실제 처리와 같은 supervisor의 다음 자연스러운 child 경계는
[초기 운영 근거](../scheduled-system-deploy-2026-10-13/status.md)를 재사용한다.
01:26 pack 잠금 경합과 01:29:41 자동 재시도 회복도 그 기록에 남아 있다.
이번 자료 대조는 불변 checkpoint를 사용했고 운영 시장 파일의 layout 잠금을 잡지 않았다.

[읽기 전용 검증기](verify.py), [호스트 조회기](capture_host.py),
[저장된 기록의 최종 연결 검사](finish_review.py)의 결과를 보존했다.
긴 JSON journal 메시지는 `journalctl --all`로 다시 읽은
[정확한 invocation 원본](target-invocations-full.jsonl.gz)과 [내용 대조](execution-linkage.json)를 기준으로 삼았다.
앞선 [로컬 검증](verification.json)의 `explicit_snapshot_bundle_read_pending=true`는 그 단계 당시의 상태이며,
후속 snapshot 조회 통과와 [최종 판정](completion.json)으로 해소했다. 원본 중간 결과는 수정하지 않았다.

## 완료 범위와 별도 후속

T15의 **기존 정책을 유지하는 시스템 manager 이전**은 구현·격리/관리자 시험·승인된 적용·
8개 업무 실제 실행·정기 backup/T05/restic 연결·수집기 유지 확인까지 완료했다.
활성 T15는 [TODO](../../../TODO.md)에서 정리하고 아래 범위는 남긴다.

- **T05:** 신규 후행 사본은 현재 **2개**다. 3개·보수적 72시간+60초·당일 완료/보존/검사 조건 이후
  새 미리보기와 별도 수동 삭제·반복 승인이 필요하다. 자동 정리는 계속 masked다.
- **업무별 시각 요구 분리:** TODO의 조건부 후속. strict gate와 600초 조건을 이번에 변경하지 않았다.
  `reference_stale` 사용처 정리와 polling 복원도 별도 검토·적용 범위다.
- **실제 재부팅:** 같은 부팅의 이전을 확인했으며 새 구성으로 재부팅을 실행한 결과로 확대하지 않는다.
- **현재 품질:** 기존 OI 품질 경고·과거 collection 이력은 보존한다. 관리자 이전 성공을 모든 자료의 정상 품질이나
  reference age 대기의 해결로 표시하지 않는다.
