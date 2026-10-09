# 최종 후보 재현

운영 경로·설정·서비스에 쓰지 않는다. 별도 빈 시험 폴더에서 진행한다.

1. `source-final.tar.gz`의 SHA256이
   `7296234b12dc09fbfdc1b90b0efdb54d65f7bf8b3742a432b3c1248f8d7ccfd1`인지 확인한다.
2. 안전한 tar 추출(`filter='data'`)로 빈 폴더에 풀고 `release-final.json`의 source 175개 해시를 대조한다.
3. 해당 폴더에서 frozen lock으로 non-editable 설치한다. 기존 소스를 편집한 뒤 `uv sync`만 하면
   설치본이 갱신됐다고 보장할 수 없으므로 두 로컬 package를 명시적으로 재설치한다.

```sh
uv sync --offline --frozen --no-editable \
  --reinstall-package marketdata --reinstall-package marketdata-freqtrade \
  --cache-dir /home/yoonhc/projects/algoTrade/historical_data/.cache/uv
.venv/bin/python -I integrations/runtime/seal_runtime.py
```

이 호스트에서 확보된 offline cache를 사용했다. 다른 호스트에는 lock에 맞는 dependency 확보가 필요하다.
`uv.lock`을 갱신하지 않았다. `--locked`가 상위 lock 갱신을 요구했던 초기 시도와 달리
기존 배포 방식인 `--frozen`으로 재현했다.
실제 import 경로가 새 venv 안인지, source와 설치 package의 모든 `.py` 바이트가 같은지도 확인한다.
[최종 대조](clean-final-imports.json).

```sh
CLOCK_TEST_RESTIC=/home/yoonhc/projects/algoTrade/historical_data/.cache/restic/0.19.1/restic \
.venv/bin/python -I -m pytest -q \
  tests/test_clock_evidence_policy.py tests/test_runtime_candidate.py \
  tests/test_clock_bootstrap.py tests/test_clock_bootstrap_revision.py \
  tests/test_backup.py tests/test_incremental_backup.py tests/test_aggtrades.py \
  tests/test_depth.py tests/test_depth_failure_publication.py \
  tests/test_depth_reference.py tests/test_depth_independent_cases.py \
  tests/test_stream_guard.py --junitxml=clock-candidate-tests.xml
```

최종 결과는 304 passed, 0 skipped다. restic 변수 없이 실행하면 해당 실제 시험이 skip되므로
동일한 검증으로 세면 안 된다. 시험은 자체 임시 저장소·임시 암호를 만들고 운영 repository를 사용하지 않는다.
실제 SIGKILL은 시험이 만든 프로세스 자신에게만 보낸다. Chrony 관측은 합성이며 호스트 시각을 변경하지 않는다.

새 파일 3개의 ruff check도 통과했다. 단위·회귀 시험은 root 시스템 유닛의 실제 권한·보호 옵션이나
운영 NTP의 품질을 검증하지 않는다. [운영 적용 전 남은 범위](deployment-review.md)를 별도로 따른다.

`release/`, `release.json`, `source.tar.gz`, `candidate.patch`, `clean-regression.xml`은
배포하지 않은 초기 `a2cddb95dd879544` 후보의 근거다. **최종은 이름에 `final`이 있는 산출물**이다.
초기 원본과 실패 XML은 기록으로 남겼으며, 이 파일들을 최종 성공 근거와 섞지 않는다.
