# 고정 계획과 실행 명령

**최신 결과: 10/17 20:32 후속 한 번 재개까지 완료했다.** [첫 수동 정리 완료](completion.md).
이 문서와 `resume-commands.md`의 apply/finalize는 모두 과거 실행 이력이며 반복하지 않는다.

**10/17 18:47 아래 apply는 이미 실행됐고 시작 intent를 사용했다. 반복하지 않는다.**
네 구성 파일은 after로 설치됐다. 정리 run은 새 repository lock Busy·삭제 0개이며 manual은 비활성으로 복원됐다.
다음 재개는 [새 계약·명령](resume-commands.md)의 별도 검토 범위다. 아래 명령은 기존 작업의 감사·복구 참고로 보존한다.

- operation plan: `eced87a499269ebe5da84be1b0795ebfd2c109ce06f37db4e5e26f8027b9e231`
- retirement plan: `31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08`
- reviewed EXDEV run: `5d7deaf59b1f4c99847a732327ce77af`
- tool SHA-256: `f079147fae7d13251855684e98ad4a0ec8eba27bc0da127d08c251b667515133`

네 구성 파일 수정·새 scope의 한 계획 재개·종료 후 비활성 복원을 함께 승인한 경우:

```sh
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-mount-recovery-2026-10-17/repair_and_resume.py --action apply --approve-operation eced87a499269ebe5da84be1b0795ebfd2c109ce06f37db4e5e26f8027b9e231 --approve-retirement-plan 31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08 --approve-recovery-run 5d7deaf59b1f4c99847a732327ce77af
```

시작 intent 이후 같은 apply를 반복하지 않는다. 실제 실행 상태와 root state를 검토한 뒤 기존 실행의 종료 처리만 이어가는 명령:

```sh
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-mount-recovery-2026-10-17/repair_and_resume.py --action finalize --approve-operation eced87a499269ebe5da84be1b0795ebfd2c109ce06f37db4e5e26f8027b9e231
```

유휴·manual 비활성 확인 뒤 네 구성 파일만 되돌리는 승인된 원복 명령. 작업 기록·삭제 결과는 그대로 둔다:

```sh
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-mount-recovery-2026-10-17/repair_and_resume.py --action rollback --approve-operation eced87a499269ebe5da84be1b0795ebfd2c109ce06f37db4e5e26f8027b9e231
```

새 실패·일부 삭제·변경된 입력이 있으면 자동 재개하지 않는다. 이전 Busy 재개 명령도 사용하지 않는다.
시각/당일 보존·기존 검증 조건이 달라지면 원래 조건대로 보류하며 이 계획으로 우회하지 않는다.
