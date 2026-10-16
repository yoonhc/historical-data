# 예약 업무의 시스템 manager 이전 계약

2026-10-13. 상태: **00:32:58 실제 이전, 22:12 합의한 T15 1단계 운영 검증 완료**.
후보·격리 검증과 임시 관리자 시험 이후 별도 승인된 고정 계획으로 적용했다.
[실제 구성·업무별 확인 범위](../reports/baseline/scheduled-system-deploy-2026-10-13/status.md)를 따른다.
[정오 실제 처리·정기 backup/T05/system restic 연결 완료](../reports/baseline/scheduled-system-operating-completion-2026-10-13/summary.md)를 확인했다.
아래 2·3단계의 업무별 시각 정책과 polling 변경은 별도 후속이며 미적용이다.
T13 완료와 T05 기존 근거 생성/보존 계약을 유지한다.

## 단계와 범위

1. **이번 후보:** 8개 업무를 시스템 systemd의 `User=yoonhc` 실행으로 옮긴다.
   자료 선정, 실행 자격, 시각 대기, 검증/저장, 재시도, 완료와 상태 판정은 유지한다.
   manager 조회, 명시적 경로, 고정 실행본, bootstrap inventory/guard/연결 해시와 실행 출처만 바꾼다.
2. **별도 후속:** 공통 시각 관측을 재사용하되 아래 각 판단 지점의 시각 요구를 분리한다.
   reference age 단독 차단 제거, 시각 미확인 진단, 날짜 결정, 상태 최신성/판단 가능성 변경은 이번에 구현하지 않는다.
3. **별도 후속:** 남은 600초 의존성 대조 후 `maxpoll 8→10`. Chrony 설정과 시각 제어는 이번에 바꾸지 않는다.

8개 모두 현행 공통 waiter의 현재 준비 확인과 직전 `ExecStartPre` 검사를 유지한다.
waiter는 strict 준비 회복까지 자기 oneshot에서 대기한다. 그 대기는 업무의 HTTP/실패 예산을 소비하지 않는다.
gate 직후 상태가 달라져 `ExecStartPre`가 실패하는 경우에는 현행 각 서비스의 Restart와 다음 timer를 따른다.
새 시스템 waiter도 `RemainAfterExit=no`이며 이전의 정상 관측을 다음 실행의 현재 근거로 재사용하지 않는다.
첫 이전 뒤 reference age 대기가 남는 것은 의도한 범위다. 정책 분리 완료로 표시하지 않는다.

## 실제 설치본 기준 계약표

모든 시각은 별도 표시가 없으면 UTC다. 예약은 이전 전 값을 그대로 보존한다.
`capture.py` 결과의 실효 속성과 아래 설치본을 후보 생성 입력으로 고정한다.
표의 workspace 실행본은 이전 전 위치다. 이전 후 OI 3종·pack·network는 동일 내용의
`scheduled-manager-20261012-255d64b78ffa8328` 고정 실행본이고 T13/T05는 기존 설치본을 유지한다.

| 업무·이전 전 실행본 | 시각 값이 바꾸는 판단 | 보류·재개 계약(이번 유지) | 완료 근거·잠금(이번 유지) | 후속 정책에서 분리할 판단 |
|---|---|---|---|---|
| OI 갱신 / workspace marketdata | `operations`의 45초 slack·5분 grid, 최근 범위와 overlap, REST 조회 가능 과거 범위 | 매 5분 :45, 활성화 30초; 실패 30초 재시작, 300초/4회 제한; strict waiter 후 실행 | update 보고서, 카탈로그/검증 저장 근거, writer 잠금. 프로세스 성공과 자료 완전성 구분 | 조회 범위 결정과 로컬 수신 시각 품질 |
| OI probe / workspace marketdata | 현재 5분 target, 최근 1시간 조회, 2ms 경계 대기, `cooldown_until`, HTTP Date/UTC-monotonic 비교 | 15초 주기·활성화 2초, Persistent=false; 개별 HTTP 숨은 재시도 없음; 다음 timer 재평가 | `state/oi-probe.lock`, 응답 원문/receipt·clock_flags. exit 2를 관측 완료로 수용하되 품질 정상으로 바꾸지 않음 | 고정 대상 관측과 현재로부터 대상 생성, 공개 지연 시간 추정 |
| OI quality / workspace marketdata | 완료된 최근 7 UTC일, `as_of`, probe age/45초 공백 | 12:40 KST·활성화 5초, Persistent=true; 다음 timer; exit 2 허용 | 카탈로그 read transaction, 고정 fetch high-watermark, 보고서의 parity·관측·needs_review | 고정 과거 내용 비교와 현재 범위/최신성 판단. T13 저녁 이후로 예약을 옮기는 변경은 포함하지 않음 |
| T13 daily / t13-daily-scheduler-20261011-703fb0d455523c1b | KST 확인 주기·UTC 자료 날짜·first_eligible_at·next_at·호스트 냉각·refresh 주기·metadata 관측 시각 | 5분 평가+활성화 30초+지연 최대 30초. 실패 60초 재시작, 600초/5회 제한. 미완료 이월·needs_review·수동 재개 계약 유지 | `state/daily-scheduler.lock`, 작업/시도/review/cooldown 원장, 검증 저장 근거, runner_ok/data_complete/review_required 구분 | 날짜·예약·냉각 경계를 입증하는 판단. 기존 예산/냉각을 초기화하지 않음 |
| pack / workspace marketdata | collection 상태와 updated_at≤현재−1시간으로 대상 선정; 실행/보고 시각 | 매시 :15·활성화 5분, Persistent=true. maintenance lock 대기; 실패 120초 재시작, 3600초/4회 제한 | `state/maintenance.lock`, writer/layout 잠금, pack·카탈로그 참조·loose hash 검증 후 같은 바이트의 개별 사본 제거 | 선정의 시간 조건. prune_loose에 T05 보관 기간을 복사하지 않음 |
| checkpoint backup / T05 c9d413cfe11ec0e7 | maintenance 잠금 취득 뒤 UTC started.date로 사본 그룹·기존 완료 재사용·증분 부모 선택; 완료 후 별도 시각 상한; 상태 조회 36h | 13:10 KST, Persistent=true. 6h timeout, SIGINT/120초 정상 종료, 실패 120초 재시작. 기존 날짜/범위 그대로 | maintenance→pack/prune→전체 validate→backup. source_root·backup ID·manifest·T05 순서/완료 근거. 근거 미확보는 유효 데이터 백업 실패와 구분 | 논리 작업 날짜 고정 및 최신성 판단. 재사용 사본은 새 후행 사본으로 세지 않음 |
| restic archive / T05 c9d413cfe11ec0e7 | 실행 기록·timer 출처·날짜별 성공/복원 관찰; manifest의 checkpoint 시각 | :05/:35·활성화 5초, Persistent=true; Restart=no, 다음 timer가 완료 사본을 다시 탐색 | 기존 observation/archive/checkpoint 잠금, 같은 backup ID/snapshot·전체 검사·T05 bundle. 과거 user receipt 보존 | 완료 사본 보존과 ‘오늘 성공’/최신성 판단 |
| network observer / workspace scripts | UTC 기록 날짜/관측 timestamp; 경과는 monotonic | 매분 :20·활성화 5초, Persistent=true; Restart=no, 다음 timer | 프로필 output_root/.lock, fsync JSONL/latest, 독립 probe 실패·경과·invocation. 진단 결과가 degraded일 수 있음 | 운영 중 시각 불확실해도 진단 확보. 네트워크 정상 자체를 진단 시작 조건으로 추가하지 않음 |

## 유지하는 코드 조합과 경계

- OI 3종·pack·network는 현재 workspace 소스와 잠금 파일 경로를 고정 배포본으로 동결한다.
  editable import를 제거하되 업무 소스 내용은 대조한다. 데이터 루트는 그대로다.
- T13과 T05 설치본은 재설치하거나 최신 workspace로 통합하지 않는다.
- restic의 system manager 조회만 작은 고정 adapter로 연결한다. 기존 module·검증 코드·scope는 유지하고 새 실행 근거에 adapter 식별자를 더한다.
- bootstrap의 기존 schema 2 프로필은 유지해 기존 수집기와 T05 reader가 읽을 수 있게 한다.
  새 시스템 업무의 기동 전 점검은 추가 topology와 bootstrap adapter가 담당한다.
  이전 사용자 업무와 timer 16개는 persistent mask, 기존 사용자 waiter는 읽기 전용 호환 항목으로 남긴다.
- 새 시스템 waiter는 기존 strict gate를 그대로 호출한다. 부팅 점검은 이 waiter와 실제 업무를 구분하고 순환 대기를 만들지 않는다.
- guard/profile/T05 clock-binding 변경은 명시적 전환 대상이다. ledger의 boot ID·closed·step 시도/결과·과거 관측을 보존한다.
  구성 교체 동안 구버전 reader도 거부하는 pending envelope를 사용하고, 파일/실효 유닛 확인 뒤 연결 해시만 이력과 함께 확정한다.
  `closed`나 설정 적용 성공을 현재 Chrony 준비 성공으로 간주하지 않는다.
- T05 baseline·완료 상한·생성 순서·등록 이력은 재등록/초기화하지 않는다. 변경된 binding 때문에 기존 비활성 삭제 승인 scope가 달라지는 사실을 보고하며 자동 승인하지 않는다.
- 두 collector/Chrony/RTC·시간원·수집 정책·공개 HEAD 관측기 비활성·사용자/시스템 자동 정리 mask를 유지한다.

## 인계·원복 완료 조건

1. 이전 대상 timer의 신규 요청 보류, 실행 중 및 waiter 뒤 pending job 확인. 실행 중 업무의 정상 종료를 기다린다.
2. 사용자 유닛을 차단하고 새 시스템 유닛/명시적 경로/기존 잠금 연결을 검증한다.
   잠금은 동시 실행 방지이며 완료 후 연속 중복 실행의 방지와 구분한다. T13 원장·backup 재사용·archiver 탐색을 유지한다.
3. timer 최초 활성화의 OnActive와 Persistent 따라잡기를 기록한다. user timer 이력이 system으로 자동 승계됐다고 처리하지 않는다.
4. 중단 단계별 재개/원복에서 불일치 상태의 시작 거부를 검증한다. 같은 부팅 승인과 파일 해시를 다시 확인한다.
5. 새 자료·원장·receipt·T05 근거가 남은 상태로 코드/관리자/연결만 원복한다. DB/자료를 과거 사본으로 덮어쓰지 않는다.
6. 격리 시험, 실제 root/system manager의 경로·권한·timer 출처 점검, 실제 운영 적용/첫 실행은 구분해서 보고한다.
   최초 후보 승인에는 root 설치·운영 timer 전환이 포함되지 않았으며 10/13 별도 승인을 받아 적용했다.
   수집 중지·추가 백업·삭제는 이번 이전/관찰에도 포함하지 않는다.

원래 설정 transaction의 CLI는 시험 루트 제한/조회 기능을 유지한다.
[별도 인계 실행기](../reports/baseline/scheduled-system-handoff-2026-10-12/deployment-review.md)가
실제 파일·manager adapter와 고정 설치·명시 링크·정상 종료·재개/원복을 연결한다.
기본 명령은 inspect이며 같은 도구의 관리자 통과 근거와 정확한 root 승인 없이는 live 실행을 거부한다.
관리자 시험에서 실제 경로·소유권·실효 환경·예약 출처와 양 manager job 처리를 확인했고,
그 결과로 같은 부팅의 최종 계획 `6a71f295…`를 확정했다. 격리 판정·시험 유닛 실행을 운영 이전 성공으로 확대하지 않는다.
원래 후보의 네 CLI 진입점 수정과 관련 해시는 새 구성 계획에 연결하며 원래 계획을 덮어쓰지 않는다.
이 계획은 검토 이후 10/13 별도 승인으로 적용됐다. 같은 계획의 준비 진입점을 다시 실행하지 않는다.
부분 원복에서 이전 apply의 취소·미발행 요청은 원복 기록과 함께 수동 대조하고 정확한 요청만 재개한다.
이 실행기는 해당 방향의 취소 요청만 자동 연결하므로 완전 무인 원복으로 표현하지 않는다.

## 후속 시각 정책의 계약

공통화 대상은 현재 Chrony 관측과 그 유효성이다. 업무 허용은 날짜·기간·쿨다운·자료 정체성·진단 목적별로 판단한다.
관측 시점과 오차 범위를 묶고 오래된 오차값에 새 wall clock만 붙이지 않는다.
실행 결과·최신성·최신성 판단 가능 여부를 분리하며 시각 보류를 자료 실패나 완료로 표시하지 않는다.
초기 부팅 준비는 유지한다. reference_stale 새 판정 제거, 과거 이력/조회 호환, 첫 supervisor/hard-fault 회복의 600초 의존성은 후속 전체 목록에서 관리한다.
각 업무의 보류 후 재평가 주체·주기·원래 대상 보존을 정의한다. 8개 새 원장이나 범용 시계 예측기는 만들지 않는다.

## 공식 문서

- [systemd 255 service](https://raw.githubusercontent.com/systemd/systemd/v255/man/systemd.service.xml): ExecCondition·oneshot·재시작의 의미.
- [systemd 255 unit](https://raw.githubusercontent.com/systemd/systemd/v255/man/systemd.unit.xml): manager별 검색 경로와 User에 영향받지 않는 %h.
- [systemd 255 timer](https://raw.githubusercontent.com/systemd/systemd/v255/man/systemd.timer.xml): Persistent·OnActive·calendar 의존성.
- [Chrony 4.5 tracking](https://chrony-project.org/doc/4.5/chronyc.html#tracking): reference와 현재 오차 추정의 구분.
