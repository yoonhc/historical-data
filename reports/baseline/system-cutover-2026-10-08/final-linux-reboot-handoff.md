# 최종 Linux 재부팅 인계

고정 배포본 `system-bootstrap-20261007-f85eb8321a19c1ee` 설치와 실제 권한 검사가 완료됐다.
현재 boot ID는 `9e33f0bc-b734-45c9-8a85-8aa6c35efa71`이며 `installation_hold`, `step_attempts=0`이다.
23:57:07 KST에 Chrony의 실제 시각 관측이 기존 준비 기준을 통과했고,
23:58에 root 소유권·원래 설정/승인 해시·업무 미실행과 시험 유닛 정리를 확인했다.

사용자는 승인된 마지막 부팅 검증을 위해 **Linux에서 다시 시작하고 다시 Linux로 부팅**한다.

```bash
sudo /usr/bin/systemctl reboot
```

이 명령은 실제 재부팅이다. 열려 있는 개인 작업을 저장한 뒤 실행한다.
Windows를 다시 방문하거나 RTC 설정·enroll·설치 명령을 반복할 필요는 없다.
Linux가 올라오면 이 대화에 복귀 사실을 알려준다. 에이전트는 다음을 실제 기록으로 확인한다.

1. 새로운 boot ID의 bootstrap 시작·필요 시 최대 한 번 step·closed 기록과 현재 Chrony 품질.
   정상 RTC면 step 0회도 정상이다. 준비에 시간이 걸려도 기준을 낮추거나 ledger를 지우지 않는다.
2. 같은 고정 배포본·UID 1000의 두 system collector 기동, 옛 user collector와 중복 없음.
3. 실제 원문/표준 저장·depth REST 대조·aggTrade 연결·시각 관측 로그.
4. 공통 gate 뒤의 기존 업무 재개. 기동/예약 요청을 업무 성공이나 백업 성공으로 대신하지 않는다.
5. 자동 checkpoint 정리 service/timer의 persistent mask 유지.

수집 공백은 10/8 21:40:09 KST부터 실제 새 수신 시각까지다. 그 구간의 호가 원문을 확보했다고 기록하지 않는다.
첫 정상 1시간 세션 종료·다음 시작과 이후 새 checkpoint/restic 확인은 실제 발생한 뒤 별도로 기록한다.
이번 인계는 부팅 뒤 수집·저장 검증까지 끝났다는 뜻이 아니다.

문제가 있으면 현재 ledger/로그/원래 유닛/이전 실패 기록을 보존하고 점검한다.
원문·manifest/source_root·기존 사본·정리 grant를 고치거나 삭제하여 시작 조건을 통과시키지 않는다.
