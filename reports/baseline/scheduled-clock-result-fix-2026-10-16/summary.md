# T16 실제 OI 품질 결과 직렬화 보완

**운영에서 발견한 Decimal 직렬화 결함을 별도 소스에 수정하고 격리 검증했다. 이후 별도 승인된 계획으로 10/17 00:17:06 KST 정확한 한 요청 복구·수정본 전환을 완료했다.** 현재 운영은 `686bb70c9ee4ef11`이며 [실제 복구·새 품질 실행·잔여 검증](../scheduled-clock-repair-deploy-2026-10-16/status.md)을 따른다. 아래는 소스 보완과 당시 복구 검토의 근거다.

고정 소스 후보: `scheduled-clock-policy-20261016-686bb70c9ee4ef11`.
전체 SHA-256: `686bb70c9ee4ef11d9845e8e41cc31a4d846fb2dc8881e9d9640d7f5fbd4ce60`.
[manifest](manifest.json), [정확한 diff](runtime.patch), [검증](validation.json).

## 원인과 수정

실제 OI 비교 행은 quantity/notional을 Decimal로 읽고 기존 domain serializer는 정확한 문자열로 저장한다. 새 wrapper/계약 serializer는 비 JSON 값을 모두 날짜로 취급했다. 기존 빈 저장소의 CLI 시험에는 Decimal 비교 행이 없어 이 경계를 놓쳤다.

보고서 저장 뒤 결과 seal·최종 request/attempt 기록이 실패했다. JSONL/systemd에는 오류가 남았지만 디스크 request는 pending이다. 잘못된 완료로 보고하지 않았다는 것과 요청 복구가 끝났다는 것은 다르다.

`policy/request_contract.py`와 `policy/run_job.py`의 encoder만 변경했다. datetime/date는 기존 ISO 표현, Decimal은 기존 domain과 동일한 정확한 문자열로 기록한다. float 변환·포괄적 객체 문자열 변환은 하지 않고 알 수 없는 타입은 TypeError로 거부한다. 일반 JSON/날짜의 기존 byte 표현은 유지한다.

runtime 291개 중 **2개 변경, 추가/삭제 0개**다. job_clock·관측/감시·시각 판단·업무별 자료 처리와 종결·T13/T05는 그대로다. 이전 후보 291개 해시를 대조한 뒤 복사했고 운영 /opt는 수정하지 않았다.

## 검증

기존 검증 환경의 Python 3.12에 새 source 경로를 명시했다. 새 최종 /opt 설치 확인은 아니다. 실제 임시 저장소·Parquet/SQLite·CLI·thread·파일을 사용했고 시각 provider/외부 응답은 대역이다.

| 범위 | 통과 |
|---|---:|
| base 정책·wrapper·CLI·저장 복구 및 새 숫자 경계 | 82 |
| daily 정책·원장 복구 | 28 |
| preserve backup/T05/restic 연결 | 13 |
| 합계 | 123 |

**기존 116+추가 7**이며 최초 숫자 검사 7개 재실행을 합산하지 않는다. 새 구성/유닛은 아직 생성하지 않아 rendered 계약 3개를 이번 검사에 포함하지 않았다. 과거 관리자 시험을 새 runtime의 관리자 재검증으로 표시하지 않는다.

새 검사는 실제 OI 데이터/누락 행의 Decimal 보고서, 저장 보고서와 결과 일치, 같은 입력의 원래 encoder 실패, 소수 18자리·큰 값·trailing zero 보존, 기존 JSON/date byte 호환, 미지원 타입 거부, seal/close 장애 후 재계산 없는 복구를 포함한다. 운영 보고서는 사본만 읽어 기존 계약으로 분류했다.

## 미완료 운영 요청의 복구 경계

[원본과 해시](operating-inputs/sources.json), [정확한 복구 검토](recovery-review.json)를 보존했다. request `a702240f…`와 report `4257e580…`는 같은 request/attempt/invocation의 oi_quality_as_of 관측 전체, 고정 범위, report 자체 digest, 현재 입력 scope로 대조했다. 연결 시 판정은 report_written_with_findings/파생 exit 2이며 자료 정상 판정이 아니다. 원래 CLI가 실제 exit 2를 반환했다고 기록하지 않는다.

원래 request에는 저장 결과 포인터가 없다. 코드만 바꾸면 복구된다고 보지 않는다. 새 코드/설정 identity가 달라지므로 다음이 필요하다.

1. 원래 request·실패 JSONL·보고서·해시를 보존하고 정확한 저장 보고서의 연결 절차를 검증·고정한다.
2. 원래 ID·pins를 유지한 채 저장 근거를 재검증해 종결한다. 보고서를 다시 계산하거나 request를 지워 빈 상태로 만들지 않는다.
3. 현재 구성과 수정 후보로 별도 전환 도구/계획을 고정하고 그 변경의 승인을 받는다. 이전 승인을 자동 승계하지 않는다.
4. 정기 OI 품질 실행과 남은 backup/T05/restic 연결을 확인한다.

이번에는 읽기 전용 연결 대조와 수정 코드의 격리 검증까지만 실행했다. 운영 request 수정·서비스 재실행·timer 중지·새 후보 설치는 하지 않았다. 시각 정책·T05·Chrony·수집기 범위를 확대하지 않는다.

## 23:24 KST 영향 범위 재확인

[8개 업무 기록 대조](impact-review.json)에서 현재 request, 저장된 attempt, decisions JSONL을 읽었다. 동일 직렬화 오류는 기존 OI 품질 요청 한 건만 확인됐다. 다른 6개 업무의 저장된 종료 기록에서는 같은 오류가 없었고, backup은 새 wrapper의 요청·실행 기록이 아직 없어 성공 범위로 세지 않는다. 파일별 읽기 시점의 관찰이며 실행 manager 상태나 전체 업무의 저장 근거를 다시 검증한 결과는 아니다.

원래 request와 품질 보고서의 SHA-256은 복구 검토 값과 여전히 일치한다. request는 `pending`이며 `work_result`·`unsealed_result` 필드가 모두 없다. 복구 순서는 **원래 scope에서 정확한 저장 결과를 연결·재검증해 요청을 종결한 뒤, 새 구성/identity로 전환**하는 것으로 정한다. 과거 exit 1·실패 이력과 보고서 원문은 보존하고, 복구에서 도출한 코드와 과거에 관측한 반환값을 구분한다. 이 순서의 복구 도구·중단/반복 실행 검증과 새 전환 계획은 이후 [별도 준비 결과](../scheduled-clock-repair-deploy-2026-10-16/summary.md)에 연결했다. 읽기 전용 대조와 계획 고정은 운영 쓰기 승인이 아니다.
