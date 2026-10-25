# T05 첫 자연 예약·자동 삭제 운영 검증 완료 — 2026-10-19

**합의한 T05 범위는 완료했다.** 10/19 15:40 KST의 실제 시스템 timer가 새 계획으로 10/15·10/16 checkpoint 두 개를 정리했고, 16:09:04에 `complete / exit 0`으로 끝났다. 21시대 읽기 전용 사후 대조에서 원본·staging 부재, 최신 3개·편입 전 제외 3개 보존, journal 종결과 미완료 상태 없음을 확인했다.

별도로 **16:53 이후 두 실시간 수집기의 용량 보호 중단**을 발견했다. T05 실행 구간의 기존 supervisor 유지와 이후의 수집 장애를 구분한다. 현재 수집기까지 정상이라고 보고하지 않는다. [별도 장애·T18 근거](../collector-capacity-incident-2026-10-19/summary.md).

## 완료 단계와 정확한 실행

| 단계 | 결과 |
|---|---|
| 첫 수동 정리·보존 대조 | 10/17 승인 대상 10개 완료. [기존 근거](../t05-mount-recovery-2026-10-17/completion.md) 재사용 |
| 설치·반복 승인·시스템 timer 활성화 | 10/18 22:59 완료. [고정 operation·활성화 감사](../t05-recurring-deploy-2026-10-18/status.md) 재사용 |
| 첫 실제 예약 평가 | **10/19 완료:** 실제 timer/job/invocation → 새 run/plan → 선행 조건과 최종 결과 연결 |
| 첫 자연스러운 자동 삭제·보존 확인 | **10/19 완료:** 삭제 2개·유지 6개·staging 비어 있음·미완료 없음 |

- operation: `2c03a81f309d0eca6f03e24a5d84fb838d75598fd1d38dbc6dd2a4204a57ae6e`
- 실행본: `t05-recurring-retirement-20261018-e57a7995f03fb301`
- scope: `311ab16f019b4473185c487b219430c1fab4e90c4257f813bacfa696b1ba29a5`
- timer: `marketdata-checkpoint-retirement.timer`, 실제 trigger **2026-10-19 15:40:00.908798 KST**
- service invocation: `51ffccda0d284584956c43248660bc2b`, start job `6604858`, UID 1000/PID 3349000
- run: `7712cfd499d845cd988cad6cf5b818a4`
- 새 plan: `e886258a9eed7ac0f22e540cd04c60bf1d53b8819176d0c17b1b43cf652aec0f`

실행 당시 D-Bus job activation details의 trigger unit·실시간/단조 시각, 환경값, service PID/invocation이 일치해 `verified_timer`로 확인됐다. 같은 invocation/job의 system journal은 성공 종료를 기록했다. 자동 action은 `run`, 이전 완료 수는 0이며 수동 plan `31766af4…`나 EXDEV `scope_transition`을 재사용하지 않았다. 활성화 당시 6개 run 이후 추가된 것은 이번 자동 run 하나다.

원본은 `/var/lib/marketdata-checkpoint-retirement/runs/<run>/run.json`, `retention/plans/<plan>.json`, `retention/journals/<plan>.json`이다. 작은 [원본 사본](evidence/run.json), [계획](evidence/plan.json), [journal](evidence/journal.json), [system journal](evidence/system-journal.jsonl), [독립 대조·원본 해시](audit.json)를 보존했다.

## 당일 백업·restic 연결

| 근거 | 정확한 값·결과 |
|---|---|
| maintenance | `c266bf265b8248709bc35e88feb6ffed`, 13:10:01 시작 → **14:53:20.926279 KST 완료**, `finished / ok=true` |
| 기존 자료 검증 | 2,213,014건, 실패 0개 |
| backup ID | `47f0b0bb7a8c7b15477017b09d97e2d17b37814fc571129f09d63447d2f6fc82`, `reused=false` |
| 자료 기준 시각 | checkpoint finished **14:43:39.993615 KST**. 전체 backup/검사 완료 시각과 구분 |
| restic snapshot | `1ed87515008161601d3bdfdf055544fd3e8c079bc5c37fb34b8beb234f734400` |
| archive | run `fb4b7f98efe641169a82d22b7e241d44`, 같은 backup ID 저장 **15:15:28.833764 KST**, exit 0 |
| 전체 검사 | `check --read-data`, **15:17:53.473214 KST 완료**, exit 0·오류 없음. 위 snapshot을 포함한 24개 목록과 plan 목록 일치 |

maintenance 원본 SHA, archive/check stdout SHA는 T05 run의 `daily_evidence`와 일치한다. 따라서 실제 당일 보존은 15:40 정리 전에 완료됐다. [maintenance](evidence/maintenance-daily.json), [archive 원장](evidence/archive-state.json), [archive 실행](evidence/archive-run.json), [check stdout](evidence/check.stdout).

이번 T05가 해당 snapshot에서 실제로 읽어 저장해 둔 `/checkpoint-evidence.json`의 stdout을 재사용했다. 해시 `117d48775965390253a32f2edc8300afb487685c98d60d86fd62f9f0d56b2713`, 객체 16개와 scope·backup ID가 archive receipt·plan에 일치한다. 그 객체만으로 완료 상한·선행 관계·후행 사본 선정을 다시 계산해 동일한 결과를 얻었다. [조회 결과 사본](evidence/snapshot-checkpoint-evidence.json). 사후 점검에서 restic dump·전체 검사·시장 자료 복원은 새로 실행하지 않았다.

## 삭제·보호 결과

| 삭제 사본 | backup ID | 기준 관측에서 보수적 경과시간 | 제거 파일 수 |
|---|---|---:|---:|
| 10/15 `26d3b66f08614a31b42d66cd3b84bb6e` | `b09dd76cb5a225668a46e470241645973f26b0971c2fc30a922a45329945dd03` | 349,390.924초 | 67,227 |
| 10/16 `2e4c1729b668424ab66293c6d29975b8` | `08eb0f0245bb2cb26b7b25ea685a93a2577aa8091b0aecdf603db5db88ac41ad` | 262,782.205초 | 67,938 |

두 경로는 `/mnt/backup/marketdata-historical/automatic/<날짜>/<폴더>` 아래의 로컬 checkpoint다. 대응 snapshot `9c0f667e…`, `789986e2…`는 당일 전체 검사 목록에 있다. 원본과 정확한 staging 경로 모두 없으며 journal의 두 항목은 `complete`, 처음부터 이번 실행에서 제거된 파일 합계는 **135,165개**다. 각 저장 inventory 해시도 원래 plan/journal의 `tree_sha256`와 일치한다. 시장 데이터의 논리 건수나 restic 사본 삭제 건수로 해석하지 않는다.

보호 사본은 **10/17 `9b916470…`, 10/18 `43b70659…`, 10/19 `47f0b0bb…`**다. 두 삭제 후보 각각보다 확실히 후행인 최종 유지 사본 3개이며, 보수적 **72시간+60초** 조건도 통과했다. 편입 전 제외 사본 **9/25 initial `e6d4a8f8…`, 9/25 linked `a3f90efe…`, 9/26 `e6e2e02c…`**도 그대로다. 현재 완료 marker 집합은 이 6개와 정확히 같고 각 `backup.json`의 backup ID·manifest SHA를 대조했다.

원래 수동 journal과 새 자동 journal 모두 완료 상태이며, 이전 실패 run은 변경하지 않았다. 현재 미완료 journal/삭제 intent는 없다. 기존 보호 기록 24개는 활성화 계획의 해시와 같고 새 정상 완료 객체 추가는 기존 근거 변경으로 취급하지 않았다.

## 시각·무결성·잠금과 점검 한계

실행의 provider 관측 **177건 모두 유효**, U는 **0.133383–0.190959초**다. reference age 원값은 7.67–1,551.90초이며 T05의 차단 기준으로 사용하지 않는다. 계획의 실제 관측 하한과 완료 상한으로 후보·보호·후행 관계를 재계산했다. 삭제 단계의 fresh 관측 및 시각 재확인은 고정 실행 경로와 정상 종결 기록에 연결된다.

설치 코드의 적용 순서는 보존 잠금 → 재감사 → 유지·후보 checkpoint 배타 잠금 → **유지 3개 `verify_backup()`** → 후보 inventory 일치 → journal attempt → 항목별 시각/구성 재검사·이동/제거다. 이번 성공 journal은 이 경로를 통과했고 최종 감시 종료도 성공했다. 별도의 사본별 전체 검사 receipt나 모든 lock syscall 로그를 출력하는 구조는 아니므로 그 이상의 독립 계측을 했다고 표시하지 않는다. 사후에는 전체 파일 검사를 반복하지 않고 저장된 run/journal, manifest와 inventory 해시를 대조했다.

고정 코드 46개, 배포 후 노드 8개, 보호 입력 50개와 기존 기록 24개가 일치한다. native host stat으로 recurring/manual/canonical·실제 자동 유닛의 root:root·0644를 확인했다. [소유권](evidence/host-ownership.txt), [실효 유닛·현재 상태](evidence/systemd-show.txt). 기존 설치 1,798개 전체 재검사는 반복하지 않았다.

## 현재 운영·다음 예약

recurring은 활성이고 원래 승인 시각·authority·scope가 유지된다. active grant SHA는 `3b436c4f0803582c6a64414a009abf2ab212945584f6ba3211ec1efbaff8035d` 그대로다. manual은 비활성, 사용자 service/timer는 `/dev/null` 두 mask, 시스템 timer는 enabled/active/waiting이다. 공통 부모 mount 경계·15:40 KST·Persistent=false·Restart=no도 같다.

**다음 자연 예약: 2026-10-20 15:40:00 KST (06:40 UTC).** 기존 반복 권한 안에서 새 계획을 평가한다. 완료된 apply/resume/수동 정리를 반복하거나 새 승인 시각을 만들지 않았다.

Chrony는 PID 1245·기존 invocation으로 계속 실행 중이다. 두 수집기는 T05 실행 중과 직후에도 원래 invocation에서 자료를 처리했지만, **16:53:55/58에 용량 보호로 종료**, 잠시 재개 후 17:05에 재차 종료됐다. 현재 호스트 전체의 무장애 상태와 T05 완료는 다른 판정이다. 현재 수집 장애는 T18로 남겼으며 어떤 서비스·시각·정책도 이번 점검에서 바꾸지 않았다.

## 성능 기록과 별도 후속

worker 총 **29분 3.163초**, manager CPU **24분 23.617초**. worker 시작부터 journal attempt까지 **26분 8.519초**, attempt 구간 **2분 54.607초**다. 각각에 여러 단계가 포함돼 순수 검증/삭제 비용으로 단정하지 않는다. restic 조회 116회(cat 2·snapshots 2·dump 112), 여유 공간 관측 증가 **7,458,230,272바이트(약 7.46GB)**이며 동시 작업을 포함한 값이다. 단계별 I/O 계측·개선은 T14에 연결했다.

완료 전 남은 범위를 [TODO](../../../TODO.md)에 따로 유지했다: 다음 유닛 재생성 시 renderer의 공통 부모 mount 수정·실제 rename 회귀, 수동/자동 당일 조건 분리 검토, restic 만료의 근거 의존관계, T14 성능 계측. 이번 범위에서 구현·적용하지 않았다. T05 활성 항목의 과거 진행 기록은 [그대로 보존](todo-t05-history.md)한다.
