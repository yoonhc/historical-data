# T18 첫 재발 방지 수정 — 성공 manifest 출력의 중복 할당 제거

**이 문서는 최초 후보와 당시 검증 근거다. 10/22 동기화 재호출·`..` 방어 보완은 [새 후보와 검사](../t18-output-durability-2026-10-22/summary.md)로 연결한다.** 아래 후보·해시·실패/성공 결과는 원본으로 보존한다. 운영 미설치·archiver timer 재개 미승인이며 조회 주기와 검증 정책은 변경하지 않는다. 긴급 공간 확보와 수집 복구 결과는 [status.md](status.md)에서 따로 확인한다.

## 구현 범위

- [공유 본문 보관 함수](../../../scripts/archive_output.py): 실제 `dump <snapshot> /backup.json`이 exit 0으로 끝나고 기대 manifest SHA-256과 일치한 뒤에만 본문을 공유한다. repository ID·본문 hash·UID/GID·mode별로 한 벌을 보관한다.
- [workspace 호출부](../../../scripts/archive_backups.py)의 `Archive.match()`는 매번 기존 restic 조회와 기대 해시 검사를 실행한다. 과거 조회 결과를 재사용하지 않는다.
- 실행별 UUID stdout 경로와 원래 바이트·권한을 남기되 같은 본문에 하드링크한다. 각 command receipt의 실제 시작/종료·snapshot·기존 stdout 경로를 보존하고, 본문 길이/SHA-256/공유 경로와 `fresh_dump_expected_hash_match`를 작은 항목으로 추가한다.
- 새 본문·권한이 다르면 별도 객체다. 실패·불일치·중단 출력은 기존 독립 파일로 남기고, 공유 본문이 변조됐거나 경로/권한이 다르면 거부한다. 이름이 같다는 이유로 기존 본문을 덮어쓰지 않는다.
- 원자 링크 교체 전후와 receipt 기록 실패에도 해당 command의 기존 stdout 경로에서 정확한 바이트를 읽을 수 있다. 현재 command의 최종 기록 전 단계만 갱신하며, 이미 종결된 다른 run/request/receipt를 수정하지 않는다.

정상 반복의 큰 본문 추가 할당을 막는 범위다. 매 조회 동안 임시 독립 stdout 한 벌은 필요하며 검증 후 공유한다. 일반 로그 전체의 기간/총량 제한, 실패 출력 누적 상한, 보조 기록 백업/회전, 과거 manifest 조회 빈도 감소는 이번 구현에 포함하지 않는다. 향후 보관 정리는 request/receipt가 참조하는 stdout과 공유 본문을 함께 고려해야 한다.

## 실제 설치본 연결

workspace의 기존 archiver에는 설치본의 T05 후처리가 모두 들어 있지 않으므로, workspace 파일을 그대로 설치 대상으로 삼지 않았다. 현재 고정 `/opt/.../preserve/scripts/archive_backups.py`(SHA-256 `69455f8e…`)에 **import와 `Archive.match()`의 출력 보관 호출만** 추가한 [설치본 기반 후보](candidate/archive_backups.py)와 [동일 helper](candidate/archive_output.py)를 만들었다. `clock_bundle`, 완료 근거 조회·reconcile·저장 연결은 원본 그대로다.

정확한 [원본/후보 해시](candidate-source.json), [전체 차이](installed-archiver.patch), [재현 빌더](build_candidate.py)를 보존한다. 후보 helper는 `33c3c560db5ca30215a677bbb1c6930aa879314e4dc41bd302cd1c686fca91f1`, 설치본 기반 archiver는 `941d425e5fab10272b92608de88c225f0d10ee592f85e29dccebf476424dee06`이다. 고정 운영 실행본을 제자리 수정하지 않았다.

## 검증과 측정

| 범위 | 실제 결과 |
|---|---|
| workspace 출력 보관 + 기존 실제 restic 회귀 | **45개 통과**: 새 보관 검사 15개와 archive 회귀/신규 연결 30개. [JUnit](candidate-tests.xml) |
| 설치된 preservation package와 후보 두 파일 연결 | 15개 보관 검사와 실제 restic 반복 no-op 검사 통과. T05 bundle/기존 요청 연결 1개는 시험 보고서 이름을 설치 계약의 `<run_id>.json`에 맞춘 뒤 통과. [최초 16 통과·1 실패 원본](installed-candidate-tests.xml), [해당 1개 재검사](installed-candidate-connection-tests.xml) |
| 실제 restic no-op 반복 | 같은 snapshot 2개를 3회 다시 실행하여 **새 dump 6회**와 별도 command receipt를 확인. 본문 inode/할당량은 유지하고 기존 repository 전체 검사 기록은 변하지 않음 |
| T05·요청 연결 | 실제 격리 repository에 완료 bundle을 저장하고 지정 snapshot에서 읽어 해시/backup ID 대조. 다음 no-op 뒤에도 설치 request contract로 이전 결과 종결 가능. 재다운로드·운영 사본/시각 변경 없음 |
| 할당량 측정 | 약 1MiB의 같은 성공 본문을 20회 처리해 본문 **1벌**, 첫 회 이후 추가 **155,648바이트**. 디렉터리 할당 포함. [측정 원본](candidate-growth-probe.json) |
| 정적 검사 | 변경한 workspace 파일의 ruff check 및 git diff --check 통과 |

45개와 설치 조합 17개는 일부 같은 입력을 다른 조합으로 실행한 것이므로 전부 독립된 62개 사례로 합산하지 않는다. 설치 조합의 최초 실패는 시험이 `run.json`을 만들고 `run_id=isolated`로 제출해 계약이 정확히 거부한 것이며, 운영 계약이나 거부 조건을 바꾸지 않았다. 실제 systemd 전환·최종 경로 설치/guard·timer 재개 검증을 마쳤다는 의미도 아니다.

## 다음 운영 범위

후보를 고정 실행 경로·현재 업무 entrypoint와 연결하는 최소 배포안을 준비해야 한다. 현재 완료 request와 새로운 코드/실행 경로의 identity, 유닛/boot guard에 필요한 연결 변경, 원복 시 새 receipt/공유 본문 보존을 대조한다. 현재 T05·Chrony·수집기 정책 변경을 이번 후보에 포함하지 않는다.

배포 및 timer 재개는 **별도 승인 전 실행하지 않는다**. 재개 직전 현재 유휴·잠금·미보관 사본·미완료 요청을 다시 확인하고, `OnActiveSec=5s`·`Persistent=true`의 즉시 실행 가능성을 명시한다. 이후 10/20·21 미보관 사본 처리와 실제 반복 출력 할당량을 확인해야 한다. 과거 전체 출력 정리나 일반 로그 정책 완성을 이 보존 지연 해소의 선행 조건으로 삼지 않는다.
