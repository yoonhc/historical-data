# T15 승인 후 운영 이전

**10/13 00:32:58 KST 승인된 시스템 manager 이전 완료. 22:12 후속 대조로 합의한 T15 1단계 운영 검증도 완료했다.**
[정오 실제 처리·정기 backup/T05/system restic 연결 결과](../scheduled-system-operating-completion-2026-10-13/summary.md)를 따른다.
아래 초기 표·보류 표시는 01:33까지의 관찰 기록이며 당시 미확인 범위를 성공으로 고쳐 쓰지 않는다.
기존 user 서비스/timer 16개는 masked·유휴이며 system timer 8개는 enabled·active다.
구성 적용 receipt를 모든 자료 처리의 완료로 확대하지 않는다. 아래 시각은 KST이며 원본 JSON은 UTC다.

## 실제 적용과 확인 범위

[root 적용 결과](root-evidence/attempt-0d473fad0e2245b59b7cf8b1b3956359.json)는 `passed=true`,
`apply_committed`다. 00:29:30 시작 뒤 **201.327초** strict 준비 회복을 기다렸다.
이 대기 동안 기존 예약은 유지했고 준비·보존 업무 유휴가 확인된 뒤 설치·인계했다.
실제 적용 중단/원복은 없었다. 인계가 취소한 요청은 0개이며 timer 시작 요청 8개를 수락했다.

[01:18:33 구성 대조](operating-20261012T161833Z.json)에서 다음을 확인했다.

- 고정 설치 파일 1,685개, root 소유권/모드/해시, UID 1000의 실제 최종 Python·prefix·모듈 경로 일치.
- 승인된 구성 46개·소유권·enable 링크 8개, 반복 ExecStartPre를 포함한 실효 명령·의존 관계·보호 옵션 일치.
- user 업무/timer 16개 masked·유휴, system timer 8개 enabled·active. 업무 User=yoonhc, ProtectClock=yes.
- boot·closed·보정 증거 보존, 승인된 profile/구성 이력만 추가, ledger와 apply receipt 일치.
- Chrony PID 1245와 depth/aggTrade supervisor PID 224785/224784 및 invocation 유지.
- Chrony/RTC/strict 정책 유지, 공개 HEAD 관측 비활성, 사용자·시스템 자동 정리 네 mask 유지.

원본 root 진행 기록·승인·명령·receipt의 사본과 해시는 구성 대조의 `root_evidence`에 연결했다.
원래 경로는 `/var/lib/marketdata-clock/scheduled-handoffs/6a71f295…/`와
`/var/lib/marketdata-clock/scheduled-transitions/0c9b8522…/`다.

## 8개 업무의 실제 첫 운영

[업무 보고서 사본·수집 경계](work-observation-20261012T162605Z.json)와
[system journal 원본/해시](journal-observation.json)를 함께 대조했다.
00:32:45–01:26:06 journal의 실제 업무 기록은 UID 1000이며 새 system invocation에 연결된다.

| 업무 | 확인한 실제 결과 | 아직 확대하지 않는 범위 |
|---|---|---|
| OI 갱신 | 새 system 실행에서 REST 수집·revision 저장·coverage complete, errors=[] | 거래소 자료의 장기 품질 보증 아님 |
| OI probe | HTTP 200, 원문·receipt·target 관측, clock_flags=[] 표본 | 공개 지연 시간 전체 조사 아님 |
| OI quality | 00:33 생성 보고서·종료 2, 기존 SuccessExitStatus=2에 따라 실행 성공 | needs_review=true를 정상 품질로 바꾸지 않음 |
| T13 daily | runner_ok=true, no_due_tasks; 완료 220개 유지, 새 주기의 refresh 18개·metadata 2개 예정 전 | 새 system에서 정오/저녁 자료를 실제 처리한 것은 아직 아님 |
| pack | 첫 평가는 0건. 다음 실행에서 pack 4개 생성 뒤 잠금 경합, 01:29:41 자동 재시도 완료·개별 사본 2,538개 정리 | 처리 0건과 후속 실제 처리/실패/회복을 별도로 기록 |
| checkpoint backup | system timer 활성·명령/경로/보호 설정 일치 | 첫 system 실행 전. 빈 실행 시각에 표시된 Result=success를 완료로 세지 않음 |
| restic archive | 00:33/00:35/01:05 실제 timer 3회, manager=system·verified_timer·현재 start job/UID/PID/invocation 대조 | 모두 processed=0. 새 snapshot·전체 검사·T05 bundle 보존의 신규 성공으로 세지 않음 |
| network observer | 새 고정 Python/script·system invocation, 실제 probe 저장, status=ok 표본 | 지속적인 네트워크 정상 보증 아님 |

OI quality는 10/5–10/11 UTC의 2,016개 비교에서 +300초 기준 숫자 일치 100%이며
원래 단위 의미 미검증 표시는 유지한다. `needs_review`에는 **10/5·10/8의 통신 실패 2개,
10/5의 시각 판정 거부 4개, 45초 초과 관측 공백 180개**가 포함된다.
과거 품질 경고를 이번 시스템 실행 실패로 바꾸거나 이력을 지우지 않았다.

restic의 뒤 두 실행에서 환경의 trigger 값은 이전 실행 값과 달랐지만, 기존 계약대로
현재 system manager의 **Job.ActivationDetails·서비스 PID/invocation·Timer.LastTrigger**를 대조해 출처를 확인했다.
환경 변수만으로 timer 실행을 인정한 것이 아니다. 과거 user receipt도 그대로 보존했다.

01:18 strict 조회는 reference age **934.652초** 하나로 보류였고 조건부 오차는 **0.124475초**였다.
실제 journal에서도 waiter 대기와 회복 후 재개를 확인했다. 이 대기는 이번에 유지한 정책이며,
이미 완료한 daily/자료를 취소한 상태가 아니다. 업무별 시각 요구 분리의 후속 근거다.

### pack 후속 잠금 경합

01:24:38의 두 번째 pack 실행은 새 pack 단계 뒤 01:26:04에 `prune_new`의
`WriterBusy: File layout is in use by a reader or backup`로 종료했다.
원문/Parquet 검증의 공유 layout 잠금 사용 시간과 겹친다. 검증 읽기의 영향 가능성이 있으며,
journal만으로 그 순간의 잠금 소유자를 확정하지는 않는다. 이를 깨끗한 첫 실행만으로 숨기지 않는다.
이후 추가 시장 파일 읽기를 멈췄으며, 기존 120초 재시도로 **01:28:05** 다음 실행이 시작돼
**01:29:41 complete·ok=true**로 마쳤다. [보고서·카탈로그·journal 대조](pack-retry-observation.json),
[실제 결과](pack-retry-result.json)를 보존한다.
앞선 실행이 01:26:00–01:26:01 생성한 pack 4개·2,538개 참조를 유지하고,
재시도의 pending_prune이 그 자료의 개별 사본 **2,538개**를 제거했다. 새 pack을 중복 생성하지 않았다.
이는 같은 바이트를 pack에 남기는 기존 정리이며 checkpoint/T05 삭제가 아니다.
기존 코드의 `logical_files_deleted=0`만으로 물리적 제거가 없었다고 표시하지 않는다.
운영 코드를 수정하거나 잠금을 우회하거나 수동 재실행하지 않았다.

## 수집기 유지와 자연스러운 다음 child

인계 전후부터 다음 child까지 **시각 artifact 16개·표본 3,172개**의 해시·연결·연속 번호를 대조했다.
두 스트림은 원래 supervisor PID·run·boot·invocation·runtime profile을 유지했다.

| 스트림 | 이전 collection → 새 collection | 새 시작 | 종료 경계→새 collection | 확인 구간 최대 감시 간격 |
|---|---|---|---|---|
| depth | `6e366b3f…` → `7983bc9b…` | 00:57:01 | 16.272초 | 1.061초 |
| aggTrade | `0353c09e…` → `22a78ce7…` | 01:01:45 | 15.496초 | 1.061초 |

이전 두 collection은 각각 complete·exit 0으로 종료했고 새 child ID/PID가 시작됐다.
새 시작의 reference age는 각각 **159.814초·443.815초**, current_estimate_usable/raw_start_allowed=true였다.
따라서 이번 경계를 stale 상태에서의 시작 시험으로 세지 않는다. 과거 stale 이력은 유지됐다.
원문·Parquet 각 collection의 첫/마지막 표본, 총 **8개 segment·68행**의 카탈로그·해시·행수/ID를 대조했다.
새 두 collection은 01:26 조회 시 각각 17,199행/14,490행 저장 중이었다. 전체 재생·메시지별 UTC 인증은 아니다.
기존 약 15초 세션 간격과 소켓 rotation은 그대로이며 무중단 WebSocket 수집을 구현한 것으로 표시하지 않는다.

관찰 스크립트의 최초 실행은 Parquet datetime을 JSON 사본으로 직렬화하는 부분에서 실패했다.
[실패 기록](work-observation-20261012T162554Z.json)을 보존하고 출력 직렬화만 보완했다.
운영 실행본·정책·자료는 바꾸지 않았으며 위 성공 기록은 별도 파일이다.

## T05 기존 근거와 01:33 당시 남은 관찰

[보존 대조](t05-preservation.json)에서 이전 복원 검증의 **baseline 1개+완료 객체 8개**가 같은
content hash로 남아 있고, 기존 완료/선후 관계 내용·등록 receipt 1개가 유지됨을 확인했다.
manager 변경으로 재등록하거나 72시간을 다시 시작하지 않았다. 새 binding은 이번 승인 구성으로 전환했다.

1. 새 system의 **10/13 정오 daily 실제 작업**을 현재 원장/저장 근거와 대조한다. 처리 0건 재평가로 대신하지 않는다.
2. **10/13 13:10 정기 backup이 실제 완료된 뒤**, 정확한 backup ID·manifest·완료 근거·같은 baseline/선행 관계를 연결한다.
3. 그 사본을 새 system restic이 저장·전체 검사한 snapshot ID와 timer receipt·T05 bundle을 대조한다.
   기존 대규모 복원이나 T13 전체 검증은 반복하지 않는다.

예약 시각은 완료 보장 시각이 아니다. strict waiter가 보류하면 사유와 실제 후속 결과를 기록한다.
T05 후행 3개·보호 기간·삭제 승인/반복 승인, 업무별 시각 정책 변경·polling 복원은 각 별도 후속이다.
추가 백업·강제 장애·재부팅을 이 관찰을 위해 실행하지 않는다. **01:33 당시 T15 운영 검증은 미완료였다.**
위 1–3항은 [22:12 최종 확인](../scheduled-system-operating-completion-2026-10-13/summary.md)으로 완료했다.
현재 T05 신규 후행 사본은 2개이며, T05 자체의 보호 기간·후행 3개·삭제 승인과 업무별 시각 정책은 여전히 별도 후속이다.

## 승인 범위

| 항목 | 고정 식별자 |
|---|---|
| 배포본 | `scheduled-manager-20261012-255d64b78ffa8328` |
| 구성 계획 | `0c9b85223c2ad851434857b4eaa86f11e94595b4663134f98881ede02fefd8fe` |
| 인계 도구 | `scheduled-handoff-20261012-b48b6beb0b9b72b9` |
| 최종 인계 계획 | `6a71f295ec760f442edf0f88b04c6cf9378d9383fb9c45c0dc7c8c140eac3f2b` |
| 부팅 | `eb163bf2-6642-4336-b4d6-6cd58771b13d` |

사용자의 “오케이 승인할게”를 [명시적 승인](approval.json)에 연결했다.
과거 비활성 template·preflight 결과·고정 도구는 수정하지 않았다.
46개 구성 변경·시스템 timer enable 링크 8개·고정 실행본 설치와
[기존 인계/부분 원복 절차](../scheduled-system-handoff-2026-10-12/deployment-review.md)가 대상이다.
수집기/Chrony 제어·시각 정책 변경·T05 재등록/삭제/자동화 활성화는 포함하지 않는다.

## 실행 전 확인 — 당시 기록

[읽기 전용 대조](read-only-before.json)에서 같은 부팅·원래 ledger·파일/소유권·실효 유닛·보호 경로를 확인했다.
Chrony와 두 supervisor는 기존 PID/Invocation을 유지하며 네 자동 정리 mask도 그대로다.
처음 보였던 pack은 정상 종료했고 backup·pack·restic은 대조 시점에 유휴였다.

시각 준비는 앞선 조회에서 통과했지만 **00:24 KST 재확인에서 보류**됐다.
후속 probe의 사유는 `reference_age` 하나(약 834초), 조건부 오차 추정은 약 0.125초였다.
기존 정책을 유지하므로 이를 무시해 인계하지 않는다. 실행 직전 다시 관측한다.

## 사용한 관리자 진입점과 현재 원복

[apply_scheduled.py](apply_scheduled.py)는 승인 기록과 설치 후 조회를 연결하는 작은 진입점이다.
전환 로직을 새로 구현하지 않고 기존 `handoff_inputs.live_execution()`을 호출한다.

1. 고정 입력·preflight·부팅·현재 구성과 소유권을 다시 확인한다.
2. 기존 예약을 유지하면서 strict 준비와 backup/pack/restic 유휴를 최대 600초 읽기 전용으로 기다린다.
   만료하면 적용을 보류한다. 이 대기는 기존 인계 실행기의 600초 정상 종료 대기와 별개이며 정책을 바꾸지 않는다.
3. 같은 계획의 별도 handoff lock 아래 root 소유 승인·기록을 만들고 기존 설치 함수로 고정 파일만 복사한다.
4. UID 1000의 최종 Python·prefix·두 모듈의 실제 경로/해시를 확인한다. 실패하면 업무 보류로 진행하지 않는다.
5. 준비용 handoff lock을 해제한 뒤 기존 인계 실행기가 같은 잠금을 획득하고 전환을 수행한다.
   실행기 자체가 준비·상태·정합성을 다시 검사한다. coordinator 획득 범위는 변경하지 않는다.
6. 원래 receipt와 전후 Chrony/수집기 신원을 연결하고 운영 관찰 대기로 기록한다.

새 진입점은 **9개 격리 검사 통과**: 잘못된 UID/venv/모듈/해시 거부, import 확인 전 인계 금지,
진행 중 보존 업무·준비 시간 초과 시 기존 업무 유지, 부분 인계 기록의 덮어쓰기 금지 등.
[원본 검사 결과](entrypoint-tests.xml). 기존 인계·관리자 시험을 새로 반복하거나 이번 9개로 대체하지 않았다.

```sh
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/scheduled-system-deploy-2026-10-13/apply_scheduled.py --run
```

위 명령은 사용자가 실행해 완료했다. 다시 설치/적용하는 명령으로 사용하지 않는다.
root 결과는 `/var/lib/marketdata-clock/scheduled-handoffs/<plan>/deployment/attempt-<id>.json`에 매번 보존한다.
원래 진행 상태/명령/receipt는 같은 계획의 기존 경로에 남는다.
`apply.json`이 생긴 뒤에는 이 준비 진입점을 다시 실행하지 않고, 실제 완료 단계를 검토해
고정 실행기의 승인된 apply 재개 또는 rollback과 미전달 요청 대조를 따른다.

현재 원복은 같은 부팅·현재 파일/ledger를 대조하는 고정 인계 도구의 rollback과
[미전달 요청 수동 대조](../scheduled-system-handoff-2026-10-12/deployment-review.md#원복)를 따른다.
user manager로 돌아가더라도 새 자료·원장·system receipt·T05 근거는 보존한다.
새 구성의 실제 재부팅 시험은 수행하지 않았다.
