# T05 수동 적용 — 유닛 보완 완료, repository 잠금 보류와 정확한 재개 제안

**10/17 17:15 최종 결과:** 명시적 재개 run `5d7deaf5…`는 유지 사본 검사·후보 inventory 생성 뒤 첫 rename의 `EXDEV`로 실패했다. 삭제 0개·원래 사본 16개/manifest 보존·journal 전부 prepared·staging 비어 있음이다. manual/recurring 비활성·네 mask·기존 근거·프로세스 유지. 별도 mount 경계의 격리 재현과 공통 부모 수정 후보까지 확인했으며 실제 system 시험·변경 scope/중단 journal 복구 절차는 남아 있다. [최신 결과와 다음 단계](../t05-first-manual-exdev-2026-10-17/status.md)를 따른다. 아래는 실패 전 시점의 기록이며 이전 Busy 재개 명령을 반복하지 않는다.

**10/17 17:10 재확인:** 같은 run/PID로 시작 후 약 19분간 진행 중이다. 16:58의 10/16 사본에서
현재 10/15 사본의 catalog·pack 읽기로 이동했다. 승인된 유지 검사 순서 **10/16 → 10/17 → 10/15**와
설치된 순차 호출을 대조하면 세 번째 유지 사본 검사 단계로 판단된다. 개별 완료 보고서를 별도로 만드는 구현은 아니므로
이 판단은 열린 파일·호출 순서에 근거한 진행 관찰이며 최종 검증 성공 receipt는 아직 없다.
10초간 실제 디스크 읽기 **2.057GB**, 프로세스 읽기 **4.364GB**, CPU **8.2초** 증가했다.
17:09:46 조회에서는 시각 관측 ready/U 0.13894초·fault 없음, journal/inventory 없음이다.
[I/O·파일·잠금 표본](progress-20261017T081005.json), [후속 run·순서·journal 확인](progress-run-20261017T0810.json).
진행은 확인되나 파일 해시와 pack 전체/내부 검증을 사본별 반복하는 비용이 크며,
이 관측을 T14 계측 후속의 입력으로 남긴다. 이번 승인된 검증은 생략하거나 변경하지 않는다.

**10/17 16:58 진행 확인:** 사용자가 명시적 재개 명령을 실행했고, 실제 동일 계획/run 승인 인자의 root 실행기와
16:50:43에 시작한 run `5d7deaf59b1f4c99847a732327ce77af`를 확인했다. 현재 적용 진행 중이며 완료 결과는 아직 없다.
UID 1000/PID 2533542가 maintenance·archive·checkpoint 배타 잠금을 보유하고,
유지할 10/16 checkpoint의 catalog와 pack을 읽고 있다. **8초 동안 프로세스 읽기 3.84GB,
실제 저장장치 읽기 1.94GB, CPU 7.81초 증가**와 열린 pack 파일 변경을 확인했다.
잠금 대기가 아니라 유지 사본 검증의 실제 진행 근거다. 시각 관측도 이어지며 이 확인 시점에 삭제 journal은 없다.
전역 `applying`/오래된 run.json만으로 정지나 실제 삭제 시작을 판정하지 않는다.
[진행 표본·실제 잠금/파일](progress-20261017T0758.json), [run·감시 상태](progress-run-20261017T0758.json).
전체 완료나 소요 시간은 확정하지 않았다. 아래 승인 대기는 실행 전 준비 기록이다.

**2026-10-17 16:37 KST: 실제 삭제 0개, 첫 수동 정리 미완료.**
유닛 설치 형식 보완은 성공했지만, 이어진 정리 run은 실제 repository 잠금 경합으로 보류됐다.
시각 정책·당일 백업 부족에 의한 보류가 아니며, 새 삭제 엔진 결함으로 단정하지 않는다.
현재 **명시적인 한 번의 재개 승인 대기**다. 운영 설정이나 승인 파일을 이번 조사에서 변경하지 않았다.

## 실제 실행 연결

| 항목 | 결과 |
|---|---|
| 유닛 보완 | 16:36:55–58, root 일반 파일/0644·같은 SHA `9932a3c4…`·UID 1000 실제 guard 통과 |
| 보완 root receipt | `ce98161d74004d6d99ffbd5b9e6cdcec` |
| 수동 root attempt | `c359c6b4dfc242aead1e0ae8cd0cbb85` |
| 실제 정리 run | `d1c40fd557974ed9b462d1ea98e689ff`, 16:37:00.702–16:37:19.870 |
| 실제 업무 출처 | system 수동 start job, UID 1000/PID 2525426, invocation `07db7fc6a0754713bf364d121f916a91` |
| 현재 시각 관측 | ready, U **0.13485525초**, reference age 488.47초 |
| 보류 | `Busy: Another archive/check/recovery holds the repository lock; retry later`, exit 75 |
| 삭제 결과 | `deleted_this_run=0`, journal·staging·적용 inventory 없음 |
| 종료 후 권한 | manual 원래 비활성 바이트 복원, recurring 비활성·정리 네 mask 유지 |

[유닛 보완 결과](evidence/unit-repair/result.json), [수동 root 결과](evidence/busy-application/result.json),
[실제 run](evidence/busy-run/run.json), [원본 복사와 해시](evidence-manifest.json).
systemd는 `SuccessExitStatus=75` 때문에 성공 종료로 표시하지만 실제 정리 결과는 보류다.

같은 시각 restic timer가 **16:37:01.949**에 요청을 만들었다. 실제 archive 실행
`86b96508c34340e9be9e0f5641f2b2b2`는 **16:37:02.339–16:37:41.972**이고,
정상 종료·processed=0이다. 해당 설치 코드의 `execute()`는 이 실행 동안 repository lock을 잡는다.
정리기의 16:37:19 잠금 실패와 겹친다.
[timer 출처·실행 기록](evidence/overlapping-restic.json), [manager journal](evidence/manager-journal.jsonl),
[timer 속성](evidence/restic-timer.txt).

16:35 calendar 실행 외에 유닛 보완의 daemon-reload 직후 추가 timer 기동이 관측됐다.
`OnActiveSec=5s`가 구성돼 있으나, 재로드 시 내부 재계산 원인을 별도 시험으로 확정한 것은 아니다.
이번 재개에는 설치·daemon-reload가 필요 없고, 보존 업무 유휴·잠금 경합도 직전에 확인한다.
현재 업무를 중지하거나 timer를 꺼 충돌을 피하지 않는다.

## 실제 파일 변경 전에 멈췄는지

설치 `clock_retirement.execute()`는 **`agent.apply()` 호출 전**에 `deletion_attempted=true`를 쓴다.
따라서 이 값은 실제 unlink 또는 rename 수행을 뜻하지 않는다. 이번 `Busy`는 `Retirement.apply()`의
첫 `with self.locks()` 안에서 repository lock을 얻지 못해 발생했다.
유지 사본 전체 검증·후보 inventory 생성·journal 확정·rename/unlink는 그보다 뒤에 있다.

실제 호스트에서 다음을 확인했다.

- 원래 후보 10개, 보호 3개, 제외 3개 **모두 존재**, 16개 manifest 해시 일치.
- 후보 10개 root의 device/inode/mode가 승인 계획과 일치.
- 정확한 계획의 journal·staging·적용 inventory 경로 없음.
- 보호 입력 91개·기존 evidence/등록 파일 15개·Chrony/두 supervisor 신원 유지.
- 보완된 수동 유닛의 root:root/0644·일반 파일·동일 바이트 유지.
- 실제 run의 restic **dump 12개 성공**은 작은 근거 bundle 조회이며,
  전체 읽기 검사나 유지 사본 전체 데이터 검증을 새로 완료했다는 뜻이 아니다.

[읽기 전용 대조](readonly-audit.json). 유지 사본 전체 데이터 검사와 후보 전체 트리 검사는 실제 재개 시
설치된 엔진이 수행해야 하며, 이번 root/manifest 조회로 대신하지 않는다.
`selection_assessed=true`도 이 수동 경로에서는 기존 plan을 읽었다는 범위이며 최신 선택 재검증 완료로 확대하지 않는다.

## 왜 기존 명령 반복이 아닌 명시적 재개인가

설치된 `ClockRetirement.locks()`는 `deletion_attempted=true`인 미종결 이전 run을 확인하면
`resume_interrupted=false` 승인으로는 `unfinished_retirement`를 반환한다.
이전 실패 기록을 지우거나 플래그를 false로 고치지 않는다.

현재 [비활성 제안](manual-proposal.json)은 기존 제안에서 **`resume_interrupted`만 true**로 다르다.
동일 계획 `31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08`과 scope
`da7b03dba075a816d1c8277298dd123ce1db62e54aa9a85920711a18913a6c22`, 대상·보존 정책은 그대로다.
이를 운영 manual.json에 활성화하는 것은 아직 승인·실행하지 않았다.

[manual_resume.py](manual_resume.py)는 아래 두 원본 run의 ID·해시·정확한 사유만 허용한다.

1. `4a61c943702c41bcb4b5e854ac098440`: 실제 삭제 함수 진입 전 유닛 링크 거부.
2. `d1c40fd557974ed9b462d1ea98e689ff`: 함수 진입 뒤 실제 mutation 전 repository 잠금 거부.

새 run, journal/staging/inventory, 바뀐 사본 root/manifest가 있으면 이 재개안은 적용하지 않는다.
현재 boot·구성·provider와 preview anchor를 새 관측으로 확인한 뒤 설치된 **기존 복구 경로**를 사용한다.
그 경로는 오래된 plan anchor를 매 mutation에 요구하지 않고 현재 관측·감시와 보수적 기간 조건을 재검증한다.
이 차이도 [승인 범위](authorization.md)에 명시했다. anchor 검사를 삭제하거나 엔진을 수정하지 않는다.

## 검증과 남은 단계

**13개 통과:** 제안 차이/명시적 재개 승인, 정확한 이력·hash, 추가 시도/삭제 흔적 거부,
원래 사본 신원·manifest 변화 거부, 실제 임시 파일 flock 경합과 해제,
보존 업무 활성·대기 job 거부, 유휴 시 정확한 비활성 승인 복원.
그중 3개는 **실제 설치된 prior-run guard**를 격리 입력으로 실행해
기존 첫 수동 승인 거부·같은 계획의 명시적 복구 허용·다른 계획 거부를 확인했다.
업무/Chrony/systemd·다른 gate는 이 guard 시험에서 대역이며 운영 삭제 시험으로 세지 않는다.
[시험](test_manual_resume.py), [검증 기록](validation.json).

**현재 호스트의 삭제 없는 새 preflight 통과:** 설치 guard·유휴/실제 잠금·과거 원본·사본 상태·현재 시각/preview anchor와
고정 제안 일치. 운영 manual은 계속 비활성, 네 mask 유지. 제안 승인 및 실제 적용 성공은 별개다.
잠금 입장 검사는 이후의 timer 경합까지 보장하지 않으며, 실제 엔진의 잠금이 최종 보호다.
다시 경합하면 보류 후 새 기록을 검토하고 자동 반복하지 않는다.

명시적으로 이 재개 범위를 승인할 때 사용할 명령:

```sh
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-first-manual-resume-2026-10-17/manual_resume.py --run --approve-plan 31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08 --approve-recovery-run d1c40fd557974ed9b462d1ea98e689ff
```

루트 도구는 설치·timer 제어를 하지 않으며 한 번의 정확한 수동 서비스만 요청한다.
정상 종료 뒤 manual 비활성 복원·삭제 10개/유지 3개/제외 3개·journal·실제 여유 공간과 근거 보존을 확인한다.
반복 자동화는 계속 별도 승인이다. 현재 T05 전체 완료로 표시하지 않는다.
