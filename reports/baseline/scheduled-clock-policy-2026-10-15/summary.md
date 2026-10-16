# T16 첫 정책 후보와 격리 검증

2026-10-15. **예약 업무 8개의 시각 판단을 분리한 첫 후보를 구현하고 격리 검증했다. 운영 적용은 하지 않았다.**
T15는 완료 상태를 유지한다. 기존 세 설치본을 각각 복사해 변경했으며, 운영 workspace와 `/opt` 실행본을 덮어쓰지 않았다.

**10/15 추가 검토:** 아래 최초 후보와 검사 결과는 보존한다. 이후 wrapper의 감시/기록 장애·종료 처리 결함을 재현했으므로,
현재 후보로 관리자 전환 검증·운영 적용을 진행하지 않는다. [추가 코드 대조와 다음 보완](#1015-wrapper-장애-경로-추가-검토)을 먼저 따른다.

```text
candidate: scheduled-clock-policy-20261015-90f97df305c662c2
```

[고정 파일·전체 SHA-256](manifest.json), [설치본과 변경 대조](source-review.json),
[검증 집계](validation.json), [운영 전환 검토와 원복](deployment-review.md).

## 이번에 바꾼 판단

공통 관측은 T05의 읽기 전용 `Host`를 그대로 사용해 같은 부팅의 실제 준비 완료·closed·provider·설정/프로필을 확인한다.
`closed`만으로 현재 준비를 대신하지 않고 매 결정에서 새 tracking을 받는다. 각 업무가 그 관측 범위로 자기 날짜·기한을 판단한다.
reference time과 age는 원값으로 기록하며 새 정책에는 `reference_stale` 등급이나 600초 차단이 없다.
수집기의 개별 100ms/500ms/100ppm 또는 T05의 5초 상한을 8개 업무에 복사하지 않았다.

| 업무 | 후보 동작 | 보존하는 경계 |
|---|---|---|
| OI 갱신 | 45초 slack·5분 grid가 관측 구간에서 확정될 때 범위 생성. 실제 REST 끝은 현재 하한 이내 | REST 29일 범위·원래 중첩/내용 검증·writer 잠금 |
| OI probe | 현재 target·조회 끝·서버 냉각을 구간으로 판정. 응답 후 조회가 실패하면 응답을 남기고 UTC 품질 미확인 | 하나의 실제 응답과 공개/지연 판정 구분, 숨은 반복 요청 없음 |
| OI quality | 완료 UTC 날짜를 고정하고 기존 과거 내용 비교 수행. 최근 관측의 신선도 판단 가능 여부 별도 기록 | 기존 parity·공백·needs_review 유지 |
| T13 daily | UTC/KST 날짜 구간 확인, `first_eligible_at`·`next_at`·냉각은 하한으로 확인. 상대 대기는 관측 상한부터 보수적으로 계산 | 기존 ID·acquire/refresh 구분·실패 예산·수동 추가 예산·냉각·저장 증거 |
| pack | 기본 선정 기준을 관측 하한−1시간으로 계산 | pack/참조/바이트 검증과 잠금, 동일 내용 loose 사본 정리 조건 |
| checkpoint backup | maintenance 잠금 획득 후 UTC 작업 날짜를 고정. 긴 검증 후 새 관측을 얻어 원래 날짜로 생성/재사용 | 실제 완료 후 T05 근거, 기존 baseline/상한/생성 순서, 데이터 성공과 근거 실패 구분 |
| restic | 완료 사본의 보존/검사 성공과 실행 시각·예약 성공 분류를 분리. 종료 관측 불가면 데이터 성공을 취소하지 않고 날짜 기여를 보류 | system timer/PID/invocation 검증·snapshot·전체 검사·T05 bundle |
| network observer | 유효한 시작 관측 뒤 독립 probe를 수집. 종료 시각 조회가 실패해도 실제 응답/실패를 보존 | 시작부터 현재 시각 근거가 전혀 없는 진단 허용은 이번 범위 밖 |

상태 함수는 정책 context 안에서 `execution_succeeded`, `fresh`, `freshness_known`을 구분한다.
판정 불가를 이미 저장한 자료의 실패로 바꾸지 않는다. 과거 기록은 다시 쓰지 않는다.
운영에 설치된 기존 상태 조회 CLI는 아직 기존 동작이며, 현재 시각 없는 상태에서 독립 상태/진단 명령을 바로 허용하는 경로도 후속이다.

## 관측 모델과 재개

조건부 Chrony 추정 `abs(System time) + root dispersion + max(root delay, 0)/2`와
조회 정렬 여유·표본 폭을 분리해 저장한다. 조회 전후 UTC/monotonic/boottime 표본과 boot/provider/프로필 식별자를 묶는다.
조회 3초·감시 1초·감시 공백 5초·250ms 수준 불연속 검사는 유지한다.
새 결정은 새 조회를 사용하며 오래된 U에 새 UTC를 붙이지 않는다. reference가 관측 상한보다 미래이면 거부한다.

이 구간은 기준 시간원/Chrony 모델과 관측 전제에 의존한다. 작은 미검출 이동의 정밀 모델은 이번에 추가하지 않았고,
250ms 미만의 모든 변화가 U 안에 포함된다고 보장하지 않는다. T05에서 합의했던 이 한계도 유지한다.
[Chrony 4.5 tracking 공식 설명](https://chrony-project.org/doc/4.5/chronyc.html#tracking).

시각 보류는 작업 잠금이 풀린 뒤 5초 단위로 새 근거를 확인해 재개한다. T13 원장은 그대로 사용하며 새로운 자료 스케줄러를 만들지 않았다.
wrapper의 작은 `request.json`에는 실행 인자·정책/설정 신원·고정 날짜를 원자적으로 남긴다. 재시작해도 그 날짜를 이어가며 다른 설정의 미완료 요청은 거부한다.
backup의 긴 검증 뒤 관측 재확인은 maintenance 직렬화 잠금만 유지하며 DB/layout/coordinator 잠금을 점유하고 기다리지 않는다.

서버 오류 뒤 시각 조회가 실패해도 원래 HTTP 응답 헤더·냉각·실패 사유를 먼저 보존한다.
시각 대기 자체는 통신/검증 실패 횟수나 수동 추가 시도 예산을 소진하지 않는다. 실제 발생한 통신 오류는 회복 후 한 번 반영한다.
하위 `jobs`에도 `deferred_clock`을 남기며 자료 실패로 오기록하지 않는다.
저장 commit 뒤 보류된 작업은 기존 검증 근거로 완료를 복구해 재다운로드하지 않는다.

## 확인한 범위

- 새 정책 **52개** 검사: 오래된 reference, 날짜/기한 경계, 실제 저장과 무요청 재평가, 냉각 두 형식·조회 실패 경합,
  추가 시도 예산 보존, 저장 후 보류 복구, pack 내용 보호, 날짜를 넘긴 backup/재개, T05 연결,
  이전 실행본의 새 원장 backup/restore/read, 부팅/pending 거부, 유닛 범위 대조.
- 기존 기능 회귀는 실행본별로 수행했다. 기본 업무 95개와 pipeline 72개 실행에는 중복이 있어 단순 합산하지 않는다.
  T13 82개, T05/백업/restic 관련 146개도 통과했다. 정확한 실행별·중복 제거 집계는 [validation.json](validation.json)을 따른다.
- 격리 환경의 실제 restic 저장/복원 경로를 포함했다. 운영 저장소 전체 검사나 대규모 운영 복원은 반복하지 않았다.
- 서비스 9개(업무 8개+새 waiter)와 기존 timer 8개의 **17개 native 구문/순서 검사** 통과.
  미설치 `/opt` 경로만 시험 경로로 치환했다. 실제 system manager 실행·권한 검증으로 확대하지 않는다.
- 기존 부팅·수집·T05 정책/내용 보존 코드의 변경 여부와 네 자동 정리 mask를 대조했다.

초기 검증에서 후보 시험 환경의 선언/설정 파일 누락, subprocess의 workspace import, T05의 혼합 import 거부가 있었다.
필요한 부모 파일과 격리 가상환경·실제 진입점의 import 순서를 맞췄다. 원래 보호 검사를 제거하지 않았다.
초기 결과 파일과 최종 통과 결과를 구분한다. 수정 도중 작성한 `patch_candidate.py`·`finish_candidate.py`는 개발 기록이며 적용 도구가 아니다.

OI probe에서는 조회 하한을 요청 시각으로 기록하면 큰 U가 가짜 불연속처럼 보일 수 있어,
**실제 중심 표본의 요청 시각과 보수적인 query end를 분리**했다. 불연속 기준을 완화하지 않았다.

## 남은 적용 단계와 후속

실제 보호 옵션 아래 관리자 시험, 정확한 구성/ledger 전환·중단/원복 계획, 운영 승인·적용·첫 처리 확인은 미완료다.
기존 T15 승인이나 인계 계획을 재사용하지 않는다. 이번 후보의 [적용 검토](deployment-review.md)를 기준으로 범위를 고정한다.

현재 시각 근거 없이 시작하는 진단·보존, 독립 상태 조회 확대, supervisor 최초 시작/회복·bootstrap의 600초 의존성,
`maxpoll 8→10`은 [TODO](../../../TODO.md)의 별도 범위다. T05 삭제 정책·승인 scope·mask를 이 작업으로 활성화하지 않는다.

판정 JSONL과 request 파일은 로컬 재개/감사용이다. 자동 checkpoint는 모든 `state/` 파일을 포함하는 계약이 아니다.
T13 원장의 새 관측과 OI 원문 receipt, T05 완료 bundle은 기존 백업 경로를 사용하지만,
새 보조 JSONL 전체의 자동 백업/회전·독립 복구까지 완료했다고 주장하지 않는다. 실제 운영 용량·조회/기록 비용은 T14 관찰에 연결한다.

## T05 병행 확인

10/14 새 backup `8dba1987…`, restic `94c79c5a…`의 저장·전체 읽기 검사와
지정 snapshot의 작은 시각 근거 bundle을 확인했다. **후행 사본 3개, 객체 11개**이며 기존 baseline과 이전 근거를 유지한다.
[정확한 ID·완료 근거·snapshot 직접 조회](t05-observation/result.json).

이번에 새 backup/전체 검사/삭제 미리보기를 실행하지 않았다. 10/15 당일 backup/restic 완료와 현재 구성의 새 미리보기·정확한 승인은 별도다.
이전 비활성 승인 scope를 재사용하지 않으며 네 mask를 유지한다.

## 10/15 wrapper 장애 경로 추가 검토

사용자가 지적한 경로를 고정 후보 원본과 대조하고 **후보 Python 3.12.3**에서 독립 재현했다.
사용자 메시지의 `/mnt/data/t16_review_reproduction.zip`은 이 호스트에 없어 직접 실행하지 않았다.
[재현 스크립트](review/probe_current_candidate.py), [결과와 입력 소스 해시](review/probe-results.json).
실제 thread와 임시 request/config 파일을 사용했으며 provider·업무는 대역이다.
종료 실패는 정상 thread join 뒤 예외를 주입했으므로 실제 감시 정지 timeout 자체를 유발한 시험과 구분한다.
운영 Chrony/업무/systemd를 실행하거나 후보 소스를 수정하지 않았다.

| 항목 | 코드·재현 결과 | 보완 계약 |
|---|---|---|
| 감시 thread 종료 후 재개 | `job_clock.Context.start()`의 오류 기록도 실패하면 thread가 종료된다. `restart_observation()`은 fault만 지우고 wrapper는 thread 객체 존재만 검사해 생존하지 않는 감시 아래 업무를 호출한다 | 감시/기록 장애를 시각 보류와 분리하고 해당 실행을 실패로 종료. 다음 실행에서도 미완료 작업·저장 근거를 확인하며 fault만 지운 재개 금지 |
| 관측 기록·알 수 없는 오류 오분류 | 정상 관측의 emit OSError와 query KeyError가 모두 ClockDeferred가 됐다 | 재평가 가능한 명시적 시각 조건만 보류로 처리. 기록·설정·프로그램 오류의 원인과 예외 연결을 보존 |
| 최종 요청 기록 누락 | 업무가 0을 반환해도 close 예외가 나면 디스크 request는 pending이고 exit_code가 없다 | 업무 결과·감시 종료 오류·요청 상태를 분리. close 실패에도 최종 저장을 시도하고 감시/기록 실패를 정상 완료로 표시하지 않음 |
| 설정 identity | 같은 경로의 내용 변경 후 같은 identity와 기존 pins로 업무에 진입 | 업무별 실제 설정 및 필요한 참조 정책/프로필의 digest를 미완료 요청과 연결. 기존 binding과 argv만으로 내용 동일성을 주장하지 않음 |
| 종료 코드 2 | 모든 job을 complete로 기록하고 다음 요청에서 pins를 비운다 | 숫자만으로 종결하지 않고 해당 업무의 실제 보고서/원장 및 원래 날짜에 대한 처리 근거로 결정 |

설정 검사 대조: `checkpoint_clock.Host.lock/query`는 Chrony 설정·runtime·bootstrap·ledger·provider를 검사하지만,
bootstrap의 `guard_files` 대상 내용을 다시 대조하거나 업무 TOML의 digest를 미완료 요청과 비교하지 않는다.
OI CLI는 config를 다시 읽고, T13의 `config_hash`는 기록되지만 wrapper의 이전 요청 identity와 비교되지 않는다.
T05/restic의 경로·권한·저장소 검사는 유지되어도 모든 업무 설정의 이전 내용 동일성을 보장하는 공통 경로는 아니다.
따라서 위 최초 보고서의 **“다른 설정의 미완료 요청은 거부한다”는 표현은 argv/binding 범위로 한정**되며 업무 설정 내용까지 보장한 것으로 읽으면 안 된다.

실제 8개 진입점의 종료 의미도 구분했다.

| 업무 | 현재 반환의 의미 |
|---|---|
| OI update | `incomplete()`가 waiting_source/quarantined/failed/complete=false 등을 만나면 2. 기존 범위·coverage·실행 기록으로 후속 처리 판단 필요 |
| OI probe | cooldown/boundary_wait/관측 미완료 등 complete=false이면 2. 원문 receipt·냉각·다음 관측과 연결해야 함 |
| OI quality | 보고서 저장 후 needs_review이면 2. 해당 고정 구간의 보고서 작성 완료와 품질 경고를 구분 |
| pack(hourly maintenance) | 정상 처리 말미 용량 검사에서 부족하면 partial·2. pack 내용·참조 보호 실패는 예외 경로이며 같은 의미로 묶지 않음 |
| backup | 실제 backup 생성/재사용 후 용량 경고면 partial·2. backup ID·원래 work_day·검증 완료와 종료 경고를 함께 확인 |
| daily | 실행기 성공/잠금 보류는 0이며 자료 완료는 별도 원장이 책임짐. 일반 업무 오류는 1 |
| restic | 성공은 0, 잠금 보류 75, 일반 오류 1. 저장 성공과 예약/시각 분류는 따로 유지 |
| network observer | 관측 저장 또는 잠금 busy는 정상 반환. 관측 내용의 네트워크 실패를 프로세스 실패로 바꾸지 않음 |

또 argparse의 사용법 오류도 SystemExit(2)가 될 수 있으므로, 공통 `code in (0, 2)`를 업무 완료 근거로 쓰면 안 된다.
이번 검토는 코드 경로 대조이며 모든 하위 반환 분기를 실제 업무로 새로 실행한 결과는 아니다.
기존 T13 저장 commit 후 복구, HTTP 실패 횟수 한 번 반영, OI 냉각 보존, backup 고정 날짜/재사용 검사는 재사용한다.
그 근거를 이번 감시·기록·close 실패 조합의 검증으로 확대하지 않는다. wrapper 재호출과 실제 잠금 해제/저장 복구의 연결만 보충한다.

다음은 시각 정책 변경이 아니라 위 실행·복구 경로의 좁은 보완이다.
현재 후보·manifest·최초 validation은 그대로 보존하고, 수정 소스와 안전한 동작을 기대하는 회귀 검사는 새 후보에 고정한다.
재현 스크립트의 `defect_present=true`는 **현재 결함 발견 결과**이며 수정 후보가 통과해야 하는 기대값이 아니다.
T05 정책/삭제 승인·mask와 운영 설치 상태에는 이번 검토로 변화가 없다.
