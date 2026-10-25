# T05 첫 수동 정리 — 실행 후 삭제 전 보류

**10/17 16:15:25 KST 실제 시도는 exit 75·삭제 0개로 보류됐다.** 수동 유닛이 심볼릭 링크로 설치돼 있지만
삭제 경로의 `installed_service()`는 root 일반 파일을 요구해, 시각 조회·사본 검사 전에 거부했다.
manual 비활성 복원·기존 사본/근거·프로세스·네 mask 유지와 journal 부재를 확인했다.
원래 도구는 이전 실행이 있으면 다시 시작하지 않는다. 아래 준비 당시 명령을 반복하지 않는다.
동일 유닛 내용의 설치 형식 보완과 같은 계획 재시도는 [별도 보완 보고서](../t05-first-manual-recovery-2026-10-17/status.md)를 따른다.

## 아래는 최초 실행 전 준비 기록

2026-10-17 사용자가 **“오케이 첫 수동 정리 진행하자”**고 승인했다.
[승인 범위](authorization.md)는 정식 계획
`31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08`의 후보 10개에 한정한다.
[미리보기의 정확한 목록](../t05-formal-preview-2026-10-17/selection-table.md)과 보호 3개·범위 밖 3개를 유지한다.
자동 정리·중단 후 재개는 승인하지 않았다.

실제 설치본의 `propose-manual`로 [현재 boot·scope·정확한 계획의 비활성 요청](manual-proposal.json)을 만들었다.
[실행 도구](manual_apply.py)는 이 요청에 받은 승인을 연결해 `manual.json`을 확정하고,
검증된 설치 시스템 유닛을 한 번 시작한 뒤 종료를 확인해 원래 비활성 바이트로 복원한다.
도구 자체에는 checkpoint 파일을 삭제하거나 검사를 생략하는 코드가 없다.

**최초 준비 당시에는 정리 미실행이었다.** 읽기 전용 실제 호스트 대조와 격리 검사 7개는 통과했지만,
`sudo -n`은 비밀번호가 필요해 종료 코드 1로 끝났다. root 도구·승인 파일 갱신·서비스 시작·삭제는 실행되지 않았다.
이는 운영 승인을 다시 요청하는 상태가 아니라 도구 세션에 관리자 인증이 없는 상태다.
[검증과 인증 대기 기록](validation.json)을 보존한다.

## 기동 전 확인한 범위

- 현재 boot `eb163bf2-6642-4336-b4d6-6cd58771b13d`, scope `da7b03db…`, 계획 파일 바이트·코드·정책이 일치했다.
- 실제 backup·restic·pack과 preview가 유휴였다. 보존 업무를 강제 중지하지 않았다.
- 보호 입력 91개·기존 근거 14개/등록 receipt·비활성 recurring/manual·정리 네 mask와 Chrony/두 supervisor를 대조했다.
- 실제 수동 인스턴스의 유닛 경로·UID 1000·고정 명령·drop-in 없음·reload 불필요를 확인했다.
- 기존 수동 run/journal이 없고 runner·coordinator/config 잠금 획득이 가능했다.
- 격리 7개 검사에서 정확한 계획/boot/scope만 승인, 다른 계획·recurring·resume 거부,
  oneshot 실행/대기 구분, 유휴 종료 후 원래 승인 파일 복원, 실행 중·변경된 권한·경합 시 덮어쓰기 거부를 확인했다.
  이 검사는 실제 삭제 검증을 대신하지 않는다.

## 실행

```sh
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-first-manual-2026-10-17/manual_apply.py --run --approve-plan 31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08
```

같은 명령은 이미 실행한 계획을 자동 재시도하지 않는다. 기동 전에 현재 입력을 다시 대조하며 변경·경합은 보류한다.
관리자 감사 사본은 `/var/lib/marketdata-clock/t05-manual-applications/<Plan ID>/<attempt ID>/`에 기록한다.
실제 업무는 `marketdata-checkpoint-retirement-apply@<Plan ID>.service`에서 UID 1000으로 수행한다.

설치된 정리기는 당일 backup/restic·복구 근거·현재 시각·선후 관계·scope를 재검증한다.
유지 사본 세 개의 전체 무결성 검사와 모든 후보 inventory 대조 후, 최초 삭제 직전에 현재 시각을 다시 조회한다.
장시간 검사 중에는 원래 유지 사본을 삭제하지 않는다. 기대 결과는 같은 계획의 `deleted_this_run=10`,
완료 journal과 후보 부재, 보호/제외 사본·근거·restic·수집기/Chrony 유지다.

도구는 기다리는 동안 coordinator **SH**와 구성 디렉터리 잠금만 유지하며,
업무가 필요로 하는 maintenance/archive/checkpoint/runner 잠금을 점유하지 않는다.
서비스가 끝나 유휴일 때만 수동 승인 파일을 원래 비활성으로 복원한다.
기다림 중 도구가 종료되거나 서비스가 계속 실행 중이면 강제 중지·승인 덮어쓰기를 하지 않는다.
그 경우 정확한 서비스 상태와 root 기록·원래 run/journal을 확인하고 정리한다.
부분 삭제·실패를 성공으로 고치거나 `resume_interrupted`를 자동 활성화하지 않는다.

## 완료 후 확인

실제 run/시각 관측·journal·후보 소멸·유지/제외 manifest·restic 조회 receipt·전후 공간·기존 근거·
manual 비활성 복원·recurring과 네 mask·프로세스 신원을 대조한다.
필요한 감사 사본과 결과를 이 보고서에 연결하고, 그 결과를 확인한 뒤 반복 자동화는 별도로 승인한다.
