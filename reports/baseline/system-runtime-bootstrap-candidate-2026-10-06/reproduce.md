# 격리 재현

운영 데이터·설정·systemd unit을 설치하거나 시각을 변경하는 절차가 아니다.
Python 3.12.3/uv 0.12.3, 일반 사용자, Linux 환경을 기준으로 한다.

1. `release.json`의 archive_sha256을 대조한다.
2. 비어 있는 새 `/tmp` 디렉터리에 `source.tar.gz`를 추출하고, source_files_sha256의 169개 파일을 대조한다.
3. 추출한 디렉터리에서 다음을 실행한다. 기존 가상환경을 복사하지 않는다.

   ```bash
   uv sync --frozen --no-editable --link-mode copy
   .venv/bin/python -I integrations/runtime/seal_runtime.py
   .venv/bin/python -I -m pytest -q tests/test_clock_bootstrap.py tests/test_runtime_candidate.py tests/test_depth*.py tests/test_aggtrade*.py tests/test_stream_guard.py
   ```

4. 위 결과의 기대 건수는 285건이다. 실제 host RTC/Chrony 상태가 아닌 격리 입력을 사용한다.
   실제 root clock 제어를 시험하지 않는다. 운영 의존성만 만들 때는 `uv sync --frozen --no-editable --no-dev
   --link-mode copy` 뒤 seal_runtime을 다시 실행한다.
5. 사용자 systemd가 있는 환경에서 아래는 무작위 이름의 시험 unit 두 개를 시작/종료한다.
   외부 시간원·chronyc·실제 시각 변경을 사용하지 않는다. 필요한 경우 sandbox 밖의 user bus 접근 허용만 한다.

   ```bash
   .venv/bin/python -I integrations/runtime/isolated_bootstrap_test.py test --output /tmp/bootstrap-isolated-result.json
   ```

6. 새 machine-profile/boot-profile/units는 설치 대상 경로를 포함한다. 시험 경로에 그대로 설치하지 않는다.
   `render_bootstrap.py`에 머신 runtime profile·user-unit-inventory를 주어 **비어 있는 출력 디렉터리**에
   렌더링할 수 있다. 이 도구는 `/etc`, service, 시각을 변경하지 않는다.

이전 후보 archive에 candidate.patch를 적용하는 방법도 별도 검증했다. 패치는 169개 소스 해시를 재현하며,
새 `candidate.json` 배포 메타데이터는 새 archive의 것을 사용한다. 이전 원본 후보 자체에 패치하지 않는다.
이전 release의 읽기 전용 파일을 별도 작업 경로로 옮기고 그 복사본에만 적용한다.
