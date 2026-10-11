# 적용 범위·중단·원복

2026-10-11. 후보 **`t05-completion-evidence-20261011-c9d413cfe11ec0e7`**.
이 문서는 후보 검토 시 작성한 적용·원복 범위이며 자체가 승인서는 아니다.
이후 사용자 승인으로 **10/11 설치·기존 7개 등록·두 실행 명령 전환을 완료**했다.
실제 범위·중단/재개·현재 원복은 [배포 결과](../t05-evidence-deploy-2026-10-11/status.md)와
[원복 보완](../t05-evidence-deploy-2026-10-11/recovery.md)을 따른다. 운영 삭제·자동 정리 활성화는 미승인이다.

## 고정 입력과 변경 파일

[manifest.json](manifest.json), [source.tar.gz](source.tar.gz), [candidate.patch](candidate.patch)에
151개 소스와 전체 해시를 고정했다. 재현은 [reproduce.py](reproduce.py)를 사용했다.
이전 후보 대비 변경 18개 중 코드 13개, 검사 5개다.

| 코드 | 변경 목적 |
|---|---|
| `marketdata/checkpoint_clock.py`, `scripts/retirement_clock.py` | 완료 후처리와 정리기의 공통 U·관측 검증. 기존 스크립트는 호환 import 경로 |
| `marketdata/checkpoint_evidence.py` | 불변 근거·기존 집합·생성 관계·sidecar·복원 검증·보수적 선택 |
| `marketdata/backup.py`, `maintenance.py` | 완료 확정 후 근거 생성, 입력 잠금, 데이터 성공과 근거 실패 분리, 복원 보존 |
| `scripts/checkpoint_evidence.py` | 읽기 전용 inventory, 별도 승인 register/observe, 근거 포함 daily backup, inspect |
| `scripts/archive_backups.py` | 실제 저장 snapshot에서 sidecar를 읽어 해시/근거 목록을 저장 receipt와 연결 |
| `scripts/clock_retirement.py`, `retire_checkpoints.py` | restic 보존 근거만 사용한 기간/순서 판단, 긴 검증 후 최초 변경 직전 재조회 |
| `scripts/checkpoint_code.py` | 새 경로를 코드 식별 범위에 포함 |
| `scripts/render_retirement_units.py`, `run_evidenced_backup.py`, `run_evidenced_archive.py` | 고정 경로 실행 및 검토용 system 유닛·user drop-in 렌더 |

검토용 설정 13개는 [deployment-files.json](deployment-files.json)에 별도 해시를 기록했다.
후보 렌더는 운영 파일을 덮어쓰지 않았으며 [render_deployment.py](render_deployment.py)는 보고서 아래에만 쓴다.
`apply_enabled=true`는 기존 정리 기능의 설정이고 실행 승인이 아니다.
새 manual/recurring 템플릿은 `enabled=false`이며 설치 경로에서 새 scope를 산정해야 한다.

## 1. 설치 전 미리보기 — 삭제·근거 등록 없음

현재 운영 archive profile/repository ID/source root, checkpoint manifest와 전체 검사 결과,
당일 maintenance daily의 같은 backup ID, 미완료 정리 여부, 기존 잠금을 확인한다.
T13 자료 갱신 상태나 15:40 경과로 이 조건을 대신하지 않는다.

새 `inventory`는 보고서 출력과 기존 협동 잠금 외에 registry/sidecar를 쓰지 않는다.
T05 preview는 시험 상태·관측·계획을 별도 경로에 남기되 완료 근거를 자동 등록하지 않는다.
초기에는 근거가 없어 전부 보호될 수 있다. 삭제 가능한 후보가 생겨야만 설치할 수 있다는
조건을 두지 않는다. 당일 checkpoint/restic 미완료이면 보류와 접근 성공을 구분한다.

**10/11 15:50 KST 완료:** 고정 후보를 임시 시스템 유닛의 최종 preview 보호 옵션 아래에서
실행해 관리 대상 7개 모두 보호·후보 0개와 inventory 일치를 확인했다. 기존 범위 이전 3개는 제외했다.
근거 registry 미생성·등록/삭제 0건, 운영 경로 142개·Chrony/수집·mask 유지와 시험 정리를 확인했다.
[실제 결과·입력 기준 실패/보완·범위](operating-preview.md).

이 시험의 plan/inventory는 임시 경로에 묶여 있어 운영 설치/등록/삭제 승인에 이식하지 않는다.
다음 단계에서는 최종 설치 경로의 해시·실효 유닛·근거 후처리 쓰기 접점을 확인하고,
새 inventory의 정확한 대상을 승인 범위와 연결한다. 현재 시험은 그 운영 쓰기의 완료 근거가 아니다.

## 2. 별도 승인할 설치·근거 생성 범위 — 자동 정리 계속 보류

- `/opt/marketdata/releases/<release-id>`에 비-editable 환경과 고정 소스를 설치한다.
  root 소유, 그룹/기타 쓰기 제거, 실제 import/해시를 확인한다.
  두 entrypoint는 `python -B -I`에서 **그 고정 배포본 내부**의 source 경로를 명시적으로 사용한다.
  개발 workspace나 PYTHONPATH에 기대지 않는다.
- `/etc/marketdata/checkpoint-clock-retirement/`에 settings/policy/clock binding/검토 유닛을 둔다.
  실제 bootstrap 해시·외부 archive profile·복구 receipt를 다시 대조한다.
  새 disabled 승인 파일을 사용하고 과거 수동/반복 grant를 수정하거나 재사용하지 않는다.
- `/var/lib/marketdata-checkpoint-retirement`의 필요한 하위 상태 경로를 `yoonhc`가 기록할 수
  있게 명시적으로 구성한다. 코드/설정/승인은 root 소유이며 일반 계정의 권한을 확대하지 않는다.
- 사용자 `marketdata-backup.service`와 `marketdata-restic-archive.service`의 **ExecStart만**
  두 [고정 drop-in](deployment/user-dropins/)으로 바꾼다. 기존 ExecStartPre 전체·예약 시각·
  systemd manager·재시작 정책을 유지한다. 실행 중인 백업/아카이브를 강제 종료하지 않고
  유휴 시점을 확인한다. 기존 자료 갱신용 `marketdata-daily`와 두 수집기는 변경하지 않는다.
- 생성기와 archiver를 함께 설치한다. 새 backup만 적용하면 sidecar는 생겨도 기존 archiver의
  stored receipt에 T05 연결이 생기지 않아 삭제 판단에서는 계속 보호된다.
- 새 시스템 정리 service/timer는 설치해도 **persistent masked**로 유지한다.
  기존 사용자 정리 service/timer도 계속 masked다. 시스템 preview/apply 유닛은
  `User=yoonhc`, 빈 capability, ProtectClock/ProtectSystem 등 기존 보호 설정을 유지한다.

기존 집합 등록은 **운영 상태 쓰기**다. 읽기 전용 단계와 분리해 정확한 inventory plan ID·
행위(register/observe)·사유를 담은 root 소유 승인을 만든다. 등록 CLI는 maintenance/repository/
checkpoint/registry 잠금 아래 현재 inventory를 다시 대조한다. 변경됐으면 새 검토가 필요하다.
등록 승인에는 삭제 권한이 없고, 정리 승인으로도 등록을 대신할 수 없다.

처음 `register`는 기존 검증 사본 목록을 불변으로 확정하고 유효한 첫 사후 상한을 기록한다.
이후 누락 근거 보완은 새 inventory와 별도 승인된 **`observe`**로 한다.
기존 baseline을 새 목록으로 덮어쓰지 않는다. 이미 유효한 상한은 재조회·갱신하지 않는다.
조회 실패 시 해당 사본은 missing으로 남으며, 과거 completed_at을 신뢰한다고 바꾸지 않는다.

## 3. 첫 운영 생성·보존 확인

정기 백업의 실제 `backup.json` fsync 완료 뒤 새 Chrony 관측을 얻는다.
생성/연결에 실패해도 완성된 데이터 사본은 성공으로 남고 시각 근거 실패 사유가 따로 기록된다.
동일 사본의 완료 상한·manifest 해시·생성 선후관계가 sidecar와 불변 registry에 남는지 확인한다.
당일 이미 완성된 사본을 재사용하는 경로에는 완료 직후 관측을 소급 만들어 붙이지 않는다.

다음 archive에서 동일 backup ID의 명시적 snapshot·sidecar 실제 bytes·전체 읽기 검사와
연결되는지 확인한다. 원래 T05 registry에만 있고 아직 snapshot으로 보존되지 않은 근거는
정리 판단에 쓰지 않는다. 초기 등록 근거는 다음 새 checkpoint의 sidecar에 함께 편입된다.

운영에서 생성한 근거의 첫 복원 조회는 별도 작업 경로에서 수행한다. 시험 성공을 이 확인으로
대체하지 않는다. 복원된 근거만으로 판단은 재구성할 수 있지만 삭제 승인·미완료 journal은
자동 복원/활성화하지 않는다. journal 유실로 로컬 누락을 설명할 수 없으면 기존 audit는 보류한다.

## 4. 조건 축적 → 새 계획 → 한 번 삭제 → 반복 자동화

기존 정기 백업을 유지하며 확실히 후행인 검증 사본 3개와 보수적 72시간+60초를 기다린다.
조건을 앞당기려고 빈 백업을 연속 생성하거나 시스템 시각을 바꾸지 않는다.
서로 순서가 입증되지 않은 기존 사본끼리는 날짜 문자열로 전 순서를 만들어내지 않는다.

이후 새 preview는 보호 목록·후보·완료 상한·유지할 후행 사본·snapshot 연결을 제시한다.
한 계획 수동 승인은 설치된 코드/설정/scope·현재 boot·정확한 plan ID에만 유효하다.
긴 유지 사본 무결성 검사 뒤 최초 파일 변경 직전에 현재 시각을 다시 조회한다.
실행 중 이상/만료/설정 변경은 중단하며 미완료 journal을 남긴다.

첫 실제 삭제의 대상 소멸·유지 파일·journal·원본/restic 보존을 확인한 뒤에만
**새 반복 승인과 시스템 15:40 timer 활성화**를 별도로 요청한다.
`Restart=no`, `Persistent=false`를 유지하며 미완료/잠금/시각 보류는 삭제 성공이 아니다.

## 중단·원복

| 중단 지점 | 보존과 복구 |
|---|---|
| baseline 확정 전 | 승인·현재 inventory 재확인. 불변 객체의 충돌을 삭제해 해결하지 않음 |
| baseline 뒤 일부 관측 실패 | 첫 유효 근거 유지. missing 항목만 다음 별도 observe 범위로 검토 |
| backup.json 이전 | 기존대로 미완료 사본. 완료 상한 없음 |
| backup.json 이후 근거 전 | 데이터 완료와 missing 구분. 사후 확인으로 더 보호 |
| registry 객체 뒤 sidecar 전 | 객체를 보존. 완료 사본 재생성/manifest 수정 없이 후속 bundle 편입과 사후 확인을 검토 |
| restic 저장 후 receipt 기록 전 | 기존 snapshot reconciliation이 실제 sidecar bytes와 연결해 복구 |
| 삭제 도중 | 새 자동 계획을 막고 원래 journal/계획 보존. 별도 승인으로 남은 범위만 재개 |

근거 생성 기능 원복은 두 사용자 ExecStart drop-in을 이전 고정 경로로 되돌리는 작업이다.
타이머·기존 guard·실행 중인 업무는 별도 확인하고, 정리 자동화는 계속 masked로 둔다.
새 sidecar·registry·자료·카탈로그·실패 기록을 삭제하거나 과거 DB로 덮어쓰지 않는다.
구형 backup/restore는 데이터 작업을 계속할 수 있으나 새 근거 의미를 조회하려면 보존한
이 후보의 `inspect --bundle ...`를 사용한다. 구형 archiver로 되돌리면 새 T05 보존 receipt가
추가되지 않는 제한이 돌아온다. 기존 정리기 자동 활성화로 우회하지 않는다.

이미 삭제한 로컬 사본은 코드 원복으로 되살아나지 않는다. 명시적 restic 지점으로 별도 복원한다.
근거만 복원해 운영 승인이나 미완료 journal까지 해결됐다고 간주하지 않는다.
추후 forget/prune을 설계할 때 이 불변 근거와 snapshot 의존관계를 함께 보호해야 한다.
이번에는 restic snapshot 만료·원본 삭제·다른 예약 업무 이전을 구현하지 않았다.
