# 보완 후보 재현

대상: `system-bootstrap-20261007-f85eb8321a19c1ee`. 운영 설치 절차가 아니다.
Python 3.12.3, uv 0.12.3, lockfile의 의존성을 사용한다.

1. [release.json](release.json)의 archive SHA256을 확인한다. 새 빈 경로에 source.tar.gz를
   추출하고 source_files_sha256의 172개 파일을 모두 대조한다.
2. 추출 경로에서 비 editable 설치와 검사:

   ```bash
   uv sync --frozen --no-editable --link-mode copy
   .venv/bin/python -I integrations/runtime/seal_runtime.py
   .venv/bin/python -I -m pytest -q tests/test_clock_bootstrap*.py tests/test_runtime_candidate.py tests/test_depth*.py tests/test_aggtrade*.py tests/test_stream_guard.py
   ```

   기대 결과는 297건 통과다. 이번 실제 재현에서는 `/tmp/marketdata-system-runtime-20261006/uv-cache`의
   고정 캐시로 offline 설치했다. 다른 환경에 이 캐시가 있다고 가정하지 않는다.
3. 실제 Chrony 4.5 client의 응답 유실 시험:

   ```bash
   .venv/bin/python -I integrations/runtime/isolated_chronyc_requests.py --chronyc /절대/경로/chronyc --output /tmp/chronyc-wire-result.json
   ```

   이 도구는 새 /tmp Unix socket만 만들고 응답을 버린다. 실제 chronyd를 시작하거나 운영 socket에
   연결하지 않는다. 기존 호출은 3초 내 재전송이 있고, 현재 Host.step은 요청 1회여야 한다.
4. 사용자 systemd가 있는 환경에서 8개 가짜 업무와 coordinator/waiter의 실제 연결:

   ```bash
   .venv/bin/python -I integrations/runtime/isolated_job_gate_test.py test --output /tmp/job-gate-systemd-result.json
   ```

   무작위 이름의 `md-clock-jobs-*` unit만 runtime link/start/stop/unlink한다. 기존 시장 데이터 명령은
   실행하지 않고 /tmp에 실행 근거만 쓴다. 시각과 step은 JSON fixture다. 업무 timeout 0.5초보다 긴
   대기에서 본 업무/재시도 0회, 준비 회복 후 8개 각각 실행, 다음 활성화의 새 대기, 업무 실패의 제한된
   재시도를 확인한다. 실제 root unit 보호 환경이나 calendar/Persistent 새 부팅 시험을 대체하지 않는다.
5. [verify_units.py](verify_units.py)에 --source, --report, --chrony-package를 명시하면 배포판의
   원래 chrony/chrony-wait/chronyd alias와 새 drop-in을 합쳐 offline transaction을 검사한다.
   실행 파일이 없는 현재 환경의 경로만 시험 경로로 바꾸고, service 활성화는 하지 않는다.
6. [candidate.patch](candidate.patch)는 직전 bootstrap 후보 archive를 별도 새 경로에 풀어
   `git apply --check` 및 `git apply`로 적용한다. 새 파일 mode도 포함한다. 172개 해시가 새 release와
   같아야 한다. candidate.json 메타데이터는 새 archive 것을 사용한다. 원래 보관 후보에 직접 적용하지 않는다.
7. 운영 의존성만 남길 때는 `uv sync --frozen --no-editable --no-dev --link-mode copy` 후
   seal_runtime을 다시 실행한다. 이번 깨끗한 경로에서 12개 배포 의존성과 실제 venv import를 확인했다.

root 보호 설정 아래 Unix socket 생성·runuser·상태 쓰기·공유 잠금, 실제 시간원·RTC·재부팅은
[전환 검토](deployment-review.md)의 별도 단계다. 관리자 인증이 없는 이번 결과로 완료 처리하지 않는다.
