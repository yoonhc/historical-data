# T05 비활성 recurring scope 한 파일 갱신·원복

**준비·검증 완료, 운영 설치 전.** 사용자가 요청한 1번은 갱신안과 설치·원복 준비다.
아래 운영 쓰기는 구체적인 한 계획을 별도 확인한 뒤 실행한다.
이 문서와 명령 자체는 checkpoint 삭제나 timer 활성화 권한이 아니다.

## 변경과 고정 입력

- 계획 ID: `82d0f54af28cf5190020c7cc57763a401e24dac417c248d37fa47afc9527ec5d`
- 도구 [refresh_scope.py](refresh_scope.py) SHA-256: `d915c86557143b003c0a3b644ce84c9f435f63579282fe3ee03a486817142367`
- 대상: `/etc/marketdata/checkpoint-clock-retirement/recurring.json`, root:root·0644·일반 파일.
- before SHA-256: `946c2c35aa5476f3de2780e113eee2131fe0a52ebbf3409dcaedf79cf3c4560e`
- after SHA-256: `14523bb85f8dcdbe3cd4fa40d21e3c11ca8a7bae181071464c68579d4a24fc65`
- bootstrap 프로필 해시: `343c057c01d70bc3ccaab4eb1253ee335a89da00b8c97fba8ad8808d13d71fcb` → `fb7a4a29c0145db1bf4248819479ad4b8b6a0d22ba8e28f23f9f3199ff25bc96`.
- 연결된 최종 T16 구성 계획: `78d7c634bced73af83e765e8f0c9e2a1a4b2029d5d0f0e3375e98e654f4dbd77`.
- 부팅: `eb163bf2-6642-4336-b4d6-6cd58771b13d`.

[plan.json](plan.json)은 내용·도구·91개 보호 입력의 해시/권한을 묶는다.
대상 외에는 `/var/lib/marketdata-clock/t05-disabled-scope-updates/<plan_id>/`의 설치 감사 기록만 새로 쓴다.
`manual.json`, 정책, binding, 유닛, bootstrap/ledger, 수집기, T16 실행본, 데이터와 기존 근거는 갱신 대상이 아니다.
서비스 제어·daemon-reload·timer 시작·backup 실행·정식 preview·삭제 명령을 호출하지 않는다.

## 읽기 전용 직전 확인

실제 호스트에서 UID 1000 또는 root로 실행한다. 기본 동작도 check다.

```sh
/usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-disabled-scope-2026-10-17/refresh_scope.py --action check
```

1. 계획·실행 도구·before/after 해시와 `enabled=false`·승인 필드 null을 확인한다.
2. 기존 coordinator **공유 잠금**과 설정 디렉터리의 이 도구 전용 배타 잠금을 즉시 획득한다.
   busy이면 기다리며 업무를 막지 않고 종료한다. backup 완료를 기다리는 단계나 coordinator 배타 잠금은 없다.
   T05 자체 실행기의 runner 잠금을 대신한다고 주장하지 않는다. grant 읽기는 한 번의 원자 교체 전후 완전한 파일을 본다.
3. 같은 boot·최종 T16 구성/ledger/receipt·현재 T05 설치본에서 새로 계산한 scope를 대조한다.
   보호 입력이 바뀌면 현재 계획으로 덮어쓰지 않는다. 기존 근거 13개는 유지하고 새 불변 객체 추가는 허용한다.
4. 네 mask, manual 비활성, 정리 service/timer/preview의 유휴·job 없음,
   Chrony/두 supervisor 실행 상태와 현재 grant가 정확한 before/after 중 하나인지 확인한다.

check의 `passed=true`는 **이 갱신 계획의 적용 전제**를 통과한 것이다.
현재 시각 품질·당일 backup·restic·유지 사본 무결성을 판정한 결과나 삭제 가능 표시는 아니다.

## 별도 확인 후 설치

```sh
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-disabled-scope-2026-10-17/refresh_scope.py --action apply --approve-plan 82d0f54af28cf5190020c7cc57763a401e24dac417c248d37fa47afc9527ec5d
```

직전 조건을 다시 확인하고 원본·제안·계획·도구·시도 intent를 root 감사 경로에 저장/동기화한다.
같은 설정 디렉터리에 root:root·0644 임시 파일을 쓰고 fsync한 뒤, 보호 입력과 대상 before를 재확인한다.
정확한 한 파일을 atomic rename하고 디렉터리를 동기화한다. 재조회에서 after·권한·비활성·보호 입력과
Chrony/두 supervisor PID·invocation 유지 여부를 확인하며 시도별 result를 남긴다.
과거 원본과 실패 기록은 덮어쓰지 않는다. 같은 계획의 이미 설치된 after를 확인하면 재교체하지 않는다.

## 중단과 원복

중단 후 먼저 check로 현재 before/after와 root의 intent/result를 대조한다.
원본 보존 중 중단되어도 정확히 같은 누락 감사 파일만 보완하고, 이미 저장된 파일의 내용이 다르면 거부한다.
rename 전이면 before, rename 후이면 after를 기준으로 같은 계획을 재개할 수 있다.
예상 밖 파일·권한·구성·boot 변경이나 승인 활성화가 있으면 재실행으로 덮어쓰지 않는다.
SIGKILL로 임시 파일이 남을 수 있다. 그 파일을 설치 결과로 간주하지 않으며, 일괄 삭제하지 않는다.

원복은 scope 문서만 원본 바이트와 권한으로 되돌린다. 최종 T16 구성은 유지하므로 원복 후에는 다시
`authorization_scope_changed`로 preview가 보류될 수 있다. 데이터·baseline·완료 상한·순서는 그대로다.

```sh
sudo /usr/bin/python3 -B -I /var/lib/marketdata-clock/t05-disabled-scope-updates/82d0f54af28cf5190020c7cc57763a401e24dac417c248d37fa47afc9527ec5d/refresh_scope.py --action rollback --approve-plan 82d0f54af28cf5190020c7cc57763a401e24dac417c248d37fa47afc9527ec5d
```

원복도 같은 현재 구성과 보존된 설치 기록을 확인한다. 이미 before면 재교체하지 않고 그 상태를 기록한다.
이후 T16 구성을 별도로 바꿨다면 이 계획의 원복 조건도 달라지므로 새 대조가 필요하다.

## 설치 이후 남는 것

비활성 scope 갱신은 삭제 권한을 만들지 않는다.
현행 당일 backup 및 같은 backup ID의 restic 저장·전체 검사 후 정식 미리보기,
정확한 한 계획의 수동 삭제 승인·실제 삭제/보존 결과 확인, 반복 자동화 승인은 계속 별개다.
이번에는 현재의 같은 날짜 조건, U ≤ 5초, 보수적 72시간+60초, 후행 검증 사본 3개와 잠금 조건을 유지한다.
