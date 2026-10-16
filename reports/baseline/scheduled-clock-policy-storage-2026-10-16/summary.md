# T16 저장 근거·복구 경계 보완 결과

**요청한 세 가지 보완과 격리 검증을 완료했다. 실제 관리자 환경·구성/ledger 전환·원복·운영 적용은 아직 미완료**다.

고정 후보: `scheduled-clock-policy-20261016-544c61aca49f1355`.
전체 소스 SHA-256: `544c61aca49f13559861902bcd49bc28a503b18b7d2718c48c2139c152f56c65`.
[manifest](manifest.json)의 runtime 291개 중 이전 후보 대비 **3개 변경, 추가·삭제 0개**다.
이전 `5b02fc74867e5bab`의 manifest와 runtime 291개를 다시 대조해 보존을 확인했다.
[전후 파일 해시·실행 환경·운영 입력 대조](source-review.json).

## 구현

| 파일 | 보완한 동작 |
|---|---|
| [request_contract.py](candidate/policy/request_contract.py) | 읽기 전용 SQLite 연결을 `closing()`으로 명시적으로 닫는다. OI 관측을 정확한 fetch 행·전체 행 해시와 연결하고, 네트워크 결과는 JSONL의 고정 byte 구간·해시로 대조한다. |
| [daily_schedule.py](candidate/daily/packages/marketdata/src/marketdata/daily_schedule.py) | 오류를 보존할 때 원래 type/message/status와 처리 category를 함께 기록한다. 재개는 동일한 저장 분류를 사용하며, 알 수 없는 오류를 DataError로 생성하지 않는다. |
| [network_observer.py](candidate/base/scripts/network_observer.py) | 기존 일자별 JSONL을 확정한 뒤 해당 관측의 offset·length·SHA를 결과에 연결한다. latest는 계속 현재 상태 표시로 갱신한다. |

`job_clock.py`·`run_job.py`와 세 실행본의 시각 판단 소스는 이전 후보와 같다. reference 원값, 업무별 구간 판단, 감시·예외 정책, T05 U/기간/후행 사본 조건은 바꾸지 않았다.

### SQLite 수명과 실패 복구

연결을 정상 조회·행 없음·SQL 오류 모두에서 명시적으로 닫는다. GC 시점에 의존하지 않는다.

새 pending failure는 `lock`, `source`, `validation`, `review` 중 기존 처리 분류를 기록한다. 원래 오류 클래스 이름과 메시지도 보존한다. SourceError·DataError의 하위 클래스도 최초 분류를 유지한다. category가 없는 이전 기록은 알려진 기존 종류만 분류하며, OSError와 알 수 없는 종류는 점검 대상으로 유지한다. 알 수 없는 category는 자동 재시도하지 않는다.

실패의 task/attempt 최종 저장은 기존 `save_many()`의 동일 transaction을 유지했다. 성공 복구의 task·관련 refresh·attempt는 별도 저장인 기존 구조를 유지하되, 중간 재중단 후 같은 proof/attempt로 복구되는 회귀를 추가했다. 별도 저장을 원자적 일괄 저장이라고 주장하지 않는다.

### latest가 바뀐 뒤의 근거

OI는 `received_at`·`source_id`·`probe_id`로 기존 fetch 행 하나를 찾고, receipt 내용과 실제 결과를 대조한 후 행 ID와 전체 행 해시를 고정한다. 같은 원문 `[]`를 다시 받은 관측도 probe_id가 다르면 다른 관측이다. 후보가 추가하는 DB 테이블이나 인덱스는 없다. `received_at` 탐색은 기존 `fetches_observed` 인덱스를 사용한다.

네트워크 관측은 기존 JSONL에 쓰는 시점에 byte offset·길이·해시를 얻는다. 복구 시 전체 파일의 해시를 요구하지 않고 해당 구간만 읽는다. 이후 append, 다른 날짜 파일, latest 교체는 원래 관측의 근거를 바꾸지 않는다. 원래 구간의 누락·변조·잘못된 record 경계는 거부한다. 한글 등 UTF-8 다중 byte와 0이 아닌 offset도 검사했다.

두 경로 모두 저장된 근거를 다시 검증한 뒤 요청을 종결한다. 현재 latest를 대신 믿거나 원래 해시 검사를 제거하지 않는다. 원래 fetch 행·JSONL 구간이 없다면 기존 요청과 결과를 남긴 채 실패하며, 다시 관측하거나 정상 완료로 추정하지 않는다. 보조 request 자체의 전체 백업·독립 복구는 별도 후속이다.

## 실제 실행한 검증

[검증 집계·결과 XML 해시](validation.json), [재현 명령](validation-commands.json).
Python 3.12.3, 실제 임시 파일·SQLite·thread를 사용했다. 시각 provider와 외부 HTTP/네트워크 응답은 대역이다.

| 범위 | 이번 통과 |
|---|---:|
| base 정책·wrapper·CLI·저장 복구 | 78 |
| daily 정책·CLI·transaction/오류 복구 | 28 |
| preserve 정책·backup/T05/restic 연결 | 13 |
| 변경한 network observer의 기존 회귀 | 15 |
| daily scheduler·review 기존 회귀 | 82 |
| native systemd 구문·순서 | 17개 유닛 |

**정책·복구 119개 = 기존 90개 재실행 + 추가 29개**다. 앞선 조사 7개와 중간 시험 반복을 더하지 않는다. 기존 preserve 회귀 146개는 runtime 무변경을 대조한 뒤 이전 실행/XML 근거를 재사용했고, 이번 재실행 수에 합산하지 않았다. 공통 계약의 preserve 연결 13개는 이번 수정본으로 다시 실행했다.

추가 검사는 다음을 포함한다.

- SQLite 정상·누락·SQL 오류 후 실제 연결 종료 확인.
- 새 wrapper로 A 관측 저장 → 결과 seal 또는 monitor close 실패 → **실제 이전 /opt 실행본의 CLI**가 같은 시험 저장소에 B 관측 기록 → 새 wrapper로 A 요청 복구. 두 관측기와 두 중단 지점의 4개 조합을 확인했다. 복구에서 업무 본문 재호출, 최신 B 덮어쓰기, request ID/pins 변경은 없었다.
- 위 시험에서 이전 실행본은 `scheduled-manager-20261012-255d64b78ffa8328`의 Python·OI CLI·network observer다. 임시 저장소를 직접 호출했고 실제 manager 전환/서비스 실행은 하지 않았다. 이전 소스 해시와 실행된 모듈 경로를 대조했다.
- 원래 fetch 행 삭제/변조, 원래 JSONL 구간 삭제/변조, 모호한 중복 OI receipt는 복구 거부. latest가 유효하게 남아 있어도 이를 대체 근거로 사용하지 않았다.
- 반복 시각 보류 뒤 OSError·설정/프로그램 오류·DB 오류·검증/충돌·잠금·HTTP/429 및 하위 클래스의 분류·기록·예산 보존. category가 없는 이전 기록도 확인했다.
- 실패 transaction의 task UPDATE 후 attempt UPDATE 실패와, 성공 복구의 task 확정 직후/attempt 확정 직전 재중단. 추가 HTTP·revision·실패 예산 중복 없이 재개했다. 이 세 검사는 실제 SQLite commit/rollback과 예외 주입이며 별도의 새 SIGKILL 시험은 아니다. 기존 wrapper의 SIGKILL 회귀는 119개 안에서 재실행했다.

초기 관련 실행도 각각 21개·26개 통과했으며 [base 초기 결과](storage-base-initial.xml), [daily 초기 결과](storage-daily-initial.xml)에 보존한다. 최종 집계에 중복 합산하지 않는다.

## 운영 경계와 다음 단계

운영 구성 입력 19개는 이전 후보 준비 때와 동일하다. 같은 boot의 `closed`·보정 이력과 T05 네 mask를 읽어 확인했다. 운영 업무·Chrony·RTC 제어, 운영 데이터 쓰기, 삭제, 추가 백업, timer 활성화는 수행하지 않았다.

다음은 [관리자 환경·전환·원복 검토](deployment-review.md)다. 이번 두 관측기의 업무 복구 경로를 전체 manager/configuration/ledger 전환 검증 완료로 확대하지 않는다. 운영 적용은 후속 검증과 별도 승인이 필요하다.
