# 삭제 없는 시스템 미리보기 — 완료

2026-10-11. 고정 후보 **`t05-completion-evidence-20261011-c9d413cfe11ec0e7`** 유지.
사용자가 승인한 다음 단계는 설치 전 미리보기다. 운영 설치·기존 집합 등록·삭제 승인은 아니다.

**15:49:40–15:50:06 KST, 실제 임시 시스템 유닛의 preview와 읽기 전용 inventory가 모두 완료됐다.**
[원본 관리자 결과](system-preflight-result-5dde262e40b34192.json),
[원본 대조·요약 수치](system-preflight-review.json).

| 확인 | 실제 결과 |
|---|---|
| 실행 환경 | 시스템 manager, `User=yoonhc`/UID 1000, effective/bounding capability 0 |
| 보호 옵션 | ProtectClock·ProtectSystem·ProtectHome 등 비교한 20개 속성 일치 |
| 운영 저장소 접근 | 원문·상태·checkpoint·restic·archive 상태 읽기 전용. 기존 lock과 시험 상태/cache만 쓰기 허용 |
| 관리 대상 현존 사본 | **7개**, preview와 inventory의 backup ID 집합 일치 |
| 범위 제외 사본 | **3개**, 모두 기존 `before_enrollment` 규칙 유지 |
| restic 조회 | snapshot **16개**. 당일 backup ID와 저장·전체 검사 완료 근거 연결 |
| 정리 판단 | **7개 모두 보호, 삭제 후보 0개** |
| 보호 이유 | `completion_evidence_missing_or_unarchived`와 `fewer_than_three_proven_successors` |
| 근거 등록 | **0건**, 시험 registry도 생성되지 않음. restic 보존된 새 근거 객체 0개 |
| 삭제·시각 제어·운영 설치 | **모두 수행하지 않음** |
| 정리 | 시험 경로 부재, 유닛 `not-found`, cleanup 오류 0개 |
| 운영 유지 | 파일/경로 142개와 boot ID·Chrony/두 supervisor PID·invocation·active 상태 전후 일치. 정리 mask 유지 |

미리보기 본체는 **25.238초** 걸렸다. 전체 설치·준비·inventory·정리까지 포함한 소요 시간이나
실제 삭제 성능으로 해석하지 않는다. 현재 7개가 보호되는 것은 새 근거 생성 기능이 아직
운영에 없기 때문에 예상된 결과다. 원래 completed_at의 오래된 날짜만으로 보호를 해제하지 않았다.

## 실제 시각 관측

시험 로그의 provider 관측 7개는 모두 유효했다. 총 불확실성은 **0.1466–0.1493초**,
조회 소요는 **11.0–13.7ms**, reference age는 **1,654.46–1,680.91초**였다.
계획 판단에 사용한 관측은 Chrony 조건부 오차 `0.135575979초` + 조회 여유 `0.013288858초`
+ 표본 여유 `0.000003106초` = **U 0.148867943초**다.
600초를 넘은 reference age 자체로 차단하지 않고 유효한 현재 관측의 U 5초 상한을 적용했다.
reference 지연을 인위적으로 만들거나 Chrony를 재시작하지 않았다. 이 관측은 조건부 추정이며
절대 UTC 정확도 인증이 아니다. 실제 삭제 단계의 수명·중단 처리는 이번 삭제 없는 시험 결과와 구분한다.

## 선행 확인

[선행 작업 근거](preview-prerequisites.json)를 읽기 전용으로 확인했다.

- maintenance daily: **13:25:35–14:38:09 KST**, `ok=true`, `state=complete`.
- backup ID: `9849b0ef7e523cbc79546c233f409ecd93cdcc8e1b5bc4f48fe8163229968a77`.
- 같은 사본의 restic snapshot:
  `fd78b31add535359af3e2e6f77d13fede7db632308bf0bad2688d4a78b14324a`.
- 저장 완료 **15:28:59**, 전체 읽기 검사 완료 **15:30:19 KST**.
  해당 snapshot이 검사 목록에 포함되며 archive active operation은 없었다.
- archive 관리 기록은 16개다. 실제 현존 사본·과거 정리된 사본의 구분과 보호 사유는
  새 미리보기에서 기존 journal까지 대조한다.
- system manager의 Chrony·두 supervisor는 active/running. 사용자 backup/restic은
  정상 종료 상태이며 사용자 정리 service/timer는 masked/inactive였다.

이는 조회 시점의 선행 확인이다. 실제 시험에서도 잠금 아래 현재 상태를 다시 대조한다.
당일 백업이 완료됐다는 사실만으로 어떤 사본의 삭제를 허용하지 않는다.

## 준비한 시험

[system_preflight.py](system_preflight.py)와 [probe.py](probe.py)는 이전 관리자 접근 시험을
재사용하며 새 후보의 공유 시각 모듈·비등록 미리보기·읽기 전용 inventory를 연결한다.
후보 151개 소스와 정책·환경은 바꾸지 않았다.

입력 검증에서 전체 215개 최종 결과와 58개 재현 결과, 소스·비-editable 환경
**1,646개 파일**, 운영 파일/경로 **142개**를 대조했다.
최초 도구 격리 환경의 검증은 호스트 소유권 검증을 대신하지 못했다. 아래 실패를 보존하고
**15:47:05 KST 실제 호스트 환경에서 동일한 `validate()`를 다시 통과**했다.
[고정 시험 입력](preflight-inputs.json), [운영 파일 기준](host-before.json).

`--run`은 다음 범위만 실행한다.

1. `/opt/md-t05-evidence-preview-<nonce>`와 `/var/lib/md-t05-evidence-preview-<nonce>`에
   시험 코드·설정·상태·cache를 만든다. 실제 `/opt/marketdata` 배포는 하지 않는다.
2. `/run/systemd/system/md-t05-evidence-preview-<nonce>.service`를 임시로 실행한다.
   User=yoonhc, 빈 capability, ProtectClock/ProtectSystem/ProtectHome 등 최종 preview 보호 조건을 유지한다.
3. 실제 Chrony 현재 관측·부팅 잠금, restic identify/snapshot 목록, 기존 checkpoint/manifest/정리
   journal을 읽고 `preview`와 `inventory`를 실행한다. backup·restore·check --read-data를 재실행하지 않는다.
4. 근거 registry 생성/등록이 없고 삭제 0건인지 확인한다. 보호 사유·관측·계획은 시험 상태에만 남긴다.
5. 시험 결과를 보고서에 보존하고 **해당 시험 유닛·프로세스·nonce 경로만** 정리한다.
   운영 파일·Chrony/수집 supervisor와 기존 mask를 전후 대조한다.

자료·checkpoint·archive 상태와 실행 코드는 임시 유닛 안에서 읽기 전용이다.
기존 협동 lock 및 restic lock 접근은 이전 시험과 동일하게 유지하며, 쓰기 가능한 시험 상태/cache와 구분한다.
이때 만든 plan/inventory는 임시 경로·scope에 묶인 검토 자료이며 운영 등록/삭제 승인으로 이식하지 않는다.

## 첫 관리자 실행의 입력 검사 실패와 보완

도구에서 `sudo -n ... --run`을 시도했으나 **`sudo: a password is required`**로
Python 시험 코드가 실행되기 전에 종료됐다. 새 시험 유닛이나 `/opt`·`/var/lib` 시험 경로는 만들지 않았다.
이후 사용자가 터미널에서 실행했으나 `validate()`가
`Operating configuration changed: /etc/chrony/marketdata.conf`로 중단됐다.
`perform()` 진입 전이므로 이 시도에서도 시험 유닛·경로를 만들지 않았다.

원인은 운영 설정 변경이 아니라 **도구의 user namespace에서 소유권 기준을 수집한 오류**다.
그 환경에서는 호스트의 root가 매핑되지 않아 UID 65534로 보였고 실제 관리자 실행은 UID 0을 읽었다.
Chrony 설정의 SHA-256은 양쪽 모두
`7a89504e8794aa929ffcc125a4bf5e6689bec4513ca902461329d141527fbb86`, 모드는 `0644`였다.

- [원래 기준](host-before.sandbox-uid.json), [원래 입력 해시](preflight-inputs.sandbox-uid.json),
  [사용자 실패 출력의 기록·실행 전 중단 근거](preflight-validation-failure.json)를 보존했다.
- [호스트 대조](host-metadata-comparison.json)에서 **142개 경로의 내용·권한·링크·존재 여부가 모두 일치**했다.
  root 소유 파일 10개의 UID 표시만 65534→0이었다. 이 중 8개는 이전 호스트 기준과도 완전히 같았다.
  실제 호스트 값을 관측한 뒤 기준과 연결 해시만 갱신했으며, UID 검사를 제거하거나 숫자를 일괄 치환하지 않았다.
- 후보 소스·환경·`system_preflight.py`·`probe.py`는 수정하지 않았다.
  소유권·해시·보호 옵션 검사는 그대로다.
- [15:47 호스트 입력 검증](preflight-host-check.json): 고정 소스 151개·환경 1,646개·운영 경로 142개 통과.
  Chrony와 두 supervisor의 PID/invocation 유지, 정리 service/timer masked/inactive,
  해당 접두사의 시험 경로 0개를 확인했다.
- 수정 입력 SHA-256:
  `555179295ac49282689797740ac7ba15baff256777567bf90cb1ae01180d1a71`.

이 입력 검증 성공을 실제 임시 시스템 유닛의 preview 성공으로 표시하지 않는다.

## 실제 재실행

도구에 sudo 인증이 공유되지 않아 사용자가 터미널에서 아래 명령을 재실행했다.
결과 `passed=true`, `access_passed=true`, `preview_completed=true`, `operating_unchanged=true`를
원본 JSON의 계획·inventory·관측·실효 유닛·전후 상태와 대조했다.

```sh
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-completion-evidence-2026-10-11/system_preflight.py --run
```

실행 결과와 정리한 시험 상태 내용은 nonce별 원본 JSON에 보존했다.
원본 SHA-256: `0c9716a1c72ad96a5510170030c7a4a512df369d3d58405954d40e994d121ee1`.
입력 기준 보완 과정과 첫 실패는 위 기록에 그대로 남긴다.

## 다음 단계와 남은 한계

이번 단계는 **설치 전 미리보기 완료**다. 최종 운영 경로에 대한 설치·근거 쓰기·정기 백업의
완료 후처리·sidecar의 실제 restic 보존/복원은 아직 수행하지 않았다.
현재 plan/inventory는 임시 코드·설정·cache·상태 경로에 묶여 있어 운영 승인에 재사용하지 않는다.

[별도 승인할 범위](deployment-review.md#2-별도-승인할-설치근거-생성-범위--자동-정리-계속-보류)는
고정 배포본·근거 생성/보관 후처리·시스템 실행 환경 설치와 기존 목록/사후 근거 등록이다.
그때 최신 inventory와 설치 경로의 해시를 다시 고정하며 자동 정리는 계속 masked로 둔다.
후행 사본 3개·72시간+60초·restic 보존 조건이 실제로 갖춰진 뒤 새 미리보기,
별도 승인한 첫 수동 삭제, 별도 반복 자동화 순서로 진행한다. **T05 전체는 미완료**다.
