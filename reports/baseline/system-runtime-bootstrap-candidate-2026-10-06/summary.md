# UTC RTC·부팅 초기 보정 후보 검증

**새 배포 후보와 격리 검증을 완료했다. 운영 설치·시각/RTC 변경·서비스 중지·재부팅은 하지 않았다.**
Windows/Linux RTC의 UTC 통일은 확인할 전제이며 아직 수행하지 않았다. 자동 정리의 실제 상태도
이번에 변경하지 않았고, 새 전환안은 영구 시각 검사 검증/승인 전까지 정리를 보류하도록 정했다.

후보 ID: **`system-bootstrap-20261006-81d25b1c4552f21d`**.
[고정 소스](release/), [배포 메타데이터](release.json), [재현 묶음](source.tar.gz),
[이전 후보에 적용할 패치](candidate.patch), [전환·되돌리기](deployment-review.md),
[검증 원장](validation.json), [설정·유닛·전환안 해시](deployment.json), [재현 절차](reproduce.md)를 보관한다. source archive SHA256:
`8c9cbd31f42b1fdc6726825e2ab115c462c743672c60fd3e125191b394020c1f`.

## 구현과 정책

| 항목 | 이번 후보 |
|---|---|
| 정상 Chrony | rtcsync, maxpoll 8(명목상 256초), 자동 makestep 없음, 윤초 slew 명시 |
| RTC 쓰기 시작 | Windows UTC 확인 자료·Linux UTC 설정을 Chrony ExecStartPre에서 요구 |
| 초기 보정 | 별도 root 시스템 unit; 외부 시간원 확인 → 필요한 step → 전체 품질 확인 |
| 실행 권한 | collector는 일반 계정; 초기 helper도 직접 시각 syscall 없이 Chrony Unix socket 이용 |
| 부팅별 제한 | 설치한 부팅은 hold; 다음 부팅 준비 완료 순간 자동 보정 허용을 닫음 |
| 강제 종료/명령 실패 | intent fsync 후 요청; 결과 불명은 unknown 유지·자동 재전송 없음 |
| 기록 공개 | coordinator 배타 잠금과 소비자 공유 잠금; 준비 완료 전 작업 시작 거부 |
| 재시작 | 완료된 준비/Chrony 재시작, hourly 사이 PID 부재로 보정을 다시 허용하지 않음 |
| user 예약 | OI·OI probe·OI quality·daily·pack·backup·restic·network observer 8개 시작 검사 후보 |
| 자동 삭제 | 옛 두 stream 및 정리 service/timer의 persistent mask를 전환안에 명시; grant 불변 |

초기 보정은 correction >100ms, <=300초에서 부팅당 최대 한 번 요청한다. source age 30초 이내와
source 불확실성을 먼저 검사한다. NTP로 추정한 시각으로 reference age를 계산하므로, 시스템 시각이
±41초 어긋난 상태에서도 **이미 correction이 100ms 아래여야 보정을 허용하는 모순**이 없다.
직후에는 기존 100ms/500ms/reference age 600초 조건을 모두 요구한다. 과거 수신 시각을 수정하지 않는다.

maxpoll 8은 원래 후보에도 있었다. 기본 maxpoll 10(1,024초)과 reference age 600초의 충돌이 발견된
것은 아니다. 초기 300초 상한과 30초 source age는 검토할 정책값이며 실측 최적화를 주장하지 않는다.
600초 장기 대기는 부팅 내 누적 시간으로 journal에 경고한다. 외부 알림은 설치하지 않았다.
구체적 상태·수치·역할은 [runtime 계약](release/integrations/runtime/README.md)에 있다.

## 완료한 검증

| 근거 | 실제 결과 |
|---|---|
| 신규 bootstrap 검사 | **37건 통과**: ±41초, 준비/RTC/목록 거부, 보정 전후, 잠금, 실제 SIGKILL, 재시도, 명령 timeout, 저장 실패 |
| runtime 회귀 | **49건 통과**; RTC 정책 검사 1건은 새 rtcsync 정책에 맞게 명시적으로 변경 |
| 기존 수집 회귀 | **199건 통과**; 원문 독립 계산·상태 공개·호가/체결 복구·stream lock 포함 |
| 고정 후보 전체 | **285건 통과**, 15.10초 |
| 새 빈 경로 | archive/169개 해시 대조, 비 editable 재설치, **285건 통과**, 14.60초 |
| 이전 후보 + 패치 | 별도 경로에서 git apply --check/apply 성공, **169개 source 해시 일치** |
| 실제 사용자 systemd 시험 | 준비 실패 후 **2회 재시도**, child 시작 없음 → 조건 회복·가짜 보정 1회·child 시작 |
| 재시작 시험 | fake provider와 완료한 준비 unit 재시작 후 step 추가 0회, 현재 시각 gate 종료 75 |
| 종료 | 시험 child SIGINT 종료 기록·MainPID=0 확인; 시험 unit만 정리 |
| 구문/설정 | 시스템 4개 및 user service 8개 verify 종료 0, Chrony 4.5 `-p` 설정 파싱 종료 0 |
| 배포 의존성 | clean 환경의 --no-dev 12개 배포 의존성·실제 import 경로·설치 receipt 확인 |

[회귀 결과](regression.xml), [재현 회귀](clean-regression.xml), [패치 대조](patch-reproduction.json),
[systemd 결과](systemd-isolated.json), [상태·가짜 명령 근거](systemd-evidence/),
[유닛/설정 검사](unit-verify.json), [설치 receipt](installation-receipt.json).

user-systemd 시험은 JSON 가짜 시각/가짜 step을 사용한다. **호스트 시각 보정 성공이나 실제 root unit의
전체 보호 설정을 입증한 결과가 아니다.** Chrony `-p`도 파싱만 했고 daemon으로 기동하지 않았다.
이전 후보의 `chronyd -x -U` 접점 시험은 기존 근거로 보존했으며 새 실제 시각 제어 시험으로 세지 않았다.

초기 샌드박스 user bus 거부는 격리 시험에 한정해 실행 허용을 받은 뒤 확인했다. 구문 fixture에서
미설치 chronyc 때문에 발생한 첫 오류는 **시험 경로만** 추출 바이너리로 바꿔 해결했다. 운영 경로는
계속 `/usr/bin/chronyc`, `/usr/sbin/chronyd`이며 실제 설치 후 실행 가능성을 다시 확인해야 한다.
첫 SIGKILL 시험의 fork 경고는 spawn 방식으로 바꿨고 최종 테스트에는 경고가 없다.

## 유지 확인과 남은 검증

[최종 대조](final-audit.json)에서 이전 후보 전체 파일, 운영 소스/설정 139개, 설치된 사용자 유닛,
정리 승인 hash, RTC adjtime가 모두 동일하다. Chrony는 여전히 미설치이고 RTC는 LOCAL이다.
기존 수집·daily·restic·정리의 예약 실행은 계속되는 운영 활동이며 이 후보 시험에 집계하지 않았다.

다음은 실제 전환 때 확인한다.

- Windows 설정·Linux RTC UTC 반영·양쪽 실제 부팅과 RTC 근접성.
- root unit의 보호 옵션 아래 runuser/user bus·Unix socket·설정·마운트 접근, 실제 Chrony clock 제어와 시간원 품질.
- 새 시스템 collector의 정상 저장·첫 세션 종료·다음 세션, 중복 수집 방지.
- 실제 부팅/VM resume. 운영 시계를 인위적으로 틀어 테스트하지 않는다.

시각 로그/닫힌 상태 근거의 백업·복원·collection 연결과 총용량 관리는 아직 없다. runtime JSONL은
실행별로 나뉘지만 무기한 무관리 운영을 승인한 것은 아니다. 로그가 없으면 품질은 미확인이다.
자동 정리의 영구 시각 gate도 별도 후보다. 초기 부팅 검사로 매일의 삭제 안전성이 완성됐다고 주장하지 않는다.

실제 다음 단계는 [전환 범위](deployment-review.md)를 확인한 뒤 수집 공백·Windows 작업·재부팅을
포함해 적용하는 것이다. 준비가 끝났다는 이유로 이번에 그 작업을 실행하지 않았다.
