# T16 감시·기록 장애와 요청 복구 보완

**10/16 후속:** 아래 후보와 당시 결과는 보존하고, 저장·복구 경계의 세 파일 보완을 새 후보 `544c61aca49f1355`로 완료했다. [최신 후보·119개 정책/복구 검증·남은 관리자 단계](../scheduled-clock-policy-storage-2026-10-16/summary.md).

상태: **수정 후보 구현·격리 검증 완료. 실제 관리자 환경, 전환·원복, 운영 적용은 미완료**다.
기존 후보의 시각 정책은 유지하고, 장애가 시각 대기나 정상 요청 완료로 바뀌던 실행 경로를 수정했다.

**10/15 추가 대조:** SQLite 연결의 명시적 close 누락과, 보류한 `OSError`를 `DataError`로 복원하는 분류 차이를 확인했다. 소규모 보완 후 새 해시 고정이 필요하다. 실패 반영 transaction과 완료 복구 재중단은 추가 격리 대조에서 안전하게 재개됐고, 정상적으로 갱신된 latest를 참조하는 원복 경로는 추가 연결 대상이다. [확인 결과·미완료 범위](review-storage-boundaries/review.md). 아래 90개 검증 결과와 고정 후보 자체는 당시 결과로 보존한다.

고정 후보는 `scheduled-clock-policy-20261015-5b02fc74867e5bab`이다.
전체 소스 해시는 `5b02fc74867e5bab2a34c9a02f9ca2a2e1f356d6de8c978367ee8ece1cc17b25`.
[manifest](manifest.json)의 runtime 파일 291개를 대조했으며, 첫 후보 대비 **18개 변경·1개 추가**다.
기존 `90f97df305c662c2` 후보의 runtime 290개와 manifest 해시는 그대로 보존했다.
[정확한 변경 파일·전후 해시·운영 입력 대조](source-review.json).

## 변경한 동작

| 검토에서 확인한 문제 | 수정 후보의 처리 |
|---|---|
| 감시 오류와 기록 오류 후 thread 종료, fault만 지워 업무 재개 | 감시·기록 오류를 먼저 메모리에 고정하고 필수 thread의 생존을 확인한다. 같은 Context에서 오류를 지우거나 thread를 자동 재생성하지 않는다. |
| 성공 관측의 기록 I/O 오류를 `ClockDeferred`로 변환 | 명시적 시각 조건만 재평가한다. 기록·설정·알 수 없는 프로그램 오류는 `ClockFailure` 또는 원래 오류로 전달한다. |
| `close()` 실패 뒤 최종 request 저장 생략 | 업무 결과, 감시 종료 결과, 요청 종결 상태를 따로 기록한다. close 실패 후에도 journal·시도 기록·현재 request 저장을 각각 시도한다. |
| 같은 설정 경로의 내용 변경을 미확인 | 업무 설정 내용·binding·wrapper 코드의 digest를 요청 identity에 포함한다. 같은 경로의 변경도 미완료 요청 재개를 거부한다. |
| 모든 exit 2를 complete 처리 | 실제 CLI가 저장한 업무별 보고서·원장과 결과를 연결해 종결 여부를 판단한다. 종료 코드만으로 완료시키지 않는다. |
| 종료 처리 실패 후 이미 저장한 업무를 다시 호출할 가능성 | CLI 반환 전에 저장 결과와 참조 해시를 request에 확정한다. 다음 실행은 새 감시 아래 그 근거를 재검증하고 업무 본문 재호출 없이 요청을 종결할 수 있다. |

구현 위치:

- [job_clock.py](candidate/policy/job_clock.py): `ClockDeferred`/`ClockFailure`, 감시 수명·오류 고정·제한된 join, 저장 결과 연결. base/daily/preserve 패키지 사본도 같은 바이트다.
- [run_job.py](candidate/policy/run_job.py): 조회 예외 adapter, 요청·시도 식별, 결과 보존, 종료 처리, 후속 실행의 저장 결과 대조.
- [request_contract.py](candidate/policy/request_contract.py): 실제 설정 내용과 보고서·SQLite 개별 행의 해시, 8개 업무의 종결 판정.
- 실제 CLI 연결은 [변경 목록](source-review.json)의 `cli.py`, daily 실행기, network observer, preserve의 backup/restic 진입점에 있다. 별도 가짜 완료 플래그로 업무 결과를 대신하지 않는다.

## 시각 대기와 실행 오류

정상적인 시각 조건의 일시 보류는 기존 5초 재평가를 유지한다. 재개할 때 감시 thread가 살아 있고 기록 가능한지 확인하고 이전 공백·사유를 남긴 뒤 새 관측을 얻는다. 업무 함수 재호출은 기존 잠금이 해제된 뒤이며, 각 업무의 원장·저장 근거가 이미 끝낸 부분을 복구한다.

thread의 예기치 않은 종료, 표본 함수의 알 수 없는 오류, 관측 기록 실패는 같은 재개 루프에 들어가지 않는다. 실제 처리 중인 함수에 비동기 예외를 강제로 주입하지 않으므로 **다음 판단 지점 또는 종료 처리에서 실패가 전달되는 구조**다. 장시간 I/O를 모든 순간 즉시 중단한다는 보장은 아니다.

최초 준비용 `--wait`도 예외 분류를 사용한다. 기존 T05 `Host`의 해시 고정 코드는 바꾸지 않고, query timeout·provider 미준비 등 명시적으로 분류한 조회/준비 보류만 변환한다. 신뢰 경로·구성 불일치와 알 수 없는 `ClockHold`는 실패다. 기록 I/O를 Chrony 응답 부재로 해석하지 않는다.

reference 원값, 조건부 오차식과 기존 조회·표본 여유, 날짜·격자·기한 구간 판단은 첫 후보와 같다. 600초 기준을 복구하거나 T05의 U 5초/수집기의 개별 정확도 기준을 복사하지 않았다. 작은 시각 이동의 추가 모델도 만들지 않았다.

## 요청·종료·복구 계약

요청 형식은 `scheduled-request-v2`다. `request_id`, `attempt.id`, 실제 invocation ID, 고정 날짜 `pins`, 설정 digest를 보존한다. 시도별 파일과 필요 시 중단 당시 request 사본을 남기며 이전 실패를 덮어 성공으로 바꾸지 않는다.

`attempt.work.observed_exit_code`는 실제 반환한 값이고, 중단 후 저장 결과만으로 복구했다면 `observed_exit_code=null`, `recovered_committed_result=true`다. `disposition.data_complete`는 요청 종결과 별개다. 종료 실패 시 `work_exit_code=0`이더라도 요청은 `finalization_failed`, 프로세스는 실패한다.

같은 경로의 설정은 내용 SHA를 검사한다. backup은 settings → retention policy → archive profile 및 clock binding까지 연결한다. 암호 파일의 비밀 내용은 request에 복사하지 않는다. 첫 실행·매 시각 판단·업무 재진입·결과 기록에서 scope를 대조한다. 다른 내용의 미완료 요청은 자동 초기화하지 않는다. 이전 형식의 request 역시 지우거나 추정 변환하지 않고 명시적 대조 대상으로 거부한다.

| 업무 | 요청을 종결하는 근거 | 미완료·실패 처리 |
|---|---|---|
| OI update | 같은 sync_run ID의 실제 원장 보고서. complete 또는 partial의 범위 재탐색은 기존 coverage 로직에 남음 | failed run은 요청도 failed. 코드 2의 partial을 자료 전체 성공으로 표시하지 않음 |
| OI probe | probe/source ID가 있는 실제 `oi-probe-latest.json`, 응답 결과와 코드 일치 | 냉각/경계 대기는 deferred. 응답 이후 감시 장애이면 받은 원문·receipt·냉각을 먼저 저장하고 실패 전달 |
| OI quality | 실제 저장한 `report.json`과 고정 비교 날짜 일치 | needs_review의 exit 2는 보고서 작성 완료. 사용법 오류 2는 저장 결과가 없어 거부 |
| daily | 같은 daily_run ID의 SQLite 완료 기록과 `runner_ok` | 전체 자료 완료는 별도. 남은 작업·냉각·예산은 T13 원장이 담당. 잠금 보류는 deferred |
| pack | `phase=finished`인 같은 maintenance 실행의 저장 보고서 | 종료 후 용량 경고의 2만 종결. 단계 미완료·예외는 종결하지 않음 |
| checkpoint backup | maintenance 보고서·검증 성공·같은 backup ID의 marker와 고정 날짜 | 종료 오류 후에도 원래 날짜·완료 사본·T05 근거를 재사용. 재생성/보호 시각 초기화 없음 |
| restic | 같은 scheduled run 보고서와 성공한 archive receipt | 시간상 자격 판정과 archive 저장 성공은 기존처럼 구분. 성공 receipt 없는 오류를 완료시키지 않음 |
| network observer | 실제 latest 보고서의 결과·종료 시각과 반환값 | degraded 관측은 관측 완료이며 네트워크 정상 보장이 아님. lock busy는 deferred |

참조 보고서나 원장 행이 달라졌거나 사라졌다면 복구 시 실패하고 기존 pins/result를 보존한다. 결과를 검증할 수 없는데 새 날짜로 넘어가거나 정상으로 추정하지 않는다. 모든 기록 경로가 동시에 쓰기 불가능하면 최종 상태 저장 자체를 보장할 수 없다. 이 경우 프로세스는 성공을 반환하지 않으며 마지막으로 저장된 pending/pins와 업무 자체의 근거가 복구 입력이다.

## 검증 결과와 한계

[검증 집계와 XML 해시](validation.json). Python 3.12.3의 실제 thread·파일·SQLite를 사용했다.

| 범위 | 통과 |
|---|---:|
| 정책·wrapper·실제 base CLI 연결 | 66 |
| 정책·실제 daily CLI/원장 연결 | 11 |
| 정책·실제 backup/T05·restic 진입점 연결 | 13 |
| 기존 base 회귀 | 119 |
| 기존 daily 회귀 | 82 |
| 기존 preserve 회귀 | 146 |
| native systemd 유닛 구문·순서 | 17개 유닛 |

**정책·복구 검사는 총 90개**다. 이전 52개와 이번 보완 38개이며, 중간 반복 실행을 더하지 않았다. 기존 회귀는 세 실행본별로 구분했다.

추가 검사는 실제 감시 thread의 오류/종료, 정상 관측 후 journal 오류, 제한 시간 내 종료되지 않는 sampler, 종료·최종 저장 실패의 분리, 설정 내용 변경, 참조 변조 거부, 실제 CLI의 코드 2/보류 판정을 포함한다. 별도 시험 프로세스를 결과 저장 직후 SIGKILL하고 다음 실행에서 **업무 호출 없이** 저장 결과를 대조하는 경계도 통과했다. backup 완료 뒤 close 실패 후 날짜를 하루 넘긴 격리 입력에서도 사본·완료 상한·T05 객체는 바뀌지 않았다.

wrapper 연결 시험의 Chrony/provider·HTTP·systemd 출처는 대역이다. 실제 코드의 파일·카탈로그·원장 처리를 실행했지만 실제 운영 서비스 검증은 아니다. 별도의 기존 preserve 회귀에서는 임시 저장소의 실제 restic 보존/복원도 실행했다. 구체적인 재현 명령은 [검증 실행 기록](validation-commands.json)을 따른다.

초기 검사 실패도 보존했다. daily 정책 시험 한 곳의 `OSError` 대역을 명시적 조회 보류로 바꾸고, 최초 lock 실패 전에 SQLite 테이블이 아직 없다는 기대를 바로잡았다. 기존 daily 회귀의 상대 경로/하위 프로세스 import 실패 2건은 후보 루트에서 다시 실행해 82건 모두 통과했다. 정책이나 구현의 실패 조건을 느슨하게 하지 않았다. [초기 연결 시험](result-daily-initial.xml), [잘못된 cwd의 회귀 실행](daily-regression-initial-cwd.xml). SIGKILL 시험도 다중 thread pytest 프로세스의 fork 대신 별도 Python 프로세스로 실행하도록 고쳤다.

## 운영 경계와 다음 단계

운영 19개 구성 입력은 첫 후보 준비 때와 동일하고, T05 네 정리 mask는 `/dev/null`이다. boot ledger는 같은 boot의 `closed`를 유지했다. 운영 서비스 명령·시각 제어·데이터 변경은 수행하지 않았다. 세 후보 환경의 설치 패키지와 소스 사본을 각각 대조했다.

다음은 이 고정 후보에 대한 **실제 관리자 보호 환경과 새 구성/ledger 인계·중단·원복 검증**이다. 운영 설치·재시작·적용 승인은 아직 없다. [적용 검토와 남은 범위](deployment-review.md).

현재 시각 미확인 상태의 진단 시작, 수집기/부팅의 남은 600초 의존성, polling 복원, 독립 status 진입점 확대는 별도 후속이다. 보조 JSONL/request/시도 이력 전체의 자동 백업·회전·독립 복구도 이 후보로 완료되지 않는다. T05 기간/후행 사본/보존/삭제 승인은 계속 별도이며 자동 정리 mask를 유지한다.
