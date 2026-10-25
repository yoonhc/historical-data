# 설치 완료 후 한 번 재개 — 실행 완료, 반복 금지

**10/17 20:32 실행과 종료 처리가 모두 완료됐다.** run `f57f334b…`에서 승인 대상 10개를 정리했고
manual을 원래 비활성으로 복원했다. [완료·독립 대조](completion.md).
아래 명령은 감사 이력이며 apply/finalize를 다시 실행할 필요가 없다.

## 당시 승인과 명령

**사용자의 명시적 승인을 받았다.** [승인 기록](resume-approval-received.json)과
[직전 대조·sudo 인증 부족](resume-sudo-authentication.json)을 보존한다.
도구 세션에서는 프로그램이 시작되지 않았고, 이후 사용자가 터미널에서 아래 apply를 한 번 실행해 완료했다.

이 명령은 새 operation `821367d24e43bd2423da568dfbca8cab47ac9c40fe405f09feadea567bec4824`의
[재개 계약](resume-authorization.md)을 명시적으로 승인한 뒤에만 실행한다.
네 구성 파일은 이미 설치됐다. 원래 `repair_and_resume.py --action apply`는 반복하지 않는다.

실제 호스트 대조만 수행:

```sh
/usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-mount-recovery-2026-10-17/resume_only.py --action check
```

승인된 한 번의 재개:

```sh
sudo /usr/bin/python3 -B -I \
  /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-mount-recovery-2026-10-17/resume_only.py \
  --action apply \
  --approve-operation 821367d24e43bd2423da568dfbca8cab47ac9c40fe405f09feadea567bec4824 \
  --approve-retirement-plan 31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08 \
  --approve-recovery-run 2d9f7b8b4a434183a875dfa441171295
```

기동 intent가 생긴 뒤에는 apply를 반복하지 않는다. 터미널 중단 시 실제 서비스와 root state를 먼저 확인한다.
같은 실행의 종료 확인·해당 manual grant 비활성 복원만 이어가기:

```sh
sudo /usr/bin/python3 -B -I \
  /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-mount-recovery-2026-10-17/resume_only.py \
  --action finalize \
  --approve-operation 821367d24e43bd2423da568dfbca8cab47ac9c40fe405f09feadea567bec4824
```

finalize는 서비스를 시작하지 않는다. 서비스가 아직 동작하면 강제 종료하지 않으며 grant를 임의로 회수하지 않는다.
잠금 경합·가까운 예약·조건 변화는 그대로 보류한다. 다른 업무·timer를 중지하거나 조건을 완화하지 않는다.
새 실패·부분 삭제는 원본을 검토하고 별도 재개 판단을 한다. 삭제 결과를 되돌리는 명령은 없다.
