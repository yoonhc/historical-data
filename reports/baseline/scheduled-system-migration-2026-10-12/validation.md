# 검증 범위와 재현

## 입력·코드 조합

[baseline.json](baseline.json)은 실제 설치본·사용자 manager·시스템 manager의 읽기 전용 캡처다.
후보 소스 41개 중 업무 소스/패키지 설정/lock 35개는 workspace와 동일하다.
추가 6개는 migration Python 5개와 기존 parent 해시 목록이다.
T13 `703fb0d455523c1b`, T05 `c9d413cfe11ec0e7`, bootstrap `f85eb8321a19c1ee`,
두 수집기 `65296619a374c325`의 설치 경로를 유지한다.
수집/Chrony 프로필은 전환 대상이 아니다. bootstrap 프로필과 T05 binding만 연결을 바꾼다.

새 업무 실행 환경은 `uv sync --frozen --offline --no-editable --link-mode copy`로 구성했다.
Python/package/import/환경 파일 해시는 [environment.json](environment.json)에 있다.
import가 새 `.venv/lib/.../site-packages`를 사용하고 marketdata 소스가 고정 입력과 일치함을 검사했다.
기존 runtime 의존성과 비교한 핵심 버전은 pyarrow 23.0.1, duckdb 1.5.5, httpx 0.28.1,
websockets 17.1, anyio 4.15.1, certifi 2026.7.22, idna 3.20이다.

## 전환 시험 152개

[test_migration.py](test_migration.py), [최종 XML](transition-tests-final.xml).

- 실제 격리 파일·symlink·fsync·원자 rename과 기존 coordinator 파일 잠금을 사용한다.
- apply의 durable 경계 **51곳**에서 예외를 주입하고 재개하는 51개, 각 경계에서 원복하는 51개.
  프로세스/전원 강제 종료 시험이 아니라 기록 직후의 예외 주입이다. 디스크 전원 장애까지 인증하지 않는다.
- 원복 자체의 intent/pending/일부 파일/staged/receipt/commit 중단 8개.
- 실행 중 업무·대기 job·다른 boot·보호 설정 변동·mask 해제·ledger/승인 불일치 거부.
- 새 구성 일부만 적용된 상태는 **실제 이전 gate의 Ledger/check_gate**가 거부한다.
  구성 완료와 현재 strict 준비는 별도로 판정하고 reference_age 실패는 그대로 거부한다.
- 실제 systemctl의 반복 Exec* 출력은 모두 보존하고 scalar 중복은 거부한다.
  새 17개 unit spec의 추가 명령을 거부하고 bootstrap은 실제 업무와 waiter를 구분한다.
- 기존 guard 제거·새 system guard 누락·T05 binding 불일치·정책 확대·불완전 mask 계획을 거부한다.
- 원복 전에 생성한 격리 artifact와 SQLite 작업 행을 유지한다. 운영 자료를 만들거나 수정한 시험은 아니다.

**manager의 drained/activation/현재 준비 결과는 주입한 모델이다.**
system bus에서 실제 신규 unit을 로드하거나 두 manager를 전환한 시험이 아니다.
추가 bootstrap inventory 시험은 부모 audit 호출을 확인하면서 새 system 측을 격리했다.
기존 user 측 audit의 모든 동작을 다시 검증했다고 합산하지 않는다.
초기 147개 결과는 개발 중 근거이고 최종 152개에 추가 합산하지 않는다.

## 실제 T05 코드의 연결 시험 8개

[test_parent_bridges.py](test_parent_bridges.py), [XML](parent-bridge-tests.xml).
운영에 설치된 **T05 interpreter와 원래 module**로 실행했다.

- 원래 `checkpoint_clock.Host.lock`이 이전/새/원복 프로필을 수용하고 pending ledger·이전 binding·전환 잠금 충돌을 거부한다.
- 원래 `restic_schedule.service_context`가 system manager의 PID·invocation·현재 start job·timer stamp를 대조한다.
  정상 timer는 인정하고 manual·다른 PID·다른 timer·변경된 job·다른 trigger stamp는 예약 성공으로 세지 않는다.
- adapter는 manager 선택과 새 출처 식별자만 추가한다. 기존 T05 module/source identity는 그대로다.

파일 경로/소유권 검사와 system bus 응답은 fixture로 제공했다. **실제 root 보호 환경의 접근 시험이 아니다.**
시각 조회·restic 실행·운영 원장/registry 쓰기는 하지 않았다.
T05 삭제 승인 scope 변경은 별도 운영 전환 검토 대상이며 여기서 재승인하지 않았다.

## 기존 업무 회귀 80개

새 non-editable 환경에서 다음 기존 파일을 실행했다. 업무 구현은 원본과 바이트 동일하다.

```text
tests/test_oi_probe.py
tests/test_oi_quality.py
tests/test_packing.py
tests/test_maintenance.py
tests/test_maintenance_wait.py
tests/test_backup.py
tests/test_incremental_backup.py
```

[XML](unchanged-workload-tests.xml). pack/개별 사본 정리의 무결성·잠금,
maintenance 잠금 대기·날짜별 backup 재사용과 증분 backup 등 기존 계약의 회귀 근거다.
변경하지 않은 T13 전체 검증이나 운영 대규모 restic 복원을 반복하지 않았다.

## 유닛·별도 경로 재현

[verify_units.py](verify_units.py)는 18개 유닛에 native `systemd-analyze verify`를 실행해 exit 0을 얻었다.
아직 설치하지 않은 새 `/opt/<release>` 실행 경로만 후보 로컬 경로로 치환했다.
정책·의존성은 바꾸지 않았으며 [원본 결과](unit-verification.json)에 그 한계를 남겼다.
실효 UMask·manager 기본 환경/limit·system bus 접근·종료 동작은 실제 관리자 사전 점검에서 확인해야 한다.

`reproduce.py`는 [seal.json](seal.json)의 해시를 확인하고 소스 archive를 새로운 `/tmp` 폴더에 추출한 뒤
오프라인 설치와 152개 전환 시험을 재현했다. [결과](reproduction.json), [XML](reproduction-tests.xml).
이는 같은 152개의 재현이며 총 검사 수에 더하지 않는다. 임시 폴더는 종료 후 존재하지 않는다.

## 고정 해시·운영 대조

| 항목 | SHA-256 |
|---|---|
| source.tar.gz | `0974dd5d88f8bb0489059ba8cdfceecfd2b6d854dc9db2bc8a90be6330017197` |
| plan.json 파일 | `9c504c1a097217fc0f8bb49db45467aff8d37514162c5b4ced24e087f7064e30` |
| manifest.json | `234a1abf2916a88fea1d8974ef580178a348f25bb696cd9c0286c3df654852ae` |

plan ID는 plan_id 필드를 제외한 정의의 해시이므로 plan.json 파일 해시와 다르다.
전체 소스·rendered 유닛·환경은 [seal.json](seal.json)과 manifest로 대조한다.
[operating-comparison.json](operating-comparison.json)은 운영 파일/목록/ledger/서비스 신원/mask와
외부 설정·parent/workspace 소스의 전후 보존, 검사 XML 해시를 연결한다.

## 남은 실제 검증

최종 root-owned 경로 설치/권한, 시스템 `User=yoonhc`의 import·읽기/쓰기/동일 잠금,
실제 system timer의 job 출처, 양 manager의 pending job/정상 종료/중복 방지,
timer 최초 활성화의 따라잡기, 실제 root 설정 전환과 원복은 남았다.
현재 코드의 live 쓰기 entrypoint는 닫혀 있으며 이 결과를 운영 이전 승인이나 완료로 확대하지 않는다.
