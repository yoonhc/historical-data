# 해야 할 일

최종 정리: **2026-10-09**. 현재 진행할 일은 이 파일에서 관리한다.
이전 마일스톤의 00:47 KST 기준 이후 보강·백업·시각 정책·실제 적용과 실패/재개는
[10/9 시각 근거와 수집 정책 마일스톤](docs/milestones/2026-10-09-clock-evidence-and-capture-policy.md)에 정리했다.
T04·T11 배포 후 관측은 **20:17 KST**까지이며, 문서·커밋 정리로 운영 관찰을 추가 완료하지 않는다.
T02의 첫 세션 종료·다음 시작은 **21:10 KST에 과거 실제 기록으로 확인**했고 24시간 관찰은 남아 있다.

T01 보강과 T03 백업 편입은 실제 근거를 남기고 완료했다. T10은 시간원 교체 완료가 아니라 현재 필수 후속에서 제외했다.
T06은 **21:42 KST 승인한 네 시험 경로의 수동 정리를 완료**했다. [삭제·보존·공간 확인 결과](reports/baseline/trial-artifact-retirement-2026-10-09/summary.md).
T04·T11은 **19:33:46 KST 운영 반영과 초기 저장·근거 등록을 확인**했으며,
첫 child 경계와 신규 시각 근거의 예약 백업·복원은 아직 남아 있다.
[현재 운영 안내](docs/system-runtime-operations.md), [실제 적용·관측](reports/baseline/clock-runtime-deploy-2026-10-09/summary.md).

## 관리 방법

- 작업을 시작할 때 이 목록과 관련 근거를 읽고, 사용자의 최신 지시에 맞춰 우선순위를 확인한다.
- 작업 중 발견한 후속 과제는 바로 추가한다. 구현할 일, 실제 실행을 기다리는 관찰, 선택이 필요한 보류를 구분한다.
- 작업을 마칠 때 상태·다음 행동·근거 링크를 갱신한다. 일부만 끝났다면 남은 부분만 적는다.
- 완료 조건을 충족하면 결과 문서에 근거를 남기고 활성 목록에서 제거한다. 완료 항목을 이 파일에 계속 쌓지 않는다.
- 예정 시각 경과·처리 0건·테스트 통과만으로 실제 운영 관찰을 완료 처리하지 않는다.
- 목록 등록은 운영 적용·삭제 승인을 대신하지 않는다. 이미 받은 승인과 보류 조건은 해당 작업 기록을 따른다.

## 먼저 진행

최신 순서: **T04·T11 운영 적용 완료 → 첫 child 경계·신규 근거의 예약 백업/복원 관찰 → T05 자동 정리 시각 검사**.
T03은 실제 완료 근거를 남기고 활성 목록에서 제거했다.
시간원 교체(T10)는 현재 필수 후속에서 제외한다. reference age 600초 초과만으로 시간원 교체를 요구하지 않는다.
조사·고정 후보는 보존하고, 시간원 목록을 다시 검토할 조건은 아래 조건부 목록에 남긴다.
이 순서와 범위 기록은 새 정책 구현·운영 적용 승인을 대신하지 않는다.
예약 관찰은 실제 완료 시점에 맞춰 병행한다. 아래 시각은 모두 KST다.

- [ ] **T02 · 전환 후 첫 24시간 관찰 — 관찰 대기**
  - 첫 세션 종료·다음 시작은 완료했다. 두 스트림의 수신/저장 건수·최종 partial·exit 2·자동 재시작,
    첫 collection 1,960개 파일의 해시·ID/sequence와 호가 재생 일치를 확인했다.
    aggTrade의 과거 공백과 호가 손실은 유지됐고, 다음 세션 경계 776개 ID의 자동 REST 보강도 확인했다.
    [첫 세션 후속 결과](reports/baseline/t02-first-session-2026-10-09/summary.md).
  - 남음: **10/10 00:20 이후** 실제 기록으로 첫 24시간의 재시도·시각 이상·저장 상태를 점검한다.
    19:33 runtime 교체 전후와 계획된 전환 공백을 구분한다. 한 버전의 24시간 무중단 검증으로 세지 않는다.
  - [당시 시각 원인 조사](reports/baseline/chrony-reference-age-2026-10-09/summary.md)에서
    07시 다음 세션의 시각 gate 지연, depth 관측 간격과 aggTrade 857개 ID 자동 보강을 확인했다.
    해당 근거가 첫 24시간 관찰의 완료를 대신하지는 않는다.
  - 근거: [새 부팅·수집 재개 결과](reports/baseline/system-cutover-2026-10-08/post-reboot-summary.md).

- [ ] **T04 · 시각 품질 기록의 보존·복원·collection 연결 — 운영 반영, 예약 백업·복원 관찰 대기**
  - 최종 후보 `clock-evidence-policy-20261009-65296619a374c325`: 신규 collection의 boot/provider/run/child·구간 연결,
    300초/1MiB 회전·bounded staging·불변 raw 등록·읽기 전용 조회를 구현했다.
    관련 305건 통과에는 실제 restic 전체 검사→명시적 ID 추출→프로그램 복원→조회 대조→독립 재백업이 포함된다.
    이전 `26e7fd4bbd0a68a5`는 보존했다. root 종료 시험에서 발견한 provider 조회/최종 표본 순서만 보완했다.
  - 후보 결과의 `available`은 근거 존재이며 정상 품질이 아니다. 열린/미보관 끝부분·근거 없는 과거 collection은 unknown이다.
    과거 received_at·partial·clock_usable 이력은 변경하지 않았다.
  - 전환 도구 `collector-transition-20261009-1c7c4120369fd893`: 실제 파일·잠금·SIGKILL 중단/재개/원복
    58건과 새 경로의 같은 58건이 통과했다. 기존 boot/closed/step 증거를 보존하고 불일치 중 시작을 거부한다.
    최종 계획 ID `22706e571f3a46a8745803b18bca2efacc3318c3e05b2309df6f3c7c37c621e6`.
    당시 false 양식은 보존하고, 이후 받은 실제 승인은 별도 운영 적용 기록에 연결했다.
  - 새 시각 artifact·연결·인덱스가 있는 시험 저장소를 실제 workspace pack/backup/restic으로 처리했다.
    실제 이전 `/opt` collector로 원복해 추가 수집한 뒤 두 번째 사본도 명시적 ID로 복원·독립 재백업했다.
    조회 대조 20개 일치, 원래 artifact 7개·인덱스 유지. 이전 CLI에 없는 조회는 보존한 새 CLI를 쓴다.
    운영 catalog·새 근거를 삭제하거나 과거 상태로 되돌리지 않는다.
  - 운영: 19:33:46 같은 계획으로 전환 완료, 19:43까지 신규 collection 2개·시각 raw artifact 6개와
    각 297개 관측 구간 조회를 확인했다. 열린 collection 끝은 unknown이며 CLI 종료 2는 이 계약의 표현이다.
    실제 systemd가 반복 키로 출력한 ExecStartPre를 원래 도구가 덮어쓰는 문제를 발견했다.
    조회 보완 `fb7d502c…`의 9건 + 기존 전환 58건을 확인하고 root 사본·보완 해시를 적용 receipt에 추가했다.
    고정 수집본·원래 전환 소스·계획 ID·기존 step 증거는 바꾸지 않았다.
  - 남음: **10/10 13:10 예약 checkpoint 이후**, 신규 근거가 든 정확한 backup ID와 restic snapshot·전체 검사,
    명시적 snapshot 복원 후 collection 시각 근거 조회 대조. **15:00 전후 점검**하되 실제 완료 기록으로만 판단한다.
    오늘 등록된 artifact와 기존 T03의 백업 완료를 이 신규 백업 완료로 세지 않는다.
  - 기존 v1 로그는 보존 중이지만 이번 신규 v2 연결에 소급 편입하지 않았다. 기존 로그의 불변 보존·백업 편입은 별도 후속으로 남긴다.
  - 근거: [최소 후보·검증·한계](reports/baseline/clock-evidence-policy-candidate-2026-10-09/summary.md),
    [최종 전환 검증](reports/baseline/clock-runtime-transition-2026-10-09/summary.md),
    [최종 적용 범위와 원복](reports/baseline/clock-runtime-transition-2026-10-09/deployment-review.md),
    [실제 적용·재개·초기 관찰·현재 원복 절차](reports/baseline/clock-runtime-deploy-2026-10-09/summary.md).

- [ ] **T11 · 시각 품질 판정과 업무 실행 허용 분리 — 운영 반영, 첫 child 경계 관찰 대기**
  - T04와 같은 후보에서 초기 엄격한 준비·현재 조회·provider·연속 감시·100ms/500ms/skew 조건을 유지하고,
    양의 reference age 600초 초과만 있는 경우 신선도 경고와 수집 시작 허용을 분리했다.
    미래 reference·조회 실패/만료·급변·다른 오차는 허용하지 않는다.
  - supervisor가 1시간 child 경계와 15초 간격을 계속 감시한다. 같은 child의 저장된 terminal partial(exit=2)은
    품질 이력을 보존한 채 다음 child로 이어가며, 저장 실패·다른 child 보고서는 근거로 쓰지 않는다.
  - supervisor 자체 재시작은 미관측 공백이므로 엄격한 준비를 다시 요구한다. 과거 hard fault는 누적되며 stale 예외가 다시 열리지 않는다.
    초기 부팅 및 다른 예약 업무의 gate, Chrony 설정/선택 정책, 600초 값은 변경하지 않았다.
  - 다음: **오늘 10/9 20:40 KST 이후**, 19:33:48 시작한 첫 실제 child의 종료·다음 child 시작과
    같은 supervisor 유지·15초 간격의 시각 근거·이전 partial/시각 이력 보존을 확인한다.
    초기 수집·저장과 첫 5분 근거 등록은 완료했다. 20:17 중간 확인에서 첫 child는 아직 실행 중이며,
    닫힌 시각 근거에 자연스러운 reference 지연 구간과 경고 이력 보존이 확인됐다.
    해당 구간에서 현재 오차 판정과 원문 시작 허용은 유지됐지만, **다음 child의 stale 허용 시작은 아직 미확인**이다.
    미동기화 부팅 시작, 진단·백업 등의 업무별 정책 확대는 이번 후보에 포함하지 않았다.
  - 관리자 시험의 실효 RuntimeMax=infinity, Restart=always, KillMode=mixed, SIGINT, TimeoutStop=330초와
    기존 보호 옵션을 확인했다. 같은 supervisor의 다음 child, child 저장 후 worker 등록을 기다리는 정상 종료,
    SIGKILL 후 새 invocation·child·미완료 spool 복구를 확인했다. 강제 종료 collection의 미확인 끝부분은 unknown이다.
    이 시험 입력 clock/child·경로는 대역이다. 이후 실제 운영 기동·저장은 별도 근거로 확인했으며,
    시간 단위 첫 child 경계 관찰은 아직 대신하지 않는다.
    모든 시험 경로·유닛 정리, 운영 설정·ledger·Chrony 유지까지 확인했다.
    원복 시 이전의 엄격한 시작 정책이 돌아온다.
  - 근거: [후보·304건 검사](reports/baseline/clock-evidence-policy-candidate-2026-10-09/summary.md),
    [원인과 실제 관측](reports/baseline/chrony-reference-age-2026-10-09/summary.md),
    [실제 종료·재시작과 전환 검증](reports/baseline/clock-runtime-transition-2026-10-09/summary.md),
    [운영 반영과 남은 실제 관찰](reports/baseline/clock-runtime-deploy-2026-10-09/summary.md).

- [ ] **T05 · 자동 checkpoint 정리의 상시 시각 검사 — 구현·별도 적용 필요**
  - 현재 정리 service/timer는 **persistent masked**다. 수집·백업 재개와 별개로 보류를 유지한다.
  - 계획 생성·적용 시점의 현재 시각 신뢰성과 시각 불연속을 검사한다. 부팅 `closed`나 수집기 플래그만 재사용하지 않는다.
  - 검증된 최근 3개 + 완료 후 72시간 미만 보호, 기존 잠금·무결성 조건을 유지한다.
    현재 시각의 보수적 하한과 checkpoint 완료 시각의 보수적 상한으로 보호 기간 경과를 입증하는 방안을 검토한다.
    두 시각의 불확실성 근거가 없으면 현재 NTP 정상만으로 과거 완료 시각을 신뢰하지 않는다.
  - 완료: 후보·격리 시험·삭제 없는 운영 미리보기 → 변경된 코드/정책의 승인 범위 확정 → 승인 후 재개·실제 결과 확인.
  - 근거: [기존 정리 정책](docs/restic-transition-and-retention.md),
    [현재 보류 상태](reports/baseline/system-cutover-2026-10-08/post-reboot-summary.md).

## 이어서 처리

- [ ] **T12 · 동일 event segment의 collection 연결 — 기존 코드 경계 사례 확인**
  - 거래소 ID·내용·로컬 received_at까지 완전히 같은 합성 입력을 두 collection에 반복하면 segment ID가 같아
    두 번째 collection의 event 연결이 누락되고 replay와 보고서가 달라진다. 기존 `/opt` 코드→같은 기존 코드에서도 재현했다.
  - 실제 운영 발생 여부는 미확인이다. T04·T11의 새 회귀로 단정하지 않고 원문·보고서는 그대로 둔다.
  - 다음: content 중복 보관과 collection별 연결의 계약을 좁게 확인하고, 필요한 최소 변경과 회귀 범위를 제안한다.
    이번 운영 전환 작업에 수정 기능을 추가하지 않는다.
  - 근거: [구분과 한계](reports/baseline/clock-runtime-transition-2026-10-09/validation-notes.md),
    [이전 코드 대조 결과](reports/baseline/clock-runtime-transition-2026-10-09/duplicate-fixture-control.json).

- [ ] **T07 · 10/9 신규 호가 범위 소진 후속 — 품질 이력 확인, 추가 분석 필요성 판단**
  - 00:20:43, collection `bc6bc9d2cb6149ba864f0d2003adf3af`, sequence `5580`의 `coverage_exhausted`다.
    같은 연결에서 복구됐지만 손실 이력은 남으며, 이번 기록의 근본 원인은 독립 재생으로 확정하지 않았다.
  - T02에서 failure_notice의 초기 관측과 최종 보고서 일치, 손실·partial 보존,
    재생 일치·valid=false와 이후 epoch 복구를 확인했다.
    [실제 저장·재생 대조](reports/baseline/t02-first-session-2026-10-09/summary.md).
    당시 외부 상태 조회의 공개 지연은 새로 측정하지 않았다. 남은 것은 추가 원인 분석의 필요성 판단이며,
    필요하면 이 사건의 저장 원문으로 범위를 좁힌다.
  - 완료: 확인된 처리·남은 불확실성을 기록한다. 기존 10/4·10/5 검증을 다시 열거나 요청 예산·범위 정책을 근거 없이 바꾸지 않는다.
  - 근거: [실제 사건과 복구 기록](reports/baseline/system-cutover-2026-10-08/post-reboot-summary.md).

- [ ] **T08 · aggTrades 공개 아카이브 수집·대조 — 미구현**
  - 일별/월별 ZIP·checksum·revision·출처를 보존하고 기존 WS/REST의 ID·가격·수량과 대조하는 경로를 만든다.
  - 완료: 공개된 실제 파일로 누락 보강·중복/충돌·미제공 상태·재실행·백업/복원을 검증한다.
    당시 WS 수신 시각 등 아카이브에 없는 필드는 만들지 않는다.
  - 이후 상시 수집 전의 **4개 공백, 1,901,391개 ID**도 실제 원천을 확인해 보강한다.
    2019년부터의 장기 전체 backfill 범위는 별도로 결정한다.
  - 근거: [보유 공백과 실제 아카이브 형식 확인](reports/baseline/aggtrade-coverage-2026-10-09/summary.md).

## 조건이 갖춰지면 검토

아래는 당장 실행할 작업이나 확정된 구현 약속이 아니다. 착수할 때 범위와 완료 조건을 구체화한다.

| 항목 | 다시 검토할 조건·남은 범위 |
|---|---|
| Chrony 시간원 목록 재검토 | 동기화 상실·추정 오차 한계 초과·가용 시간원 부족이 반복될 때 필요성을 판단. reference age 600초 초과나 응답 누락만으로 교체를 필수화하지 않음. [T10 조사·후보·제외 판단](reports/baseline/chrony-source-candidate-2026-10-09/summary.md) 보존 |
| 암호/복구 키트의 PC 외부 보관 | 외부 보관 수단을 정할 때. 현재 두 번째 SSD의 사본과 실제 사용 검증은 완료됨 |
| 다른 장비의 백업 데이터 사본 | 별도 장비·매체를 정할 때. 암호만 밖에 보관하는 것과 구분 |
| Proxmox 수집 VM 이전 | 새 VM 준비 후 고정 배포본·설정·마운트 UUID·권한·시각·부팅·복구와 pause/resume을 실제 확인 |
| restic 복구 지점 만료·공간 회수 | 필요 시 승인 정책에 맞는 만료 기록·보관 그룹·미리보기를 준비. `forget`과 `prune`은 별도 검증·승인 |
| 장기 aggTrades 전체 backfill | T08 이후 연구 기간·예상 용량·수집 부하를 정한 뒤 진행 |
| 현물 aggTrade/depth 스트림 | 실제 현물 데이터 수요가 생길 때 범위 결정 |
| Freqtrade 이벤트/호가 특징 어댑터 | 연구에서 필요한 입력이 정해질 때. 수집 코어의 독립성 유지 |

외부 보관·VM 복구·보관 지점 정책의 기존 근거는 [restic 전환 문서](docs/restic-transition-and-retention.md)에 있다.
Windows/RTC/Chrony 전환, 실제 root 접근 시험, 이전 restic 예약 3/3·독립 복구는 이미 완료한 범위이므로
새 작업 목록에 다시 넣지 않는다. 완료 근거는 각 보고서에 보존한다.
