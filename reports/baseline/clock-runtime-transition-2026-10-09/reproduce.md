# 고정 후보의 재현 범위

최종 성공 근거는 이미 확보했다. 아래는 재현 방법이며 같은 전체 시험을 다시 수행해야 하는 대기 항목이 아니다.
운영 경로/서비스는 쓰지 않고 새 시험 폴더를 사용한다. 최초 실패 XML·root 결과·원래 후보는 덮어쓰지 않는다.

## 수집 후보

`collector-correction/source.tar.gz`의 SHA256:
`0c1ae174206c984dc73ac8d2572d2cf4bad9283c72132e8d0000145c1ba40841`.
새 빈 디렉터리에 Python `tarfile.extractall(..., filter='data')`로 추출하고,
`collector-correction/release.json`의 175개 `source_files_sha256`을 대조한다.
`candidate.json`의 ID는 `clock-evidence-policy-20261009-65296619a374c325`여야 한다.

그 새 디렉터리에서 실행한다.

```sh
uv sync --offline --frozen --no-editable \
  --reinstall-package marketdata --reinstall-package marketdata-freqtrade \
  --cache-dir /home/yoonhc/projects/algoTrade/historical_data/.cache/uv
.venv/bin/python -I integrations/runtime/seal_runtime.py
CLOCK_TEST_RESTIC=/home/yoonhc/projects/algoTrade/historical_data/.cache/restic/0.19.1/restic \
.venv/bin/python -I -m pytest -q \
  tests/test_clock_evidence_policy.py tests/test_runtime_candidate.py \
  tests/test_clock_bootstrap.py tests/test_clock_bootstrap_revision.py \
  tests/test_backup.py tests/test_incremental_backup.py tests/test_aggtrades.py \
  tests/test_depth.py tests/test_depth_failure_publication.py \
  tests/test_depth_reference.py tests/test_depth_independent_cases.py \
  tests/test_stream_guard.py --junitxml=clock-candidate-tests.xml
```

확인한 결과는 **305 passed, 0 skipped**다. offline cache가 없는 장비에서는 고정 lock의 dependency를
별도로 확보해야 하며 lock을 새로 생성하지 않는다. restic 환경변수 없이 통합 검사가 skip된 결과를
같은 검증으로 세지 않는다. 실제 설치 모듈이 새 venv에 있고 소스와 같은지 및 non-editable 여부를 대조한다.
이번 설치/시험 환경의 [봉인 manifest](collector-correction/verified-runtime-manifest.json)는 37개 파일이다.
원래 후보와의 [차이](collector-correction/correction.patch)를 역으로 숨기거나 원래 버전에 새 ID를 붙이지 않는다.

## 전환 도구

`transition-source-final.tar.gz`의 SHA256:
`4898fc07a2458880e59c1bbb1b1040e73623c32b5951360e9e5564761f60dec4`.
새 빈 디렉터리에 안전하게 추출하고 `release-final.json`의 9개 파일 해시와 `candidate.json`의 ID를 대조한다.
그 디렉터리에서 기존 시험용 Python으로 실행한다.

```sh
/home/yoonhc/projects/algoTrade/historical_data/.venv/bin/python -I -m pytest -q \
  test_transition.py --junitxml=transition-tests.xml
```

결과는 **58 passed, 0 skipped**다. 실제 fsync/flock/SIGKILL은 시험 경로/프로세스에만 사용한다.
서비스/provider 응답은 대역이다. 운영 계획 생성은 root 소유권을 실제로 읽을 수 있는 환경의 읽기 전용
동작으로 수행했으며, stage/commit/rollback은 이번에 운영에 실행하지 않았다.

## 기존 버전과의 저장·복원 조합

[mixed_versions.py](mixed_versions.py)는 서로 다른 Python 실행본을 실제로 실행한다.
최종 결과에 실행 경로와 6개 단계의 종료 코드가 있다.

- `NEW`: 수정 후보를 비 editable 설치한 `/tmp/clock-shutdown-correction-cpmkuynr`.
- `OLD`: 운영 중인 기존 고정 release `/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee`.
- `ROOT`: 기존 pack/backup/restic과 해당 `.venv`가 있는 workspace.
- `REPORT`: 이번 결과 폴더. 재현할 때는 원래 결과를 덮지 않는 새 폴더로 지정한다.

재현 복사본에서는 위 네 경로 상수만 설치한 실제 경로로 명시한다. 시험은 매번 새 `/tmp/clock-mixed-*`
저장소·restic repository·임시 암호를 만들며 운영 저장소나 암호를 인자로 받지 않는다.
`NEW`의 pytest는 이전 실행본에서 교환 입력을 제공하는 시험 대역 용도로만 추가한다.
실제 수집 module은 선택한 이전/새 실행본에서 import되는지 결과로 확인한다.
미래 날짜 검사를 우회하지 않도록 fixture checkpoint는 현재보다 이전인 2026-10-07/08로 고정했다.
같은 입력의 재시도 대신 두 수집에 서로 다른 update ID를 사용하며, 동일 segment 경계 사례는 별도
[이전 코드 대조](duplicate-fixture-control.json)로 보존한다.

## 실제 systemd 권한·종료 접점

[system_preflight.py](system_preflight.py), [worker](system_probe_worker.py),
[검토 입력 해시](system-preflight-inputs.json), [환경 inventory](system-preflight-inventory.json)를 보존했다.
시험은 root 소유의 전용 `/opt/md-clock-probe-<nonce>` 코드와 `/var/lib/md-clock-probe-<nonce>` 데이터,
`md-clock-probe-<nonce>-*.service`만 만들고 정리한다. 최종 보호 옵션·일반 계정 실행을 유지한다.
고정 환경 archive는 `/tmp/clock-system-probe-environment-corrected.tar.gz`였으며 hash가 입력 문서에 있다.

나중에 `/tmp`가 없어지면 새 후보를 위와 같이 설치하고 [bundle 생성기](build_system_probe_bundle.py)의
시험 경로를 맞춰 새 inventory/input을 만들어 **새 시험 입력으로** 검토한다. 기존 결과의 입력 해시를
새 값으로 덮어쓰지 않는다. 보호 옵션이나 UID 신뢰 조건을 낮추지 않는다.

이번에는 정상 종료 성공(세 번째 실행)과 kill-only 성공(네 번째 실행)을 각각 근거로 쓴다.
운영 서비스·Chrony/RTC를 제어하거나 시험을 위해 시계를 틀리지 않았다.
실제 운영 전환은 [별도 승인 범위](deployment-review.md)를 따른다.
