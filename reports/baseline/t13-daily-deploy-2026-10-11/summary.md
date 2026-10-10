# T13 daily 운영 적용과 초기 실행 확인

**2026-10-11 00:51 KST에 승인된 고정 daily 실행본으로 전환했고, 00:53에 타이머를 활성화했다.**
00:59:10까지 첫 실행과 자동 재평가 3회가 모두 정상 종료했다. 운영 적용과 처리 0건 재평가는 완료했으며,
정오·저녁·날짜 경계의 실제 자료 처리와 새 원장의 운영 백업·복원은 아직 남아 있다. T13 전체 완료는 아니다.

적용본은 **`t13-daily-scheduler-20261011-703fb0d455523c1b`**다.
사용자의 “오케이 운영 적용하자” 승인에 따라 daily만 전환했다. 두 실시간 수집기와 Chrony는
같은 PID·invocation을 유지했고, 기존 clock guard·부팅 ledger·backup/pack/restic·자동 정리 mask는 바꾸지 않았다.
공개 시각 관측기는 `enabled=false`이며 운영 `needs_review` 재개 조작도 0건이다.

## 실제 적용 범위와 고정 근거

| 대상 | 결과 |
|---|---|
| 실행본 | `/opt/marketdata/releases/t13-daily-scheduler-20261011-703fb0d455523c1b/`, root 소유·비 editable 설치 |
| 설치 환경 | 고정 소스 149개, 검증 환경의 런타임 파일 1,486개와 패키지 18개 버전·실제 import 대조 |
| 외부 설정 | `/etc/marketdata/daily-scheduler.toml`, `/etc/marketdata/daily-publication-probe.toml` |
| 실행 주체 | 기존 `yoonhc` 사용자 systemd manager, 새 고정 Python의 `-I -m marketdata.daily_schedule run` |
| 교체 유닛 | `~/.config/systemd/user/marketdata-daily.service`와 `.timer`만 기존 workspace 링크에서 일반 파일로 교체 |
| 유지한 준비 검사 | `/etc/systemd/user/marketdata-daily.service.d/90-marketdata-clock.conf`, waiter와 기존 ExecStartPre 전체 |
| 타이머 | 5분마다 평가, 최대 30초 분산, `OnActiveSec=30s`, `Persistent=true`; `enabled/active/waiting` 확인 |
| 자료 일정 | 12:20 KST 현물 봉·펀딩·양 시장 metadata, 18:00 선물 봉·마크·지수·OI, 미완료만 20:00·22:00 재확인 |
| 원장 | 운영 카탈로그에 작업·실행·시도·냉각·관측·재개 기록용 테이블 생성. 사본 DB를 운영 DB에 이식하지 않음 |

기존 workspace 소스 139개는 배포 전과 같은 해시다. 새 daily와 기존 workspace의 pack·backup·restic을
함께 사용하는 조합의 격리 호환 검사는 [최종 후보 검증](../t13-daily-review-2026-10-10/validation.md)을 재사용했다.
이번 적용 때문에 검증한 scheduler 코드·정책·후보 ID를 바꾸지 않았다.

| 고정 항목 | SHA-256 |
|---|---|
| 소스 내용 manifest | `703fb0d455523c1bf09b0a6ff5baa898a4241e8152a2bd24dd26eb727b11a77f` |
| 소스 archive | `8ff4e33ef8b5ab59cc39fd5d3ac7b29763a7183a9c74ab8f10bbd14de2de76a3` |
| scheduler 설정 파일 | `1410f51a83b5ed95f0c2258835540c3bf2946975285041d298a9608638aff887` |
| 비활성 observer 설정 파일 | `347596dfcabe6e48a3e4084887e6930860593a83d6313aa392f4223e9f7f120b` |
| 실제 daily base service | `d9121ababca9ed23cf6bc84df1b3b9a1832494f6385eb324379064d7067a4f3b` |
| 실제 daily base timer | `55326bb195734a6ea5afea99b1916390fc839e2fbb37ccd8dc145fb8c12318a1` |

[후보 manifest](../t13-daily-review-2026-10-10/manifest.json), [설치 입력](install-inputs.json),
[설치 환경](expected-environment.json), [실효 유닛](effective-units.json),
[배포 코드 해시](deployment-code-sha256.json)를 연결한다.
root 설치 명령·UID 1000 접근 결과의 [원본 기록 사본](root-install-commands.jsonl.gz)과
[01:04 최종 소스·설치 환경·설정·유닛·문서 링크 대조](final-checks.json)도 보존했다.
실행 보고서의 `config_hash`는 해석된 설정의 식별자이며 위 TOML 파일 바이트 해시와 구분한다.

## 중단과 복구

아무 중단 없이 적용된 것은 아니다. 설치기와 전환기의 두 경계 문제를 고친 뒤 이어서 적용했다.

1. **00:42:23 관리자 설치 중단.** uv가 복사한 파일의 그룹 쓰기 권한과 `.venv/.lock`의 쓰기 권한이
   root 고정 배포본 검사에 걸렸다. 소스·환경 내용 대조는 통과했지만 서비스·설정은 아직 바꾸지 않았다.
   [최초 실패](initial-install-result.json)를 보존했다. 사용자의 원래 설치기 재실행은 기존 경로 검사에서 변경 전에 거부됐다.
2. **00:46:28 설치 재개 완료.** [별도 재개 코드](finish_install_daily.py)가 원래 입력·boot·운영 상태·환경을
   다시 대조하고 새 배포본의 1,535개 경로에서 그룹/기타 쓰기 권한만 제거했다.
   UID 1000의 import·설정 읽기·데이터 접근을 확인한 뒤 설정 설치를 마쳤다.
   후보 내용은 같고 권한 조건을 낮추지 않았다. [성공 결과](finish-result.json), [권한 변경 목록](permission-seal.json).
3. **00:49:13 daily 전환 중단.** 기존 timer를 disable할 때 enable 링크와 외부 소스를 가리키던 base 링크가
   함께 제거됐다. 전환기의 변경 금지 파일 집합에 승인된 교체 대상인 daily base 경로까지 들어 있어
   파일 부재로 검사를 중단했다. 이때 daily는 비활성, timer는 보류, 기존 service 링크와 workspace 원본은 그대로였다.
   [실패 결과](activation-result.json), [명령·단계 기록](activation-events.jsonl)을 보존했다.
4. **00:51:22 전환 재개.** [재개 코드](continue_activation.py)는 승인된 두 base 경로만 변경 금지 집합에서
   구분하고, 기록된 중단 상태·원본 링크·설치 해시·현재 시각 준비를 확인했다. root drop-in과 나머지 보호 조건은
   그대로 검사한 뒤 두 유닛 교체·daemon-reload·실효 검사·새 daily 시작을 마쳤다.
   [재개 결과](continuation-result.json). 첫 실행 성공을 확인하고 **00:53:18**에 timer를 켰다.

timer 보류는 약 4분 5초였고 기존 다음 정오 실행 전이었다. 두 실시간 수집기는 중지하지 않았다.
설치 준비 중 기존 strict gate가 일시적으로 reference age를 거부한 경우도 있었으며,
시각·임계값을 바꾸지 않고 자연 회복 뒤 실제 시작 검사를 통과했다. daily의 엄격한 gate 정책은 그대로다.

설치 재개 코드 SHA-256은 `cdff0e5d7b51623983782e6177bc9f91f07125704d5a709a5f340877ff234201`,
전환 재개 코드는 `ba768e16f82872528d6eca531fe59dd9426c7a212f891b53d29f3ad64cedea1f`다.
원래 코드·실패 결과·root receipt는 보존하며 완료한 설치기/재개기를 다시 실행하지 않는다.

## 첫 실행과 처리 0건 재평가

아래는 새 원장에 저장된 실제 실행이다. 시각은 10/11 KST, 소요 시간은 실행 보고서의 시작~종료 기준이다.

| 실행 | 시작 | 소요 시간 | 결과 |
|---|---|---:|---|
| 첫 실제 실행 `0c6e2ce4…` | 00:51:22.601 | 17.465초 | 기존 확보 156개 acquire 연결, 예정 전 20개, 처리 0건 |
| timer 활성화 직후 `e9521136…` | 00:53:18.732 | 0.264초 | 같은 완료·예정 전 분류, 처리 0건 |
| OnActive 재평가 `68c3a9dc…` | 00:53:56.306 | 0.289초 | 처리 0건 |
| 실제 5분 calendar 예약 `1414775f…` | 00:55:02.272 | 0.262초 | 처리 0건 |

네 번 모두 `runner_ok=true`, `run_outcome=no_due_tasks`, **`data_complete=false`**다.
예정 전 20개는 refresh 18개와 metadata 관측 2개다. 완료 156개를 다시 다운로드하지 않았고,
시도·서버 냉각·수동 재개 기록은 각각 0개, observer는 disabled·요청 0개다.
UTC 날짜가 바뀌기 전의 관찰이므로 이후 신규 날짜 작업과 실제 정오/저녁 처리는 아직 검증하지 않았다.

[첫 보고서](first-run-report.json), [첫 실행 확인 후 timer 활성화](timer-enable-result.json),
[00:59:10 운영 관측](operating-after.json), [원장 표본](ledger-sample.json), [journal](daily-journal.txt)에
전체 run/collection ID·종료 코드·시각을 남겼다. oneshot daily의 `inactive/dead`, `Result=success`,
`ExecMainStatus=0`은 실행 완료 상태이며 timer는 활성 상태다.

첫 ExecStart 프로세스는 17.721초, unit CPU는 17.081초였다. 00:55 재평가의 ExecStart는 0.518초,
unit CPU는 0.545초였다. provider 준비·Python 기동을 포함한 수치와 runner 내부 소요 시간을 구분한다.
`MemoryPeak`·I/O 카운터는 미설정이므로 디스크 I/O나 전체 수집 지연 영향은 측정했다고 하지 않는다.
동시에 두 supervisor의 PID·invocation과 00:59 무렵의 실제 수신·저장은 이어졌지만, 이것만으로 모든 잠금 지연이
없었다고 보장하지 않는다. 상세 성능 측정은 T14로 유지한다.

## 검증 범위와 남은 확인

- 기존 최종 후보 **213개** 검사와 깨끗한 설치 재현은 [이전 결과](../t13-daily-review-2026-10-10/validation.md)를 따른다.
  이번에는 반복 ExecStartPre·링크 교체·부분 파일·정확한 변경 범위의 **배포 회귀 7개**를 추가 확인했다.
  [검사 결과](deployment-tests.xml). 기존 전체 검사를 다시 실행한 것으로 합산하지 않는다.
- 배포 직전에는 일관된 운영 카탈로그 **사본**에서만 미리보기를 실행했다.
  00:37 기준 기존 확보 156개 제외·예정 전 20개·HTTP 0개, 사본 생성 포함 24.232초였다.
  [미리보기](predeploy-preview.json). 운영의 처리 0건 실행 시간과 비교해 성능 개선 배수로 해석하지 않는다.
- 기존 소스·root guard·부팅/보정 ledger·시각 설정·다른 예약 설정은 유지됐고 자동 정리 service/timer는
  계속 `/dev/null` masked다. 현재 두 collector/Chrony의 같은 invocation은 [전후 결과](operating-after.json)로 확인했다.
- 다음은 **12:20 정오 묶음, 18:00 저녁 묶음, 필요 시 20:00·22:00 미완료 재평가 및 날짜 경계**다.
  모든 자료가 성공하면 일부 실패 경로는 격리 근거로 남기며 운영 장애를 일부러 만들지 않는다.
- 새 작업 원장·실제로 추가된 자료의 **다음 checkpoint → 같은 backup ID의 restic → 전체 검사 → 별도 복원 조회**는
  아직 미완료다. 10/10 18:52에 보강한 10/9 UTC 네 종류의 편입도 함께 대조한다.
  예약 시각 경과나 실행기 성공만으로 자료·백업 완료로 표시하지 않는다.
- 공개 시각 HEAD 관측, 개별 needs_review 재개, 업무별 시각 정책, 시스템 manager 이전, T05 정리 재개는 별도다.

미리보기 시험 경로 `/tmp/t13-daily-predeploy-preview-20261011`은 약 4.53GiB를 보존 중이다.
이전 세 사본과 합계 약 18.11GiB이며 원문 없는 계획 사본이다. [용량](preview-storage.json).
이번에 삭제하지 않았고 기존 T06 삭제 승인을 재사용하지 않는다.

## 중지와 원복

새 daily의 향후 자동 실행만 보류하려면 `systemctl --user disable --now marketdata-daily.timer`를 사용한다.
이미 실행 중인 daily는 별도로 강제 종료하지 않고 정상 종료를 확인한다. 두 실시간 수집기·백업 timer·Chrony는 대상이 아니다.

원복은 [기록된 단계와 원복 절차](deployment-plan.md)에 따라 이전 두 base 링크와 enable 상태를 복원한다.
새 원장·자료·실패·revision은 남기며 DB 과거 사본 복원이나 테이블 삭제는 하지 않는다.
이전 일괄 실행기가 새 cooldown/needs_review를 따르지 않는 한계와 기존 strict gate를 확인한 뒤 재개한다.
개별 작업 점검·승인 재개 방법은 [검증한 CLI 절차](../t13-daily-review-2026-10-10/deployment-review.md#실제-사용-절차)를 따른다.
후속 상태는 [TODO](../../../TODO.md)의 T13에서 관리한다.

## 초기 결과 검토 후 확정한 관찰 순서

10/11 사용자의 후속 검토를 반영했다. 위 00:59~01:04의 실제 결과와 아래 관찰 계획은 구분한다.
후보 개발·재설치·서비스 재시작을 반복하지 않고 현재 배포본을 유지한다.

| 확인 단계(KST) | 확인 범위 |
|---|---|
| 10/11 09:00 이후 | UTC 날짜 변경에 따른 대상 날짜 계산, 새 작업의 최초 실행 시각 전 요청 제외 |
| 10/11 12:20 이후 | 정오 묶음의 실제 검증·저장과 완료 원장 연결, 저녁 대상은 예정 전 유지 |
| 10/11 18:00 이후 | 저녁 대상 처리, 정오 완료 acquire와 정당한 refresh·재시도를 구분 |
| 필요한 20:00·22:00 및 다음 주기 | 미완료 중 자격이 된 작업만 처리, 날짜가 바뀌어도 기존 미완료·시도·냉각 보존 |
| 10/11 정기 checkpoint/restic | 실제 기준 시각까지의 새 원장·작업 결과 및 전날 보강한 10/9 UTC 자료의 편입·저장·전체 검사 |
| 저녁 저장 이후 후속 지점 | 실제 저녁 자료와 해당 원장의 포함 확인. 현행 일정상 통상 10/12이며 실제 결과로 판단 |

13:10 예약이라는 사실만으로 저녁 자료가 들어 있다고 보지 않는다. checkpoint의 실제 기준 시각과 내용으로
대상 범위를 고정하고, 같은 backup ID의 restic snapshot·전체 검사 결과를 연결한다.
필요한 원장과 대표 실제 처리 자료가 함께 들어 있는 후속 snapshot ID를 선택해 별도 경로에서 한 번 종합 복원한다.
앞선 지점에서는 포함·저장·검사 근거를 확인하고 같은 규모의 복원을 불필요하게 반복하지 않는다.
복원 시 자료와 해당 checkpoint의 완료·대기·시도·냉각·재개 기록을 비교한다. 없는 운영 기록을 인위적으로 만들지 않는다.

strict gate 때문에 본 실행기가 시작되지 않은 대기와 실행기 내부의 waiting_source는 별도로 기록한다.
404 이월·429 등 자연 발생하지 않은 경로는 기존 격리 시험 근거를 사용하며, 운영 장애를 주입하거나
해당 실패가 생길 때까지 T13 검증을 무기한 연장하지 않는다. 공개 관측기·다른 업무 정책·시스템 manager 이전·
자동 정리 재개와 시험 사본 삭제는 기존의 별도 범위로 유지한다.
