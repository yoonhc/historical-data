# 저장 근거·복구 경계 추가 대조

대상은 고정 후보 `scheduled-clock-policy-20261015-5b02fc74867e5bab`이다.
후보 runtime 291개 해시는 그대로이며, 이번에는 코드 수정 없이 대조했다. 운영 DB·서비스에는 접근하거나 쓰지 않았다.
[대조 결과와 환경](result.json), [입력·재현 코드](test_review.py), [실행 결과](results.xml).

## 확인 결과

1. `request_contract.record_bytes()`는 연결을 명시적으로 닫지 않는다. 실제 임시 SQLite 연결의 참조를 유지한 뒤 함수 반환 후에도 SELECT가 가능함을 확인했다. 운영 FD 고갈을 관측했다는 뜻은 아니다. `with connection`은 transaction을 관리하고 연결 종료를 담당하지 않는다는 [Python 3.12 문서](https://docs.python.org/3.12/library/sqlite3.html#how-to-use-the-connection-context-manager)와 일치한다. `contextlib.closing()`으로 좁게 수정할 항목이다.
2. OI/network `latest`의 정상적인 다음 관측 갱신 후, 이전 sealed 결과는 해시 불일치로 거부된다. 허위 완료는 없지만 원복 후 재적용에서 미완료 요청이 막힐 수 있다. OI는 `Store.put_raw()`의 fetches.metadata에 probe_id와 receipt가 남으며, network는 latest와 같은 관측을 일자별 JSONL에 추가한다. 이 이력을 원래 source/관측 ID와 해시로 대조하는 경로를 우선 검토한다. 아직 이를 사용한 자동 요청 복구나 실제 원복 시험은 구현·검증하지 않았다. 현재 latest를 대신 믿거나 해시 검사를 제거하지 않는다.
3. `failure()`의 마지막 task 갱신과 attempt 종결은 `Ledger.save_many()`의 동일 transaction이다. 복구 중 task UPDATE 이후 attempt UPDATE에서 SQLite trigger로 ABORT를 발생시켰다. task 실패 횟수는 0으로 rollback되고 pending_failure는 남았으며, 재개 후 1회만 반영됐다. 추가 HTTP도 없었다.
4. `finish()`의 task 완료, 관련 refresh 완료, attempt 완료는 한 transaction이 아니다. task 완료 직후와 attempt 완료 저장 직전의 두 경계에서 복구를 다시 중단시켰다. 새 Scheduler가 같은 저장 근거를 확인해 마무리했고, proof·attempt ID·revision 수·HTTP 수·실패 예산은 바뀌지 않았다. 이 경로는 원자적 일괄 저장이 아니라 기존 근거로 재실행 가능한 복구다. transaction 일반 성질은 [SQLite 문서](https://www.sqlite.org/lang_transaction.html)를 참고하되, 이 결론은 실제 후보 helper와 임시 DB 대조에 근거한다.
5. **추가로 확인한 수정 대상:** `recover()`의 pending_failure 복원은 알려지지 않은 오류 유형을 `DataError`로 대체한다. 동일한 `OSError('disk failure')`를 직접 처리하면 `needs_review`, validation_failures=0이지만, 시각 보류 뒤 복구하면 `validation_retry`, validation_failures=1이 된다. 실패 횟수의 transaction 원자성과 별개로 오류의 처리 의미가 달라진다. 기존 오류 유형의 처리 의미를 보존하고 알 수 없는 유형을 재시도 가능한 검증 오류로 바꾸지 않는 좁은 보완이 필요하다.

## 시험 해석과 다음 행동

7개 대조는 임시 파일·SQLite, 기존 시험 HTTP/시각 대역을 사용했다. 그중 연결 미종료·latest 변경 거부·OSError 분류 변경은 **현재 동작을 재현하도록 기대값을 설정한 조사**다. 7개 통과를 수정 완료로 해석하거나 기존 정책/복구 90개에 합산하지 않는다. 실제 프로세스 kill·systemd 인계·운영 장애 시험은 이번에 하지 않았다.

실행: 후보 보고서 루트에서 `candidate/daily/.venv/bin/python -B -I run_tests.py daily review-storage-boundaries/test_review.py -q --tb=short --junitxml=review-storage-boundaries/results.xml`.

다음 후보에서 SQLite close와 pending_failure 의미 보존을 수정하고, latest가 정상 갱신된 원복 경로를 기존 이력에 연결한다. 실패·완료 transaction 경계는 위 근거를 재사용한다. 변경 코드의 관련 회귀와 새 해시를 고정한 뒤 관리자 보호 환경·전환·원복 검증으로 진행한다. 시각 정책·기존 90개 검사의 범위·T05 정책은 유지한다. 운영 적용은 여전히 별도 승인 대상이다.
