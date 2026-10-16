# T13 고정 범위의 운영 검증 완료

**2026-10-12 17:16 KST, T13 운영 검증을 완료했다.** 다음 KST/UTC 경계와 10/11 정오 10건·저녁 16건의 후속 checkpoint/restic 편입, 명시적 snapshot의 독립 복원·대조가 통과했다.
10/12 저녁 자료는 완료 조건에 추가하지 않았다. 구현·설치·213개 격리 검증을 반복하지 않았고, 이번에는 실제 운영 결과를 확인했다.

기준은 [10/11 고정 완료 계획](../t13-operating-review-2026-10-11/completion-plan.md)과 변경하지 않은 [대상 목록](../t13-operating-review-2026-10-11/completion-targets.json)이다.
Target ID는 `300941a16686697c683a16f39e325cd34b52a5d2fcf77addef7ed8e5eb584ef0`다.
실행본 `t13-daily-scheduler-20261011-703fb0d455523c1b`·스케줄·공통 시각 gate는 유지했다.

## 날짜 경계와 이전 완료 보존

| 실제 runner 시각(KST) | 결과 |
|---|---|
| 10/12 00:24:52 | 새 KST 주기의 refresh 18개·metadata 관측 2개 추가. 기존 complete 188개 유지, 신규 대상은 예정 전이며 처리 0건. |
| 10/12 09:00:12 | UTC 날짜 변경으로 **10/11 UTC** acquire 6개·refresh 6개 추가. 기존 예정 20개와 complete 188개 유지, 처리 0건. |
| 10/12 12:38:00–04 | 당일 정오 10개 실제 시도 완료. 기존 완료 acquisition의 재시도는 없으며 새 refresh·metadata·새 UTC 날짜 acquisition을 처리했다. |
| 10/12 17:13:39 최신 runner 기준 원장 | 작업 220개 = complete 200개 + 저녁 예정 20개. 총 시도 36개 = 고정한 어제 26개 + 오늘 정오 10개. |

`data_complete=false`는 새 예정 작업이 생긴 결과다. 기존 완료 acquisition **162개**의 ID·완료 근거 해시와 고정한 작업 **32개**·시도 **26개**의 기록 해시가 운영 원장과 오늘 checkpoint에서 모두 그대로 유지됐다.
32개에는 동일 acquisition 성공을 공유한 refresh 6개가 포함된다. 이를 별도 요청 6건으로 세지 않는다.
모든 저장된 시도의 시작은 해당 작업의 최초 실행 가능 시각 이후다.

각 경계의 이전/이후 run ID·신규 task ID·목적·실제 시각은 [schedule-review.json](schedule-review.json), 원본은 [압축 원장](ledger-records.json.gz)에 보존했다.
전체 작업 수 188개를 고정하거나 현재 운영 상태를 과거 checkpoint에 강요하지 않았다.

## 고정한 실제 복구 지점

| 항목 | 실제 값(KST) |
|---|---|
| maintenance | `4a5905fc51ab4f8e8165f3f468640dfd`, 13:10:01 시작 → 14:21:46 완료 |
| 카탈로그 기준 구간 | 14:15:07.305502–14:15:12.270618 |
| manifest의 completed_at | 14:21:42.594340 |
| restic 저장 | 14:40:31.231077–14:42:06.135500, 종료 0 |
| 저장소 전체 읽기 검사 | 14:42:09.886951–14:43:39.287237, `check --read-data`, 종료 0 |

- Backup ID: `fd4bf8417a5203f4dfbd467118740ecc2441d642096471737727fcbcdad00fdf`
- Checkpoint: `/mnt/backup/marketdata-historical/automatic/2026-10-12/ea0b6b077d354a9290f2d919522771a9`
- Manifest SHA-256: `d3fd2fe18a7ab3c231d4a6c1277c9ea85084a0d2b18e7e9d582028287ffe7a65`
- Restic snapshot: `616ed4d6dbd3636b1970c5e623e3a770d42d31ae9ee3b9b26102e9675110a470`

[정기 maintenance 원본](maintenance.json), [저장·전체 검사 receipt](archive-evidence.json), [실제 snapshot 메타데이터](snapshot.json), [전체 검사 stdout](scheduled-full-check.stdout)을 연결했다.
성공한 전체 검사 목록에 이 snapshot이 포함됨을 확인했다. 기존 정기 결과를 사용했으며 추가 백업이나 전체 검사를 실행하지 않았다.

## 한 번의 선택 복원과 종합 대조

전체 checkpoint 약 43.12GB 중 이번 계약에 필요한 **82개 파일, 2,679,830,804바이트(약 2.50GiB)**만 선택했다.
카탈로그 전체, manifest, 26개 시도의 원문·CHECKSUM·표준 파일 및 필요한 pack, 같은 지점의 T05 sidecar를 포함한다.

17:15:27–32에 위 snapshot ID를 직접 지정해 `restic restore --verify --overwrite never`로 새 경로에 복원했다.

```text
/mnt/backup/marketdata-restic-restore/t13-completion-20261012-637d2877b36f4c99
```

| 대조 | 결과 |
|---|---|
| 복원 파일 | 선택한 82개와 정확히 일치, 바이트 수·SHA-256 일치, 원본 checkpoint와 다른 inode |
| daily 원장 | checkpoint의 tasks 220·runs 310·attempts 36·cooldowns/reviews/probe_targets 각 0개를 ID별 전체 JSON으로 대조, 모두 일치 |
| 고정 대상 | 정오 10·저녁 16개 시도 및 관련 32개 완료 작업의 해시·연결 일치 |
| 자료 | query/source/revision 연결, 원문/CHECKSUM 포함 **47개 입력 artifact**, Parquet **26개·24,206행** 검증 |
| 내용 | Parquet 행 수·해시, 봉/OI의 해당 UTC 날짜와 1분/5분 격자, 기존 품질·issue 기록 일치 |
| 기존 완료 | 완료 acquisition 162개 근거 유지, 이전 정상 revision이나 실패 이력 덮어쓰기 없음 |
| restic 지점 | 복원 전후 snapshot ID 목록 동일 |

[복원 계획](restore-plan.json) → [실제 실행 receipt](restore-receipt.json) → [checkpoint 자료 대조](checkpoint-contents.json)·[복원 자료 대조](restored-contents.json) → [최종 검증](verification.json).
읽기 도구는 [verify.py](verify.py)이며, 이전 검증의 `verify_proof`를 재사용했다.
운영 SQLite는 읽기 전용 짧은 transaction으로 읽었고, 장시간 자료 대조는 완료 checkpoint와 복원본에서 수행했다. `Store`, scheduler, `plan()`을 생성·실행하지 않았다.

이는 고정한 T13 범위의 독립 복원이다. 생략한 다른 시장 자료까지 포함한 **전체 저장소 복원 인증 또는 재백업 시험으로 확대하지 않는다.**
시험 폴더는 보존하며 [정확한 경로·용량](restore-storage.json)을 남겼다. 삭제는 이번 작업에 포함하지 않았다.

## 공통 waiter 대기는 별도 후속

00:24 첫 실행에 앞서 공통 waiter는 10/11 22:25:45–10/12 00:24:51 동안 **7,145.64초** 대기했다. 사유는 `reference_age`이며, KST 날짜 경계 뒤 신규 작업 생성도 실제 runner 시작 때 이루어졌다.

정오에는 12:17:45–12:37:59의 공통 waiter **1,213.70초** 대기 후 runner가 시작했다.
12:20 최초 실행 가능 시각 대비 첫 자료 시도 12:38:00은 **1,080.18초(약 18분) 지연**이다.
전체 waiter 대기 시간과 해당 자료의 예약 지연은 다른 값이다. 자료 시도 자체는 12:38:00–04에 10개 모두 성공했다.

[waiter-review.json](waiter-review.json)에 invocation·첫/마지막 대기·해제 기록을 고정했고 [원본 journal](journal.jsonl.gz)을 보존했다.
이 사유를 scheduler 내부 `waiting_source`, 과거 완료 취소, 다운로드 실패로 처리하지 않는다.
TODO의 **진단·백업 등 업무별 시각 요구 분리** 후속에 연결했으며 이번에 정책을 변경하지 않았다.

## 같은 복원의 T05 판정

**첫 정기 사본의 완료 직후 상한·생성 순서·restic 보존/복원 확인은 통과했다. T05 전체와 삭제 재개는 미완료다.**

- 새 completion object: `b976e8b04f2bfccbf68a2bc988faac2ed95c1e5dc9172a512396fa9f70dc9192`
- Sidecar SHA-256: `9262536c5dfa45af01c380a6cf43c94f586100a5b6ba40305861355a0e45eafd`
- `mode=after_completion`, 관측 UTC 구간의 KST 표현 **14:21:46.020546–14:21:46.296229**, U **0.137841655초**.
- 기존 baseline 1개 + 기존 사본 7개 + 새 사본 1개 = 불변 object **9개**. 새 사본이 기존 7개보다 후행임을 복원한 근거로 확인했다.
- 원래 T05 registry와 선행 checkpoint 폴더를 읽지 않고, **복원한 sidecar·manifest만으로** object 해시·완료 상한·선후관계를 검증했다.

[T05 별도 결과](t05-restored-evidence.json)를 보존한다. 신규 후행 사본은 현재 **1개**이며 3개 축적·보수적 보호 기간·당일 완료·승인 등 삭제 조건은 별도로 남아 있다.
첫 사후 상한을 갱신하거나 운영 근거를 재등록하지 않았다. 이 판정을 삭제 가능 판정으로 사용하지 않는다.

## 유지한 운영 범위와 후속

[전후 호스트 상태](host-before.json)·[마지막 대조](host-after.json)에서 Chrony와 두 수집 supervisor의 PID/invocation, daily/Chrony 설정, 사용자·시스템 자동 정리 service/timer의 `/dev/null` mask가 유지됐다.
daily 관련 5개 설정/유닛 해시는 [10/11 점검](../t13-operating-review-2026-10-11/host.json)과도 일치했다. 공개 관측기는 `enabled=false`이며 원장 관측 요청도 0개다.
샌드박스의 systemd 버스 접근 실패 뒤 호스트에서 읽기 전용 조회했으며 서비스 제어는 하지 않았다.

합의한 범위가 모두 통과했으므로 활성 T13과 진행 중 계약 메모를 TODO에서 정리했다. 상세 계약은 [agreed-contract.md](agreed-contract.md)에 옮겨 보존했다.
공개 관측기 활성화, 업무별 시각 정책·시스템 manager 이전, T05 삭제 단계, T14 성능 계측, 시험 사본 정리는 각각 별도 후속이다.
미공개 이월·429/냉각·needs_review·수동 재개가 자연 발생하지 않은 경로는 기존 213개 격리 검증으로 유지하며, 새 운영 장애를 주입하지 않았다.
고정 배포본·운영 데이터·시각 설정을 바꾸거나 Git 커밋/푸시는 하지 않았다.
