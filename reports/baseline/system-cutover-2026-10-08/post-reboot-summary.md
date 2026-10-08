# 최종 Linux 부팅과 실제 수집 재개 — 2026-10-09

고정 배포본 `system-bootstrap-20261007-f85eb8321a19c1ee`의 **실제 부팅·준비 검사·수집 시작·저장을 확인했다.**
00:14–00:21 KST의 초기 조회·파일 검증과 00:30–00:32의 마감 확인을 구분한다.
첫 1시간 세션 종료·다음 수집 시작과 오늘 새 checkpoint/restic 검증은 아직 발생하지 않았다.
전환 공백의 aggTrade 미복구 범위와, 초기 검사 범위 이후 발생한 호가 범위 소진 1건도 남아 있다.

[관측 원본](post-reboot-initial/observation.json), [판단·후속 항목](post-reboot-initial/conclusions.json),
[저장 파일 검증](post-reboot-initial/stored-data-verification.json)을 연결한다.
관측은 실제 호스트에서 일반 계정으로 수행했고 관리자 명령·시각 변경·서비스 제어를 실행하지 않았다.

## 시각 준비와 실행

- 새 boot ID는 `eb163bf2-6642-4336-b4d6-6cd58771b13d`다. 설치 부팅 `9e33f0bc-…`와 다르다.
- `LocalRTC=no`, `/etc/adjtime=UTC`, `Asia/Seoul`, 외부 NTP 동기화가 유지됐다.
  00:19:29의 RTC와 시스템 UTC는 초 단위 읽기 범위에서 근접했다. 정밀한 RTC 정확도 인증은 아니다.
- Chrony는 00:11:08에 `chronyd -F 1 -f /etc/chrony/marketdata.conf`로 기동했다.
  timesyncd는 비활성/not-found여서 두 daemon이 함께 실행되지 않았다.
- bootstrap은 00:11:12 시작 후 00:11:18 정상 종료했다. 같은 부팅 ledger는 `closed`, `step_attempts=0`이다.
  외부 시간원이 아직 없을 때는 기다렸고, 남은 보정량 약 67ms·조건부 오차 상한 약 196ms인 관측이
  기존 기준을 통과한 뒤 보정 허용을 닫았다. 이번 부팅은 step 요청 없이 기준을 통과했다.
- `chrony-wait`와 두 collector는 그 이후 00:11:18에 시작했다. 실제 gate 재조회도 exit 0이었다.
  `closed`를 이후 시각의 영구 정상으로 해석하지 않고 현재 Chrony와 runtime 감시를 별도로 확인했다.
- UID 1000의 supervisor/child가 스트림별 하나씩 있었다. child의 `python -I`·venv·실행 경로는
  `/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee`였다.
  옛 사용자 collector는 masked/비활성이며 중복 수집 프로세스는 없었다.
- 실행 파일 35개와 모든 시작 검사 파일의 해시가 고정 기준과 일치했다. root 소유권·일반 사용자 수정 제한,
  원본·백업 마운트 UUID, 기존 업무 설정 9개·정리 grant도 유지됐다. Linger=yes였다.
- 두 runtime의 현재 부팅·프로세스 생존·증거 신선도 검사 모두 `clock_usable=true`, 위반 0이었다.
  이는 실행 중 표본 관측이며 메시지별 UTC 정확도나 24시간 안정성 검증은 아니다.

bootstrap journal에는 runuser의 PAM nofile/memlock limit 설정 불가 메시지가 있다.
읽기 전용 사용자 manager 조회와 bootstrap은 정상 완료했다. 메시지를 없애려고 보호 옵션·권한을 변경하지 않았다.

## 저장 품질과 전환 공백

첫 수신은 서비스 시작 시각이 아니라 저장된 WebSocket 행에서 구했다.

| 항목 | depth | aggTrade |
|---|---|---|
| collection ID | `bc6bc9d2cb6149ba864f0d2003adf3af` | `c36431bac84d4092b5f1ca33b4740af4` |
| 첫 새 WS 수신(KST) | 00:11:19.299452 | 00:11:23.302467 |
| 검사 subset의 WS 수신·저장 | 4,794건 | 22,240건 |
| 검사 canonical segment | 155개 | 92개 |
| subset ID 연결 단절·수신 시각 역행 | 0·0 | 0·0 |
| 00:19 초기 상태 CLI | healthy=true | healthy=false: 전환 공백 미복구 1건 |

depth의 4,849행은 WS 증분 4,794 + 최초 anchor 1 + check 54다.
보고된 완료 REST 대조 53회는 모두 일치했고, top 20 완전·동기화 손실 0·미재구성 메시지 0이었다.
canonical subset 전체의 해시·행 수·ID 연결·수신 시각과 각 스트림의 처음/마지막 3개 segment에 연결된
원문 해시를 읽었다. 전체 저장소 논리 검사나 호가 독립 재계산을 반복한 것은 아니다.

10/8 21:40:09 보류 시작부터 각각 약 **2시간 31분 10초 / 14초** 뒤 실제 WS 수신이 재개됐다.
이전 마지막 수신과 새 첫 수신의 차이도 [경계 기록](post-reboot-initial/conclusions.json)에 따로 남겼다.
중단 구간의 호가 원문은 확보되지 않았으며 새 anchor 이후 재구성과 구분한다.

aggTrade는 이전 저장 ID `3478866749`와 첫 새 WS ID `3479150819` 사이 공백을 감지했다.
기존 복구 collection `563e1622c2a947c2aa1f9fef319ed89f`의 결과는 다음과 같다.

- 요청: `3478866750–3479150818`, 총 284,069건.
- REST 저장: `3478866750–3478886749`, 20페이지·20,000건, 내부 ID gap 0.
- 중단 사유: `page_budget_exhausted`. 원래 자동 복구 상한 20페이지를 유지했다.
- 남은 범위: **`3478886750–3479150818`, 264,069건**.

현재 수집이 멈춘 것이 아니라 이전 공백이 남은 상태다. 새 WS subset의 연결·순서 오류는 없다.
`aggtrade-status` exit 2/healthy=false와 미복구 이력은 유지한다. 점검 중 추가 거래소 요청·예산 확대·
fresh-start 변경·기존 보고서 수정은 하지 않았다. 정확한 잔여 범위의 별도 보강·연속성 검사는 후속이다.

### 마감 확인에서 발견한 호가 품질 손실

[00:30 마감 관측](post-reboot-initial/closing-observation.json)에서 호가 `sync_losses=1`을 발견해
[00:32 후속 확인](post-reboot-initial/depth-loss-followup.json)으로 원인 코드·상태를 구분했다.
초기 저장 파일 검증은 00:19까지의 subset이므로 이 사건을 포함하는 검사로 확대하지 않는다.

- 실패 관측: **00:20:43.212214 KST**, sequence 5580, update ID `11766665291966`, `coverage_exhausted`.
  원래 report의 `failure_notice`와 오류를 보존했다. 거래소 전체 주문이 사라졌다는 의미가 아니다.
- 직전 epoch 0의 오류는 `Known snapshot price range exhausted`이며, 보존된 이전 Book은 매수 20단계였다.
  예외로 보존된 Book의 단계 수를 실패 증분 적용 후의 수로 해석하지 않는다.
- 00:20:43.315419 새 epoch가 `same_socket_after_coverage_exhausted`로 시작했고 이후 동기화와 top 20을 회복했다.
  두 시각 차이를 정확한 복구 완료 소요 시간으로 쓰지 않는다.
- 00:26:19에는 원래 900초 회전 정책에 따른 새 연결/epoch로 넘어갔다. 연결 오류와 혼동하지 않는다.
- 00:32 현재 `synchronized=true`, 미재구성 메시지 0이지만 `sync_losses=1`이므로
  `depth-status`는 **exit 2/healthy=false**다. 현재 세션의 품질 이력을 지우지 않았으며 최종 partial은 종료 후 확인한다.
- 00:30 두 runtime의 `clock_usable=true`, 시각 위반 0, supervisor PID와 재시작 횟수 0이 유지됐다.
  현재 기록의 품질 사유는 호가 범위 소진이다. 이 사건의 근본 원인을 독립 재생으로 규명한 것은 아니다.

00:30에는 호가 10,863건·aggTrade 120,360건이 각각 수신·저장 건수와 일치하며 수집이 계속됐다.
이는 현재 저장 보고서의 수치이고 앞서 파일을 읽어 검사한 subset의 규모와 다르다.
이번 마감에서 요청 예산·최소 간격·범위 정책이나 서비스를 변경하지 않았다.

## 기존 업무·백업·자동 정리

[업무 결과](post-reboot-initial/business-results.json)는 준비 검사 통과와 업무 완료를 구분한다.

| 업무 | 이번 부팅의 실제 결과 |
|---|---|
| 데이터 갱신 daily | 00:14:04–00:14:11, 8개 target complete, 오류 배열 없음 |
| OI 수집·probe·network observer | 원래 예약으로 실행·정상 종료, 다음 예약 활성 |
| OI quality | 00:11:24 보고서 생성, exit 2/needs_review=true. 서비스가 허용한 종료지만 품질 경고는 남음 |
| 파일 pack | 00:15:01–00:16:33 완료, logical_files_deleted=0 |
| restic archiver | 00:11:18–00:11:37 예약 실행, processed=0, counted_dates=[], 기존 지점 13개 |
| 새 checkpoint backup | 아직 실행되지 않음. 다음 예약 10/9 13:10 KST |
| 자동 checkpoint 정리 | service/timer 모두 persistent masked·비활성 유지 |

OI quality의 이상 기록 14개(과거 probe 실패 10, 시각 거부 4)는 전부 이번 부팅 이전이며,
가장 최근도 10/8 15:10:31 KST다. 이번 부팅의 신규 시각 역행으로 오인하지 않는다.
아카이브와 REST의 +300초 대조는 2,016/2,016 일치하지만 단위·시각 의미의 기존 검토 범위를 넘어 확정하지 않는다.
[품질 사본](post-reboot-initial/oi-quality-report.json)과 [이상 원문](post-reboot-initial/oi-quality-anomalies.jsonl)을 보존했다.

restic 처리 0건을 새 저장·전체 데이터 검사 성공으로 집계하지 않았다. 최신 확인된 복구 지점은 여전히
10/8 21:43:04 기준 backup `be6f6e49…`, snapshot `da1af66e…`, 21:56:41 전체 읽기 검사다.
이번 부팅 이후 자료는 다음 checkpoint와 동일 backup ID의 예약 restic 저장·전체 검사를 기다린다.
자동 정리는 상시 시각 검사의 별도 검증·승인 전까지 재개하지 않는다. forget/prune·시험 공간 삭제도 수행하지 않았다.

## 관측 도구 수정과 후속

첫 관측 도구가 `before.json`의 `{sha256, mode}` 객체 전체를 해시 문자열과 비교해 설정 일치를 false로 표시했다.
초기 결과를 보존하고 `sha256` 멤버로 바로잡았다. 저장 파일 검증의 `config_hash_correction`에서
업무 설정 9개가 모두 일치함을 확인한다. RTC는 승인된 LOCAL→UTC 변경이므로 전환 전 `/etc/adjtime`과
같아야 한다고 요구하지 않는다. 실제 설정을 고쳐 통과시키지 않았다.
또 journald JSON 출력의 긴 MESSAGE 생략을 피하려고 업무 로그를 `--all`로 보존했다.

다음 확인은 **10/9 01:20 KST 이후** 첫 1시간 세션 종료·저장 보고와 다음 child 시작이다.
현재 호가 손실과 aggTrade 전환 공백이 종료 보고에 보존되는지도 함께 확인한다.
이어 오늘 13:10 checkpoint의 실제 완료와 같은 backup ID의 예약 restic 저장·전체 검사를 확인한다.
시각 JSONL의 보존·복원·collection 연계와 자동 삭제의 상시 시각 gate는 필수 후속이며 아직 완료하지 않았다.
Windows 부팅 직후 NTP 이전 표시 시각, 실제 큰 step 실행, VM pause/resume은 이번에 검증하지 않았다.
