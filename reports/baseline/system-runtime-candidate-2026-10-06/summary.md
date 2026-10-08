# 시스템 수집 서비스·Chrony·고정 배포본 후보

**배포 후보와 격리 검증을 완료했다. 운영 설치·수집 중지·시간 서비스 교체·RTC 변경·재부팅은 하지 않았다.**
사용자 서비스에 임시 대기만 붙이는 이전 제안에서, 현재 PC와 향후 Proxmox VM이 공유할 운영 구조로
범위를 바꿨다. 사용자는 현재 수집 PC가 Windows 등 다른 OS와 듀얼부팅이라고 확인했다.

후보 ID는 **`system-runtime-20261006-c09bafd0645578a3`**다.
[배포본과 전체 해시](release.json), [고정 소스](release/), [재현 묶음](source.tar.gz),
[운영 전환·되돌리기 검토](deployment-review.md)를 함께 보관한다.
묶음 SHA256은 `04420219b8167fb55995e7c3c231de38bc9f53749a408ef8fee9a0e128dc09ad`다.

## 구현한 동작

| 항목 | 후보 동작 |
|---|---|
| 서비스 관리자 | 시스템 systemd, `User=yoonhc`; depth/aggTrade 두 instance |
| 실행 코드 | 새 release의 비 editable venv, `python -I`; 개발 폴더 import 거부 |
| 부팅/재시작 준비 | 실제 Chrony tracking·현재 invocation·명시적 설정 해시, data UUID·접근, 필수 DNS 확인 |
| 준비 실패 | 수집 child를 시작하지 않음; monotonic 시간 제한 뒤 systemd가 재시도 |
| 시간 서비스 정책 | steady Chrony에 자동 step/RTC 쓰기 없음; 큰 보정은 별도 통제 절차 |
| 실행 중 감시 | UTC/monotonic/boottime 관측, 시각 이동·관측 공백·provider 변경을 별도 JSONL에 기록 |
| 시각 이상 | 원문 child 유지, 해당 실행의 `clock_usable=false` 유지; 기존 partial/replay를 바꾸지 않음 |
| 기록 저장 장애 | 감시 근거를 쓸 수 없으면 child에 SIGINT, 종료/서비스 오류 처리 |
| 상태 조회 | 현재 boot·프로필·PID 시작 tick·child 생존·신선도를 함께 확인 |
| 정상 종료 | child SIGINT와 최대 120초 저장 종료 대기; systemd cgroup으로 잔류 프로세스 관리 |

[구현 계약과 초기 수치](release/integrations/runtime/README.md)에 조건과 보장 범위를 적었다.
초기 값은 남은 보정 100ms, 조건부 오차 상한 500ms, reference age 600초다. 1초마다 시계 차이를
관측하고 10초마다 Chrony를 조회한다. **이 호스트의 Chrony 실측으로 최적화한 값이나 메시지별
정확도 인증은 아니다.** 시각 이상 관측 수와 실제 장애 사건 수도 구분한다.

후보를 만들면서 배포판 기본 `makestep 1 3`·`rtcsync`, AppArmor의 `/etc/chrony/**` 읽기 범위를
확인했다. 그에 맞춰 자동 큰 보정·RTC 쓰기를 제외하고 설정 경로를 `/etc/chrony/marketdata.conf`로
정했다. 배포판 daemon sandbox와 chrony-wait를 활용하면서, 오래된 최초 통과 표시를 재사용하지 않는다.

## 실제 검증 결과

| 검사 | 결과와 정확한 범위 |
|---|---|
| 신규 runtime 검사 | **49건 통과**: 준비 회복·실패, 시계 이동/정지 관측, 상태 유효성, 저장 실패, 원래 stream flock 등 |
| 기존 depth/aggTrade 회귀 | **199건 통과**: 상태 선행 공개·두 실제 사건의 독립 계산·연결/복구/요청 정책 등 |
| 최종 합계 | **248건 통과**, 13.42초 |
| 다른 빈 경로 재현 | 고정 묶음 163개 파일 해시 대조 → 새 비 editable 설치 → **248건 통과**, 13.99초 |
| 운영용 의존성 구성 | 재현 환경에서 `--no-dev`로 12개 배포 의존성만 유지, 새 설치 receipt·실제 import 경로 확인 |
| systemd 유닛 구문 | 설치하지 않은 후보 collector unit `systemd-analyze verify` 종료 0, 경고 없음 |
| 실제 systemd 재시도 | 임시 사용자 유닛: 준비 실패 **2회 재시도**, child 없음 → 조건 회복 후 시작 |
| 실행 중 이상·종료 | 같은 임시 유닛: 가짜 provider 변경 뒤 child 유지·clock 거부 → SIGINT 최종 쓰기·잔류 child 없음 |
| Chrony 실제 바이너리 | 4.5-1ubuntu4.2, 일반 UID 1000, `-x -U`, **CapEff=0**, 루프백 가짜 NTP의 CSV 파싱 성공 |
| 실제 경로 접근 | 후보 venv로 기존 data root의 파일 접근·UUID를 읽기 전용 확인. DB를 열지 않음 |
| 실제 CLI 시작 거부 | 준비되지 않은 환경에서 `wait-ready` 종료 **75**, 수집 child 0개 |

근거: [검증 기록](validation.json), [처음 확정한 회귀](regression.xml),
[깨끗한 경로 재현](clean-regression.xml), [설치 receipt](installation-receipt.json),
[유닛 구문](unit-verify.json), [systemd 실행](systemd-isolated.json),
[시각 상태 기록](systemd-evidence/), [Chrony 시험](chrony-isolated.json), [CLI 점검](cli-readiness.json).

systemd 동작 시험은 **사용자 transient unit + 가짜 collector/provider**다. 실제 root system unit의
모든 sandbox 옵션, 실제 거래소 연결, 호스트 시계 제어까지 검증한 결과로 확대하지 않는다.
Chrony 시험도 호스트 시스템 시각을 바꾸지 않는 모드이며 외부 시간원의 정확도를 입증하지 않는다.
준비 실패·시각 역행을 운영 수집에 주입하지 않았다.

초기 실패도 [validation.json](validation.json)의 `initial_attempts`에 남겼다. 첫 복사본에서
기존 독립 검산 입력이 빠져 233건 성공·6건 실패했고, 원래 고정 입력을 수정 없이 포함해 해결했다.
임시 유닛 시험기는 예상한 첫 pre-start 실패를 전체 실패로 오인해 `--no-block` 관찰 방식으로 수정했다.
Chrony는 `-x`만으로 root 검사까지 해제되지는 않아 공식 비특권 옵션 `-U`와 같은 사용자 지정으로
시험했다. CLI 경로 시험의 비어 있는 Store와 UUID 없는 `/tmp`도 시작 거부 대상이었으며,
검사를 완화하지 않고 기존 운영 경로의 읽기 전용 접근 점검과 분리했다.

## 운영을 유지한 근거

- 작업 시작 시 캡처한 **운영 파일 139개가 모두 동일**하다. 별도 후보의 기존 파일 중 달라진 것은
  빌드 도구 setuptools를 고정한 루트 `pyproject.toml`뿐이다. 새 runtime·시험·문서는 후보에 추가했다.
- Book·Session·stream 수집 코드와 기존 호가 patch는 바이트 그대로다. 원래 raw·partial·보고서·
  재생 기준을 수정해서 시험을 맞추지 않았다.
- 설치된 사용자 depth/aggTrade unit 내용은 원래와 같고, checkpoint 반복 승인 내용도 같다.
- Chrony는 호스트에 여전히 **not-installed**, `/etc/adjtime`은 **LOCAL**이다.
- 실제 서비스 조작은 고유 이름의 임시 시험 unit 시작/종료뿐이다. 운영 수집·daily/restic/정리 unit을
  설치·재시작·중지하거나 타이머를 변경하지 않았다. 예약으로 일어나는 정상 운영 작업은 별개다.
- forget/prune·운영 checkpoint 삭제·시험 공간 삭제·암호 변경을 수행하지 않았다.

## 운영 적용 전에 함께 볼 제한

**듀얼부팅 RTC를 아직 교정하지 않았다.** Windows 쪽 RTC 해석을 확인한 뒤 두 OS의 기준을 정한다.
지금 Linux만 UTC로 바꾸지 않는다. 자동 큰 보정도 없으므로 초기 오차가 크면 slew 동안 수집 시작이
늦어질 수 있다. 준비 timeout 후 자동 통과는 없고, 별도 수동 step은 영향 범위를 승인받아야 한다.

**시각 근거의 자동 백업·연구 연계는 아직 별도 작업이다.** 현재 백업 코드는 카탈로그 등록 파일과
catalog를 대상으로 하므로 `state/runtime-clock`은 기존 daily/restic에 자동 편입되지 않는다.
이 단계의 기록은 로컬 운영 관측 근거다. 장기 품질 이력으로 쓰기 전 백업 편입과 collection ID/
메시지별 조회 연결을 정해야 한다. 기존 `healthy=true`를 이 로그 하나로 대체하지 않는다.

**자동 삭제의 영구 시각 gate는 미구현이다.** 현재 반복 승인 범위를 유지했고, Chrony 설치·시간
보정 전 타이머를 보류하고 실행 중 정리를 끝까지 확인하는 전환 절차를 준비했다. 새 영구 gate는
정리 코드·프로필·승인 해시를 포함한 별도 후보/승인 대상이다. 기존 정리기가 이미 자동 보호된 것으로
보고하지 않는다. 오늘 15:40 예약 결과와 다음 daily/restic 연결은 이번 후보 시험에서 집계하지 않았다.

다음 단계는 [명시한 전환 범위](deployment-review.md)와 위 제한을 검토해 운영 반영 범위를 정하는
것이다. 실제 NTP 동기화 품질, 새 시스템 서비스의 정상 수집·hourly 종료, 재부팅, Proxmox VM의
pause/resume 동작은 아직 완료한 관찰이 아니다.

기술 기준은 [Chrony tracking·waitsync](https://chrony-project.org/doc/4.5/chronyc.html),
[설정과 step 정책](https://chrony-project.org/doc/4.5/chrony.conf.html),
[비특권·시각 제어 금지 옵션](https://chrony-project.org/doc/4.5/chronyd.html),
[uv build constraint](https://docs.astral.sh/uv/reference/settings/#build-constraint-dependencies)를
확인했다. 위 문서의 동작과 프로젝트에서 선택한 초기 정책 수치는 구분한다.
