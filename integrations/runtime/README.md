# 수집·부팅 runtime 개발 소스

이 디렉터리는 10/25 현재 운영 고정본의 runtime을 루트 개발 소스에 통합한 것이다.
현재 서비스는 계속 `/opt/marketdata/releases/`의 기존 실행본을 사용한다.

| 파일 | 역할 |
|---|---|
| `capture_runtime.py` | 수집 supervisor와 시각 근거·child 경계 |
| `marketdata_runtime.py` | 프로필·환경·관측 검사와 child 명령 |
| `clock_gate.py` | 기존 부팅 준비·RTC 확인·엄격한 시각 gate |
| `bootstrap_system.py`, `systemd_read.py` | 시스템 예약 업무 inventory를 포함한 bootstrap guard |
| `render_units.py` | 수집 시스템 유닛의 설치 없는 출력 |
| `render_bootstrap.py` | bootstrap 구성 출력. 시스템 업무 구성에는 명시적인 `--system-topology`를 함께 제공 |
| `seal_runtime.py` | 수집용 비-editable 설치 패키지·runtime의 코드 receipt 생성. 통합 배포 전체의 manifest는 아님 |

`profile.example.json`, `rtc-utc-confirmation.example.json`, `user-unit-inventory.json`,
`chrony.conf`는 예시·회귀 입력이다. 현재 운영 설정으로 간주하거나 설치하지 않는다.
유닛 생성은 설정·승인·ledger 전환과 별도이며, 현재 authority를 새 소스에 재사용하지 않는다.

통합본의 예약 업무는 루트 `packages/marketdata/src/`를 읽고 수집 runtime은 설치 패키지를 읽는다.
다음 배포에서는 두 사본의 상대 파일 목록·해시 일치와 모든 실제 진입점의 manifest 포함을 확인해야 한다.
기존 `seal_runtime.py`만 실행해 이 확인을 마쳤다고 표시하지 않는다.
[최종 검토와 임시 환경 대조](../../reports/baseline/source-integration-2026-10-25/final-review.md)에 범위를 기록했다.

개발 검사는 루트 `tests/test_runtime_candidate.py`, `test_clock_bootstrap*.py`,
`test_clock_evidence_policy.py`, `test_unified_source.py`에 있다.
정확한 운영 경로는 [구조 안내](../../docs/project-structure.md)를 따른다.
