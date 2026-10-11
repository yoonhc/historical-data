# T05 검증 범위

최종 후보 `t05-clock-retirement-20261011-3618649f76cfd2f4`.
이 문서의 격리 검증은 운영 삭제 실행이나 시스템 유닛 접근 시험의 대체 근거가 아니다.

## 후보 검사

- T05 39개: 시각·근거·권한 단위 35개와 실제 restic을 사용하는 임시 저장소 통합 4개.
- 기존 관련 회귀 69개: checkpoint 코드 식별·공유 잠금·수동 정리·기존 자동 정리·restic 예약.
- **108개 전부 통과**, 실패/오류/skip 0개, 787.13초.
  [candidate-final-tests.xml](candidate-final-tests.xml). 기존 fork 잠금 검사에서 Python의
  다중 thread 후 fork 경고 1건이 있었으나 해당 자식 잠금 검사는 통과했다.
- 기준 배포본 138개 파일에 [candidate.patch](candidate.patch)를 별도 경로에서 적용했다.
  결과 144개 파일이 manifest와 일치하며 `uv sync --offline --frozen --no-editable
  --link-mode=copy`로 새 환경을 설치했다. [재구성 기록](patch-application.log),
  [실제 import 경로](reproduced-environment.json). 압축 실행 기록은
  [패치 적용](patch-application.log.gz), [108개 실행](candidate-final-tests.log.gz),
  [동일 39개 재현](reproduced-clock-tests.log.gz)에 보존한다.
- 재구성 경로에서 T05 **동일 39개** 검사가 151.56초에 통과했다.
  [reproduced-clock-tests.xml](reproduced-clock-tests.xml). 새로운 39개로 합산하지 않는다.
- 초기 27개·31개·통합 4개 실행은 개발 중간 근거다. 최종 합계에 중복 합산하지 않는다.

## 중요한 분기

| 범위 | 검사한 결과 |
|---|---|
| reference age와 조회 나이 | 900·3,600초 reference를 현재 유효한 조회로 판단. 조회 자체 15초 만료는 거부 |
| 다른 시각 이상 | 미래 reference, 미동기화/윤초 대기, 보정·오차·skew 초과, CSV 비정상·NaN, 조회 지연/실패 거부 |
| 연속성 | 앞/뒤 UTC 급변, suspend 차이, 표본 공백, boot/provider/config 변경, 로그 실패를 해당 실행에 고정 |
| 72시간 | epoch 크기의 정수 ns에서 경계 -1ns 보호, 정확한 경과/초과 판정. 최근 3개 보호와 합집합 |
| 완료 상한 | 원래 completed_at 보존, 검증된 완료 사본의 사후 관측만 상한으로 사용. 결여·변조·다른 snapshot/manifest 거부 |
| 실제 임시 삭제 | 충분한 근거가 있는 후보 2개만 삭제, 최근 3개 검증·원본 hash·restic 상태 보존 |
| 중간 시각 급변 | staging 이동 이후 +41초를 주입하면 파일 unlink 전에 중단. 미완료 journal·실패 시도 보존, 새 preview 보류 |
| 명시적 복구 | 같은 plan의 새 수동 복구 승인으로 남은 작업만 완료. 이전 실패 삭제/초기화 없음 |
| 승인 | 과거 purpose 거부, 수동 승인을 recurring 경로에 복사해도 거부, 반복 적용은 already_complete·새 삭제 0개 |
| 실행 환경 | root 소유 승인 요구, 실효 fragment·drop-in·reload·중복 단일값 검사. 실제 system manager 접근은 아래 별도 시험 |
| 기존 계약 | 당일 checkpoint와 같은 backup ID의 저장/전체 검사·복구 영수증·잠금·입력 inventory·중단/재개 검사는 유지 |

임시 통합 시험에서는 Host, 시계 표본, root 소유권/manager 출처를 시험용으로 주입한다.
실제 restic 데이터 경로는 모두 임시 저장소다. 운영 권한 통과나 실제 시각 제어의 증거로 표현하지 않는다.
운영 네트워크 장애나 시각 급변은 주입하지 않았다.

## 실제 호스트의 읽기 전용 확인

[host-clock-probe.json](host-clock-probe.json): 현재 부팅의 closed 준비·공유 잠금,
현재 Chrony invocation·고정 설정과 새 tracking을 확인했다. reference age 약 1,464초에서도
조건부 오차와 조회 자체의 유효성을 통과했다. 시각/RTC 제어는 하지 않았다.

[user-preview-result.json](user-preview-result.json): **11:46:26–11:46:27 KST** 실행.
현재 Chrony 준비는 통과했으나 `daily_not_today`로 보류했다.
해당 날짜 checkpoint/restic 선행 작업이 충족되지 않았으므로 후보 산정 전 종료했다.
`selection_assessed=false`, plan ID 없음, 삭제 0개다. 이 결과를 후보 0개나 전체 미리보기
통과로 표현하지 않는다. [시각 관측](user-preview-clock.jsonl).
이 실행은 일반 계정 직접 실행이며 최종 시스템 보호 환경 검증은 아니다.

## 관리자 시스템 시험 — 접근 통과, 선행 작업 조건으로 미리보기 보류

[system_preflight.py](system_preflight.py) `--check`에서 고정 입력과 설치 환경 1,644개를
검증했다. sandbox의 root UID 매핑 차이는 실제 호스트의 읽기 전용 재검사로 구분했으며
소유권 검사를 낮추지 않았다.

사용자가 관리자 인증하에 `--run`을 실행했다.
[원본 결과](system-preflight-result-ae5d62d13d034b72.json)의 사실은 다음과 같다.

- 11:59:49–11:59:50 KST, `User=yoonhc`/UID 1000, 시스템 manager의 현재 start Job·PID·invocation 확인.
- 검토한 보호 옵션 20개 일치, CapEff/CapBnd=0, NoNewPrivs=1.
- 원본·checkpoint·restic 데이터 경로 및 고정 배포본/설정은 readonly mount로 확인.
- 실제 Chrony 조회·bootstrap SH 잠금·시험 상태 fsync·repository ID와 snapshot 15개 조회 성공.
- reference age 1,048.03초, 조건부 오차 0.125822초, provider 조회 0.011916초,
  현재 관측 나이 0.000737초로 준비 통과. reference age만으로 보류하지 않았다.
- 당일 유지보수 daily가 아직 없어 `daily_not_today`, `selection_assessed=false`,
  plan ID 없음, 삭제 시도 false·삭제 0개.
- `access_passed=true`, `preview_completed=false`, 전체 `passed=false`.
  접근 검증 실패가 아니라 기존 선행 조건에 따른 보류이며 전체 미리보기 완료는 아니다.
- 시험 경로 모두 제거·유닛 not-found, cleanup 오류 0개. 133개 운영 파일과 Chrony·두 supervisor 전후 동일.

최종 후보·검사는 그대로 고정한다. 당일 checkpoint/restic 전체 검사 실제 완료 후 후보 산정까지
확인한다. 운영 경로·권한 접근을 통과시키려고 보호 옵션이나 기존 검사를 낮추지 않았다.

## 남은 범위

최종 운영 경로 설치, 실제 첫 후보 삭제, 반복 grant와 timer 활성화는 각각 승인 전이다.
현재 시스템 retirement 운영 유닛은 미설치이며 기존 사용자 유닛은 masked다.
삭제 없는 미리보기와 0건 실행을 실제 삭제 검증으로 확장하지 않는다.
