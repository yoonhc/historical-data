# T05 첫 수동 정리 보류 — 유닛 설치 형식 보완 준비

**후속 실제 결과:** 10/17 16:36:58 유닛 보완은 root 일반 파일/0644·동일 바이트와 UID 1000 실제 guard까지
통과했다. 이어진 run `d1c40fd5…`는 16:37:19 repository 잠금 경합으로 다시 exit 75·삭제 0개로 보류됐다.
manual 비활성 복원과 보호 입력/프로세스 유지는 확인됐다. 유닛 보완은 재실행하지 않는다.
[두 번째 보류 원인·검토된 재개 제안](../t05-first-manual-resume-2026-10-17/status.md)을 따른다.
아래는 첫 실패와 보완을 준비하던 당시 기록이다.

**2026-10-17 16:15:25 KST**, 첫 수동 run `4a61c943702c41bcb4b5e854ac098440`은
`deferred`, exit **75**, 삭제 **0개**로 끝났다. 수동 승인 파일은 원래 비활성으로 복원됐고,
후보 10개·보호 3개·제외 3개, 기존 근거와 정리 네 mask·Chrony/두 supervisor가 유지됐다.
시각 조회·보존 조건 계산·유지 사본 전체 검사·삭제 journal 생성 전에 중단됐다.
**첫 수동 정리와 T05 전체는 미완료다.**

[원래 root 결과](original-attempt/result.json), [실제 run](original-attempt/run.json),
[journal](failure-journal.jsonl), [호스트 직접 대조](failure-review.json)를 보존한다.
현재의 보완은 준비·검증 단계이며 아직 운영 유닛을 교체하거나 다시 시작하지 않았다.

## 원인과 검증 한계

정확한 거부 사유는 다음이다.

```text
ClockHold: Untrusted clock/approval path:
/etc/systemd/system/marketdata-checkpoint-retirement-apply@.service
```

10/11 설치기는 이 경로를 `/etc/marketdata/checkpoint-clock-retirement/units/`의 승인 유닛으로 연결하는
**심볼릭 링크**로 설치했다. 그러나 실제 apply/run의 `installed_service()`는 `clock.trusted(FragmentPath)`를 호출하고,
이 함수는 경로 자체와 부모의 링크·root 이외 소유·그룹/기타 쓰기를 거부한다.
원본은 root:root/0644이며 바이트도 일치하지만 **링크라는 형태가 설치 검사 계약과 맞지 않았다.**

미리보기는 이 실제 삭제용 service 검사를 실행하지 않는다. 기존 격리 업무 시험에서는 해당 검사를 대역으로 치환했고,
첫 수동 실행 도구도 링크를 resolve해 내용만 확인했다. 그러므로 앞선 preview·도구 7개 통과를 실제 apply 진입까지
검증한 것으로 해석하면 안 된다. 이번 보류는 시각 품질·reference age·당일 백업 부족 때문이 아니다.
`ClockHold`라는 예외 이름보다 위 상세 사유를 기준으로 판단한다.

저장 run의 실제 service context는 UID 1000·PID `2512522`·invocation `97e2bdee62b94928bbbfdabafececa48`과
수동 start job을 확인했다. system manager journal에는 같은 유닛의 시작·정상 종료가 기록돼 있다.
유닛은 `SuccessExitStatus=75`를 가지므로 systemd의 `Result=success`가 정리 완료를 뜻하지 않는다.
종료 후 unit 조회에서 사라진 실행 정보나 `ExecMainStatus=0` 대신 실제 run의 exit 75를 사용했다.

## 좁은 보완 범위

| 항목 | 변경 |
|---|---|
| 운영 대상 한 경로 | `/etc/systemd/system/marketdata-checkpoint-retirement-apply@.service` |
| Before | root 소유의 승인 원본을 가리키는 심볼릭 링크 |
| After | 동일한 **1,797바이트**, root:root/0644 일반 파일 |
| 유닛 내용 SHA-256 | `9932a3c4a8bc5acc74d5ba9c98d6348e2e367524999e15181b246fd0ac3addd0` 유지 |
| 수동 정리 계획 | `31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08` 유지 |
| Scope | `da7b03dba075a816d1c8277298dd123ce1db62e54aa9a85920711a18913a6c22` 유지 |

운영 정리 엔진·가상환경·명령/보호 옵션·시각/보존 정책·canonical 유닛 내용은 바꾸지 않는다.
scope는 canonical 유닛의 바이트를 고정하므로 이 한 경로의 설치 형식 수정은 기존 scope나 계획의 내용을 바꾸지 않는다.
다른 91개 보호 입력·기존 baseline/완료 상한/순서·등록·recurring·네 mask도 유지한다.
기존 plan을 다시 작성하거나 새 계획을 자동 승인하지 않는다.

[repair_and_apply.py](repair_and_apply.py)는 정확한 before 링크를 원자적으로 같은 내용의 일반 파일로 교체한 뒤
system manager daemon-reload를 수행한다. Chrony·수집기나 예약 업무를 중지/재시작하지 않는다.
**수정된 실제 FragmentPath에서 UID 1000으로 설치본의 `installed_service()` 검사를 먼저 통과해야**
수동 승인과 업무 시작으로 이어진다. 이 단계가 실패하면 원래 링크로 복원하고 업무를 시작하지 않는다.

[수동 실행 보완](manual_apply.py)은 이전 파일과 별도로 고정했다. 종전 도구·입력·root receipt는 보존했다.
예외 허용은 원래 거부의 **정확한 ID/해시·사유·selection/deletion 미시작·삭제 0·journal 없음**뿐이다.
다른 과거 실행이나 journal이 있으면 거부한다. `resume_interrupted=false`와 기존 모든 적용 검사를 유지한다.
부분 삭제 후 재개 권한을 새로 만들거나 실패 이력을 지우는 변경이 아니다.

## 검증 완료·실제 적용에 남은 것

- **격리 12개 통과:** 기존 승인/종료 검사 7개 재실행과 추가 5개다.
  동일 바이트 일반 파일 교체/원복, 입력 변화 거부, 교체 전 재검사 실패 시 링크 보존,
  rename 뒤 오류의 원래 링크 복원, 이번 정확한 삭제 전 거부만 허용하는 조건을 확인했다.
- **설치본 guard 5개 대조:** 실제 호스트의 소유권·파일과 설치 함수를 사용했다.
  원래 링크 거부, 동일 바이트 trusted 일반 파일 허용, drop-in·reload 필요·다른 유닛 내용 거부를 확인했다.
  systemctl 응답을 해당 경우로 대체한 읽기 전용 시험이며 **운영 파일을 이미 교체한 시험은 아니다.**
  [정확한 사례·한계](installed-guard-tests.json).
- **실제 호스트 읽기 전용 preflight 통과:** 현재 before 링크·boot·구성·같은 scope/plan·기존 실패와
  유휴/잠금·비활성 승인·프로세스/근거 유지. 운영 파일/유닛 쓰기·재기동은 하지 않았다.
- 실제 교체 후 실효 유닛/guard 통과, 유지 사본 세 개 전체 무결성·후보 재대조·삭제 직전 시각 검사와
  첫 실제 삭제/보존 결과는 아직 남아 있다. 기존 core 검증이나 preview를 그 성공으로 대신하지 않는다.

[검증 기록](validation.json)에 도구·입력 해시와 각 검사 범위를 연결했다.
도구 세션에서 `sudo -n`으로 아래 적용 명령을 실행하려 했지만 `sudo: a password is required`로
exit 1이 반환됐다. root 보완 도구는 실행되지 않았고 유닛 교체·승인 변경·서비스 시작·삭제도 없었다.
사용자 터미널에서 sudo 인증을 포함한 실행이 필요하다.

감사 중 일반 Python에서 설치 모듈을 읽으려다 pyarrow 부재로 조회가 중단된 기록도 남겼다.
고정 release 가상환경에서 읽기 대조를 다시 수행했고, 미리 복사된 원래 기록의 바이트도 모두 재대조했다.
이는 운영 정리기의 별도 결함이나 재실행이 아니다.

## 관리자 실행과 복구

첫 수동 정리 승인은 유지하며, 아래 명령은 **유닛 한 개의 설치 형식 보완과 같은 계획의 재시도**를 포함한다.
[명확한 범위](authorization.md)와 [고정 파일·해시](inputs.json)를 따른다.

```sh
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-first-manual-recovery-2026-10-17/repair_and_apply.py --action apply --approve-plan 31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08
```

설치 보완 root receipt는 `/var/lib/marketdata-clock/t05-manual-unit-repairs/<attempt>/`에,
수동 정리 root receipt는 기존 `t05-manual-applications/<Plan ID>/<attempt>/`에 별도로 남긴다.
보완 receipt의 `passed=true`는 유닛 설치 보완 성공이며 실제 삭제 완료와 구분한다.

업무 시작 전 보완 실패는 원래 링크로 자동 복원한다. 보완 뒤 업무를 시작하지 않은 상태에서 원복이 필요하면,
같은 도구의 `--action rollback --approve-plan <Plan ID>`가 정확한 after 파일만 원래 링크로 돌린다.
새 정리 run/journal이 생기면 이 자동 경로는 거부하며 먼저 실제 결과를 점검한다.
실제 업무가 시작된 뒤에는 정상 종료 시 manual을 비활성으로 복원하고 결과를 보존한다.
중간 삭제/실패는 자동 재시도하지 않는다. 반복 자동화는 별도 승인이다.

후속 자동화에서 system 자동 service를 unmask할 때도 같은 `installed_service()` 계약에 맞는 **root 일반 파일**을 사용하고
실제 진입을 확인해야 한다. 이번 보완을 이유로 자동 service/timer의 mask를 해제하거나 먼저 수정하지 않는다.
