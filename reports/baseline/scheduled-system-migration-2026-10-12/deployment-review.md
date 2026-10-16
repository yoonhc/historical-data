# 적용 범위와 원복 검토

상태: **후보·격리 검증 완료, 실제 설치/인계 실행기·root 사전 점검·운영 적용 미완료**.
이 문서는 운영 명령 실행 지시가 아니다. 승인 template은 `enabled=false`다.

## 고정 대상

```text
release: scheduled-manager-20261012-255d64b78ffa8328
plan: 70c1723f25b5eff078b483c1c2ff848a8eba7d6d6b40c0d930ca44d2c829217c
captured boot: eb163bf2-6642-4336-b4d6-6cd58771b13d
```

[plan.json](plan.json)에 각 파일/링크의 before/after를 포함한다.
새 소스 41개와 환경을 `/opt/marketdata/releases/<release>`에 설치하는 작업은 아직 실행하지 않았다.
아래 46개 구성 작업 및 root ledger transaction과 실제 timer 제어를 함께 검토해야 한다.

| 구성 대상 | 수 | 변경 |
|---|---:|---|
| `/etc/systemd/system/marketdata-{8개 업무}.{service,timer}` | 16 | 새 system manager 유닛; 업무는 User=yoonhc |
| `/etc/systemd/system/marketdata-clock-ready.service` | 1 | 같은 strict wait 명령, system manager 의존성 |
| `/etc/systemd/system/marketdata-clock-bootstrap.service` | 1 | 기존 보호 옵션/순서, 새 inventory adapter 경로 |
| `~/.config/systemd/user/marketdata-{8개 업무}.{service,timer}` | 16 | 기존 node 보존 후 `/dev/null` persistent mask |
| 기존 user `timers.target.wants` 링크 | 8 | 원래 target을 보존하고 제거 |
| `/etc/marketdata/scheduled-system/{topology.json,oi-live.toml}` | 2 | 새 inventory와 기존 OI 설정의 고정 사본 |
| `/etc/marketdata/clock-bootstrap.json` | 1 | user masked 목록·새 system guard 해시 |
| T05 `clock-binding.json` | 1 | 새 bootstrap 파일 해시에 연결 |

new system timer의 enable 링크·manager daemon-reload·정상 종료/재개는 아직 실제 실행기로 묶지 않았다.
이 46개 파일 모델을 모든 운영 조작의 최종 승인 목록으로 취급하지 않는다.
운영 준비 때 root/user 소유권, 권한, 원래 symlink와 timer 활성 상태를 추가로 고정해야 한다.

전환 근거는 `/var/lib/marketdata-clock/scheduled-transitions/<plan>/` 아래
원래 ledger·plan·승인·진행 단계·apply/rollback receipt를 보존하는 설계다.
`boot-state.json`은 전환 중 pending envelope를 기록하며 완료 시 기존 boot/closed/step/관측을 보존하고
profile 해시와 configuration_history만 갱신한다. 삭제/재등록/보정 권한 재개 경로는 없다.

## 기존 정책과 실행본

OI 3종·pack·network는 동결한 기존 source를 사용한다. T13 daily는 `703fb0d455523c1b`,
T05 backup/restic은 `c9d413cfe11ec0e7`를 그대로 사용한다. `%h`를 `/home/yoonhc`로 명시하고,
암호 파일 내용은 후보에 복사하지 않는다. 환경/설정은 경로와 digest로 확인한다.

8개 strict waiter/ExecStartPre, UTC calendar, Persistent/OnActive, 실패 재시작/제한,
기존 source/데이터 root·범위·잠금·검증·완료·냉각·작업 예산을 유지한다.
생성/검증 뒤 실제 T05 완료·순서 근거를 남기는 경로도 유지한다.
업무별 시각 판단·status 최신성 의미·600초 제한·Chrony polling은 바꾸지 않는다.

두 collector/Chrony의 명령·profile·unit·현재 프로세스, RTC/Windows/재부팅,
공개 HEAD 관측기 비활성, 기존 사용자/시스템 자동 정리 service/timer mask는 유지 대상이다.
이번 후보의 적용을 위해 두 수집기를 정상 종료하거나 재배포할 필요가 있다는 전제를 두지 않는다.
기존 실행 중 수집의 monitoring은 기존 runtime profile을 유지한다. 전환 도중 supervisor 자체가
재시작되는 예외에는 pending/프로필 불일치로 새 시작을 보류한다.

## T05 연결

binding 변경 후에도 같은 registry·baseline·backup ID·manifest·완료 상한·생성 순서를 사용한다.
이전 사후 관측을 새 시각으로 덮어쓰거나 후행 사본 수를 초기화하지 않는다.
부모 T05 module/Python은 그대로이며 restic adapter의 manager 출처는 별도 필드다.

binding은 T05 삭제 scope 입력이므로 이전의 disabled manual/recurring scope와 달라질 수 있다.
기존 grant는 그대로 보존한다. 향후 T05 preview/삭제 승인은 새로운 연결 상태의 scope를 명시해야 하며,
기존 grant를 덮어쓰거나 자동 활성화해 통과시키지 않는다. 이 이전이 T05 삭제 승인이 되는 경로는 없다.

## 실제 인계에서 확인할 순서

1. 별도 승인한 root 사전 점검에서 실제 최종 환경·일반 UID·경로·잠금·보호 옵션·system bus timer 출처를 확인한다.
   시험용 유닛과 경로만 사용하며 운영 자료·타이머·시각은 유지한다.
2. 그 결과를 포함한 운영 설치/인계 실행기를 준비하고 실제 계획의 boot·profile·guard·ledger·소유권·mask를 재고정한다.
   기존 계획과 달라진 항목을 보고하고 운영 적용 범위의 승인을 받는다.
3. 승인한 8개 user timer의 새 요청을 보류한다. 양 manager의 실제 실행·시작/재시작 대기 job을 구분한다.
   실행 중인 backup/restic/pack은 강제 종료하지 않고 정상 종료를 기다린다.
   아직 본 업무를 시작하지 않은 waiter 뒤 job은 기록하고 별도 취소/재평가 대상으로 처리한다.
   timer를 멈췄다는 사실만으로 서비스의 Restart 예약까지 소멸했다고 가정하지 않는다.
4. 전환 잠금과 명시적 pending 기록 아래 유닛·프로필·T05 binding을 교체한다.
   두 manager를 reload하고 old user persistent mask·새 system 전체 Exec*·보호 설정·출처를 확인한다.
   실제 pending job이 남거나 새 업무가 시작됐다면 commit하지 않는다.
5. 구성 일치와 현재 strict 준비를 각각 기록한다. closed/step/과거 증거를 보존한 receipt를 확정한다.
   현재 준비가 미통과면 gate 대기는 계속한다. profile 적용 성공을 현재 시각 준비로 바꾸지 않는다.
6. 승인한 시스템 timer만 활성화한다. OnActive/Persistent의 실제 새 invocation과 기존 원장·사본의 처리 대상을 대조한다.
   각 서비스가 active였다는 사실만으로 자료 완료·신규 checkpoint·예약 성공으로 세지 않는다.

실제 인계에는 별도 실행기와 live host readback이 필요하다. 현재 `transaction.py`는
`.migration-fixture`가 있는 격리 root에서만 쓰고 `/`는 거부한다. 서비스 제어 명령도 실행하지 않는다.

## 중단·원복

파일은 각 단계에서 승인된 before 또는 after node여야 한다. 알 수 없는 파일/ledger 변경이나 boot 변경은 보류한다.
기록이 남은 같은 방향 재개는 해당 단계부터 진행한다. 운영 clock ledger를 삭제하거나 다시 enroll하지 않는다.
새 boot에서 기존 같은-boot 계획을 자동 실행하지 않고 실제 상태에 맞는 복구 범위를 다시 검토한다.

원복은 새 system timer의 신규 요청을 막고 실행 중인 업무가 끝난 뒤,
기존 user base symlink/file·wants 링크·bootstrap profile/unit·T05 binding을 기록대로 복원한다.
실효 상태 확인·원복 receipt 후 원래 user timer 활성 상태로 돌아간다.
새 system timer를 비활성화하지 않고 user timer부터 다시 켜지 않는다.

새 자료·daily 작업/시도·pack·checkpoint·restic snapshot·receipt·T05 registry는 유지한다.
운영 DB를 이전 사본으로 덮어쓰지 않는다. 새로운 구성에 대한 이력도 원복 과정에 보존한다.
원복 후에도 기존 strict gate가 적용되며 reference age 대기 문제는 그대로 남는다.

## 운영 완료 조건

실제 이전과 원복 준비, 사용자/시스템 중복 부재, 각 업무의 기존 의미로 실행·저장 확인,
시스템 timer 출처의 restic 기록, 다음 정기 backup의 T05 완료/순서 연속성을 확인해야 한다.
기존 T13 완료 범위·이전 대규모 복원 시험을 다시 열 필요는 없다.
이번 후보 검증을 첫 운영 실행이나 업무별 시각 정책 분리 완료로 세지 않는다.
