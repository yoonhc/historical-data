# 부팅 초기 시각 보정 후보의 재전송·권한·업무 재개 보완

**후보 구현·격리 검증·깨끗한 경로 재현을 완료했다. 운영 설치·시각/RTC 변경·수집 중지·재부팅은 하지 않았다.**
실제 root 보호 환경의 접근 검사는 관리자 인증이 필요해 미완료다. Windows/Linux RTC UTC 확인 역시
아직 전제이며 완료 사실이 아니다. 기존 자동 정리의 운영 상태는 변경하지 않았고, 전환안에서는 계속
영구 시각 검사 별도 검증·승인까지 보류한다.

고정 ID: **`system-bootstrap-20261007-f85eb8321a19c1ee`**

- [소스·해시](release.json), [172개 고정 파일](release/), [소스 묶음](source.tar.gz)
- archive SHA256: `1960c8b743d377170e0683678dde4078895be2650958ead027f5fd7fe9a4dfa1`
- [직전 후보에 대한 패치](candidate.patch), [패치 재현](patch-reproduction.json), [재현 절차](reproduce.md)
- [전환·상태 조회·되돌리기](deployment-review.md), [머신 설정](machine-profile.json),
  [bootstrap 설정](boot-profile.json), [최종 유닛](units/), [검증 원장](validation.json)

## 보완 내용

| 항목 | 변경과 의미 |
|---|---|
| chronyc 내부 재시도 | 같은 프로세스의 `-m "retries 0" "makestep"`; 응답 유실 시 내부 재전송 차단 |
| 명령 형태 | 인자 없는 현재 보정 유지; 미래의 자동 step window를 설정하지 않음 |
| root 소켓 접근 | `ReadWritePaths=/run/chrony` 추가; client 응답 소켓 생성에 필요한 좁은 예외 |
| 8개 업무 준비 대기 | 공통 read-only `marketdata-clock-ready.service` 앞에 start job을 대기시킴 |
| 업무의 기존 제한 | ExecStart·Restart·StartLimit·Timeout·SuccessExitStatus·timer 변경 없음 |
| coordinator 구분 | 고정 명령을 확인한 waiter는 업무 실행으로 세지 않음; 실제 업무 PID는 계속 보정 금지 |
| 대기 완료 이후 | RemainAfterExit=no; 다음 활성화 때 현재 시각 재확인, 최종 ExecStartPre 검사 유지 |
| unknown | 이미 구현한 재보정 없는 현재 품질 확인 경로 유지; 조회·조치·취소 절차를 구체화 |

새 waiter를 포함해 user 목록은 21개이며 bootstrap profile은 schema 2다. ledger schema는 1을 유지한다.
이전 후보의 profile hash/승인을 새 후보에 재사용하지 않는다. 이전 후보는 운영 미설치 상태였다.
변경/추가 소스는 8개다. 원문·Book·범위 채택·요청 예산·품질 판정·백업/정리 계산은 변경하지 않았다.

## 실제 확인 결과

| 검증 | 결과 |
|---|---|
| 실제 chronyc 4.5 + 응답 없는 격리 Unix socket | 같은 3초 제한에서 이전 호출 **2회**, 새 Host.step **1회** 요청 |
| bootstrap 회귀 + 새 대기/audit 검사 | 37 + 12 = **49건 통과** |
| runtime 및 기존 수집 회귀 | 49 + 199 = **248건 통과** |
| 전체 관련 회귀 | **297건 통과**, 14.37초 |
| 새 빈 경로·비 editable 설치 | **297건 통과**, 14.12초; 운영용 no-dev 12개 의존성/venv import 확인 |
| 8개 실제 임시 user systemd 업무 | 준비 대기 동안 본 업무·업무 재시도 **0회**, 회복 후 각각 1회 실행 |
| 다음 활성화에서 다시 시각 불량 | 8개 모두 다시 대기 후 회복·각각 1회 실행; synthetic step 추가 0회 |
| 실제 업무 실패 주입 | 짧게 설정한 2회 제한 뒤 중단; 시각 대기를 이유로 무한 업무 재시도를 추가하지 않음 |
| 기동 의존 관계·구문 | 시스템 5개·사용자 9개 transaction, Chrony `-p` 파싱 모두 종료 0·stderr 없음 |
| 직전 후보 + patch | git apply --check/apply, 새 소스 172개 해시 일치 |

[chronyc 원시 요청 근거](chronyc-wire.json), [systemd 실행 기록](job-gate-systemd.json),
[가짜 시각·실행 건수·시험 유닛](job-gate-systemd-evidence/), [회귀](regression.xml),
[깨끗한 재현 회귀](clean-regression.xml), [기동 관계 검사](unit-verify.json),
[설치 receipt](installation-receipt.json).

chronyc 시험은 실제 client와 fake socket을 사용했으며 daemon/호스트 시각은 제어하지 않았다.
systemd 시험은 실제 coordinator audit/check/wait 로직과 user manager를 사용했지만 시각·step·본 업무는
대역이다. 각 업무 timeout을 0.5초로 짧게 둔 상태에서 1.2초 이상 대기해도 업무 시작 제한이 소진되지
않음을 확인했다. 실제 calendar/Persistent 새 부팅, root 보호 옵션 아래의 실행과는 구분한다.
구문 검사에는 배포판 원래 Chrony 유닛과 alias/drop-in을 포함했고 실행 경로만 시험 경로로 치환했다.

## 실패 이력과 한계

- 최초 systemd 시험은 8개 업무의 두 차례 재개까지 확인한 뒤, systemd가 이미 해제한 완료 unit의
  reset-failed를 성공으로 가정한 시험 도구에서 중단됐다. [첫 기록](job-gate-systemd-first-attempt.json).
- 두 번째 시험은 업무의 재시도 제한이 실제 동작했으나 Result가 start-limit-hit로 바뀐다고 가정해
  timeout됐다. 해당 환경에서는 exit-code가 유지됐다. [두 번째 기록](job-gate-systemd-second-attempt.json).
  최종 시험은 실제 failed 상태·재시도 횟수·본 업무 건수와 journal을 대조해 통과했다. 운영 코드의
  판정 기준을 시험에 맞춰 바꾼 것이 아니다.
- 기본 uv 캐시는 잠금 접근 제한과 캐시 누락으로 설치하지 못했다. 기존 /tmp의 고정 cache를 사용한
  offline 설치와 새 경로 재현은 통과했다. 패치 생성의 새 파일 mode 누락도 고쳐 최종 적용/해시를 확인했다.
- user 소켓 접근은 sandbox 밖의 격리 시험 허용을 받아 검증했다. root 검사는 sandbox 밖에서도
  `sudo -n true`가 암호를 요구하여 실행하지 않았다. 권한을 낮추거나 우회해서 통과시키지 않았다.

공통 waiter의 성공은 업무나 백업 성공이 아니다. 정상 준비 대기는 자동으로 해소되지만, waiter 자체를
강제 종료/stop해 dependency job이 취소되면 업무를 다시 요청해야 할 수 있다. Requires 관계 때문에
waiter의 명시적 stop은 의존 업무에도 영향을 줄 수 있어 운영 중 단독 stop을 rollback으로 쓰지 않는다.
최종 검사 직전에 시각이 다시 나빠지는 경우와 실제 업무 실패는 기존 오류 정책에 따른다.

## 보존 확인과 다음 단계

[최종 대조](final-audit.json)에서 운영 소스/설정 139개·설치된 사용자 유닛·직전 후보 전체 파일·
정리 승인 해시·RTC adjtime를 대조한다. 기존 데이터, manifest/source_root, 원문·보고서·백업·시험 공간·
복구 키트는 그대로다. 새 waiter/drop-in/root 유닛은 후보 폴더에만 있고 운영에 설치하지 않았다.

다음은 관리자 인증하에 실제 root 보호 환경의 소켓/상태/공유 잠금/runuser 접점을 확인하고, Windows/Linux
UTC 전환·고정 배포본 설치·수집 종료/기동·부팅을 명시한 범위로 승인받는 단계다. 시각 로그의 보관/복원/
collection 연결과 자동 삭제의 영구 시각 검사는 여전히 별도 필수 작업이다.
