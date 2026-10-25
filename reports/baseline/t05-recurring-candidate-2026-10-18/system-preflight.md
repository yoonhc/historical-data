# T05 후보의 전용 관리자 환경 시험

**10/18 14:34:26 KST root 재점검 완료.** 첫 실패와 도구 보완은 아래에 보존한다.
최초 도구 세션의 `sudo -n`은 인증 부족으로 프로그램 시작 전에 종료됐고,
이후 사용자 터미널에서 아래 첫 실제 시험을 실행했다. 운영 적용 승인을 요청하는 단계가 아니다.

## 재점검 결과

[원본](system-preflight-result-fe42ff4321294b9a.json), [후속 대조·해시](system-preflight-review.json).
실행 시각은 14:33:49–14:34:26 KST다. 원본 SHA-256:
`bbabde69b834da8661703057c3add83b7820c80f940cc1257afd9998f3c58e95`.

| 항목 | 실제 결과 |
|---|---|
| system timer 실행 | invocation `5552754101484102a17497095943d034`, start job `5288385`, `verified_timer` |
| 같은 service의 수동 시작 | invocation `4ce3c458387c4613b0ae28730009560d`, start job `5288524`, activation details 없음·scheduled=false |
| 실행 환경 | 두 실행 UID 1000·후보 import·CLI help·실제 root fragment guard 통과, 보호 옵션 유지 |
| 읽기 전용 시각 | U 0.1339053/0.1344654초, ready=true. reference age 716초대 원값 보존 |
| 비활성 승인 | 두 실행 모두 authorization_pending/75, deletion_attempted=false·삭제 0개 |
| 운영 경계 | 보호 구성 15개·네 mask·Chrony/두 supervisor·boot 전후 동일 |
| 시험 정리 | 오류 0개, 두 유닛 not-found·PID/job 없음, 전용 경로 부재 |

이 시험은 완료했으므로 같은 점검 명령을 다시 실행할 필요는 없다.
운영 최종 `/opt` 설치·활성 grant·timer 활성화·실제 자동 삭제와는 별개다.
기존 입력 seal과 검증 파일은 시험 직전 상태로 보존하고 이후 완료는 별도 review에 기록했다.

## 첫 실제 시험과 보완

10/18 14:28:28–14:29:05 KST [원본](system-preflight-result-dc353c8dacb4461c.json)은
첫 timer service가 정상 종료한 뒤 같은 service를 수동 시작하려다 `Unit ... not found`로 실패했다.
원래 도구는 상세 worker 결과를 전체 성공 때만 root 보고서에 넣었으므로,
첫 실행의 journal 성공과 상세 시각/출처 결과의 보관 완료를 구분한다. 전체 시험을 통과로 표시하지 않는다.

`systemd-run` v255는 transient timer에 `RemainAfterElapse=false`를 설정한다.
날짜를 하나 지정한 timer는 마지막 실행 뒤 회수될 수 있다.
[공식 구현](https://raw.githubusercontent.com/systemd/systemd/v255/src/run/run.c),
[timer 수명 옵션](https://www.freedesktop.org/software/systemd/man/255/systemd.timer.html#RemainAfterElapse=).
시험 calendar를 같은 UTC 시각의 매일 반복으로 만들어 후속 수동 시작까지 유닛을 유지하고,
시험 종료 시 nonce 두 유닛을 명시적으로 정리하도록 보완했다. 운영 timer 바이트는 바꾸지 않았다.
`LoadState=not-found`를 정상 유휴 검사로 받아들이지 않고, 각 단계의 결과·fragment를 즉시 보고서에 보존한다.

첫 시험의 운영 전후 동일·cleanup 오류 0·두 유닛 not-found·시험 경로 부재를 확인했다.
[v1 입력](preflight-v1/preflight-inputs.json)과 실패 원본은 보존했고,
[보완 해시·범위](preflight-repair.json) 및 기존 12개+추가 3개 검사를 새 입력에 연결했다.
후보 runtime·worker·배포 유닛·scope·시각 정책은 그대로다.

## 실행한 재점검 명령

```sh
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-recurring-candidate-2026-10-18/system_preflight.py --run
```

[도구](system_preflight.py), [유닛 내부 실행](trial_worker.py),
[고정 입력/운영 설정 해시, revision 2](preflight-inputs.json), [검사](preflight-tests.xml).

## 시험 범위

1. 입력·소스·환경과 현재 운영 설정이 검증본과 같은지 확인한다.
2. nonce가 붙은 `/opt/md-t05-recurring-*`, `/var/lib/md-t05-recurring-*`와
   전용 transient system service/timer 두 개만 만든다.
3. 자동 service의 UID·보호 옵션을 유지하고 쓰기는 시험 상태 경로에만 허용한다.
   실제 운영 설정·시각 provider는 읽기만 한다.
4. 실제 timer의 약 35초 뒤 시험 예약과 이후 수동 시작을 별개의 invocation으로 실행한다.
   시험 종료 전까지 다음 날의 예약이 남아 유닛이 유지되며, 시험 완료 때 즉시 정리한다.
   timer 출처 인정/수동 시작 구분, 다른 unit/timer 거부, root 소유 fragment와 canonical 비교를 확인한다.
5. 최종 Python·진입점/import, 실제 읽기 전용 Chrony 관측·guard와 시험 보고서 쓰기를 확인한다.
   시험용 승인도 계속 비활성으로 두고 실제 `execute(run)`이 `authorization_pending`으로
   보류하는 것을 확인한다. 삭제 실행 권한을 주지 않는다.
6. 운영 설정·네 mask·Chrony/두 supervisor 신원을 다시 대조한다.
   시험 journal을 보고서에 남기고 nonce 유닛의 종료·부재를 확인한 뒤 정확한 시험 경로만 제거한다.
   정리에 실패하면 그 사실과 남은 경로를 보존한다.

이 도구는 운영 서비스 재시작·timer 변경·daemon-reload·근거 등록·backup·checkpoint 삭제를 하지 않는다.
timer 테스트의 시각은 시험용이며 15:40 운영 예약을 바꾸지 않는다.
실제 15:40 자격과 승인된 작은 저장소의 자동 처리·잠금 보류 후 새 계획 생성은 격리 검사로 확인했다.
이 root 시험의 비활성 승인 경로를 실제 자동 삭제 검증이라고 세지 않는다.

## 결과 처리

성공 또는 실패의 원본은 `system-preflight-result-<nonce>.json`으로 남긴다.
`passed`, `operating_unchanged`, cleanup의 유닛·경로 부재를 각각 확인한다.
시각 조건이나 운영 입력이 달라져 실패하면 검사를 무시하지 않고 해당 차이를 대조한다.
운영 설치/활성화 전에 최종 `/opt` 경로·실제 파일/권한·scope 일치는 별도로 확인해야 한다.
