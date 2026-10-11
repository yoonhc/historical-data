# T05 후보 구현·격리 검증

2026-10-11 12:00 KST 기준. **후보·격리 검증과 실제 관리자 시스템 접근 확인 완료,
당일 checkpoint/restic 조건을 충족한 미리보기는 대기**다.
운영 삭제·설치·timer 활성화는 수행하지 않았다. T05 전체 완료가 아니다.

고정 후보는 **`t05-clock-retirement-20261011-3618649f76cfd2f4`**다.
[소스 144개·전체 해시·압축본/패치 해시](manifest.json),
[정책과 한계](design.md), [정확한 적용·승인·원복 범위](deployment-review.md).

## 구현 결과

기존 최근 3개·완료 후 72시간 보호, 당일 checkpoint와 같은 backup ID의 restic 저장·전체 검사,
복구 근거·무결성·공유 잠금 조건을 재사용했다. 현재 UTC 하한과 완료 확인 UTC 상한의
차이가 72시간 이상임을 추가로 요구한다.

기존 checkpoint에는 당시 시각 오차 근거가 없다. 원래 completed_at을 수정하지 않고
검증된 완료 사본을 현재 정상 시각에 확인한 별도 불변 관측을 완료 상한으로 사용한다.
따라서 처음 관측한 사본에는 **추가 72시간 이상 보호**가 생길 수 있다.

Chrony의 현재 조회·조건부 오차·부팅 준비·provider 설정과 UTC/monotonic/boottime 연속성을
확인한다. reference age는 기록하되 T05에서 600초 상한으로 차단하지 않는다.
오래된 reference와 조회 자체의 만료는 구분한다. 다른 업무의 시각 gate나 Chrony 설정은 유지했다.

시각 이상·기록 실패·미완료 정리에는 새 자동 정리를 보류한다. 각 실제 계획은 새 ID와
정확한 후보/보호·시각 근거를 포함한다. 수동 exact-plan 승인과 recurring 승인을 분리하며
기존 승인 재사용을 거부한다. 중단된 원래 계획은 별도 현재 부팅의 명시적 복구 승인으로만 재개한다.

시스템 `User=yoonhc` service/timer, 수동 apply template, preview service를 렌더했다.
`ProtectClock=yes`, 빈 capability, 제한된 쓰기 경로를 사용한다. **운영 시스템 유닛은 미설치**이며
기존 사용자 service/timer는 계속 masked다.

## 검증과 실제 미리보기

- **108개 검사 통과**: 신규 T05 39개 + 기존 정리·잠금·예약 회귀 69개.
- 기준 배포본에 패치를 별도 경로에서 적용해 144개 파일 해시 일치, 비-editable 설치,
  T05 동일 39개 재현 통과. 두 실행을 147개의 서로 다른 검사로 합산하지 않는다.
- 임시 실제 restic 저장소에서 2개 후보 삭제·3개 유지, 시각 급변 직전 중단,
  명시적 원래 계획 재개, 승인 오용·근거 변조 거부를 검사했다.
- 운영의 일반 계정 미리보기는 **11:46:26–11:46:27 KST, `daily_not_today` 보류**다.
  현재 시각은 준비됐지만 당일 checkpoint/restic 선행 조건이 미충족이라 후보 산정 전 종료했다.
  삭제 0개이며 `no_candidates`나 미리보기 완료로 표시하지 않는다.
- 전후 기존 설정/승인/mask 파일, 부팅, Chrony·두 수집 supervisor는 동일했다.
  [host-after.json](host-after.json).
- **11:59:49–11:59:50 KST 관리자 시스템 시험**: UID 1000·실제 system manager start Job,
  20개 보호 옵션, 빈 capability, 원본/checkpoint/restic의 읽기 전용 mount,
  Chrony 조회·부팅 준비 공유 잠금·시험 fsync·실제 restic snapshot 15개 조회를 확인했다.
  reference age 1,048초, 현재 조건부 오차 약 125.8ms, 현재 조회 준비 통과다.
  그러나 동일한 `daily_not_today`로 후보 산정 전에 보류했다.
  **access_passed=true, preview_completed=false, passed=false**를 그대로 보존한다.
  시험 유닛 not-found·시험 경로 제거·정리 오류 0개·133개 운영 파일/프로세스 유지 확인.
  [관리자 원본 결과](system-preflight-result-ae5d62d13d034b72.json).

자세한 근거와 시험 대역의 범위는 [validation.md](validation.md)에 있다.

## 다음 단계

오늘 **13:10 예약 checkpoint 생성 작업과 같은 backup ID의 restic 저장·전체 검사가 실제 완료된 뒤**
후보 산정까지의 미리보기를 다시 평가한다. 13:10 경과만으로 완료를 가정하지 않는다.
여기의 daily는 T13 자료 갱신이 아니라 `marketdata-backup`의 maintenance daily다.
기존 검증을 반복하거나 조건을 낮추지 않고, 현재 보류된 끝부분을 완료한다.
최종 보호 환경의 전체 preview는 [system_preflight.py](system_preflight.py)의 별도 임시 실행으로
진행하며 관리자 인증이 필요하다. 현재 접근 결과만으로 전체 preview 성공을 선언하지 않는다.
임시 경로의 계획은 운영 삭제 권한이 아니다.

후속 승인 순서는 **운영 설치를 masked 상태로 확인 → 최종 경로의 한 계획 수동 삭제 승인 →
실제 삭제/보존 결과 확인 → 새 반복 승인·시스템 timer 활성화 승인**이다.
후보 0개·시간 경과·검사 통과를 이 승인 대신 사용하지 않는다.

기존 v1 시각 근거, T13 운영 관찰, 다른 예약 업무의 시각 정책/시스템 이전은 각 후속 항목에
남겼다. 자동 정리 외의 서비스·시각·백업 설정·원본·restic 지점·기존 시험 공간은 변경하지 않았다.
