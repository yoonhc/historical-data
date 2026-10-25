# T05 mount 경계 수정과 기존 중단 계획의 재개 검토

**최신 결과: 10/17 20:32 재개 operation `821367d2…`의 첫 수동 정리 10개 완료·독립 대조 통과.**
[완료 기록](completion.md). 반복 자동화는 아직 승인/활성화하지 않았으며 네 mask를 유지한다.
아래는 이미 적용한 mount 전환의 검토 이력이다.

**10/17 18:47 구성 적용 완료·정리 실패:** 승인된 네 파일은 after이며, 이어진 run `2d9f7b8b…`는
reload 뒤 restic 예약과 repository lock이 경합해 삭제 0개로 끝났다. 원래 journal·사본은 그대로다.
아래는 이미 실행한 설치·scope 전환의 검토 기록이며 같은 apply를 반복하지 않는다.
현재 [실제 결과와 재설치 없는 재개안](status.md), [새 재개 계약](resume-authorization.md)을 따른다.
당시 미완료였던 첫 수동 정리는 위 후속 결과로 완료했고, 반복 자동화·T05 전체는 남아 있다.

고정 operation plan: `eced87a499269ebe5da84be1b0795ebfd2c109ce06f37db4e5e26f8027b9e231`.
도구 SHA-256: `f079147fae7d13251855684e98ad4a0ec8eba27bc0da127d08c251b667515133`.

## 원인과 실제 관리자 시험

`ProtectSystem=strict` 아래에서 원본과 staging을 각각 `ReadWritePaths`로 열면 별도 bind mount가 된다.
같은 device 번호라도 `rename()`은 이 경계를 넘지 못한다. 17:44:28 KST 실제 UID 1000 시스템 유닛 두 개에서
기존 경계의 errno 18과 공통 부모 경계의 성공을 확인했다. 두 경우 모두 경계 밖 쓰기는 errno 30으로 거부됐다.
시험 경로·두 유닛은 제거됐고 운영 입력은 동일했다.
[원본 systemd 결과](../t05-first-manual-exdev-2026-10-17/system-rename-result-6b0b3591a09c42dc.json).

## 정확한 변경과 승인 범위

| 경로 | 변경 |
|---|---|
| `/etc/marketdata/checkpoint-clock-retirement/units/marketdata-checkpoint-retirement-apply@.service` | 수동 service의 원본/staging 두 쓰기 경계를 공통 부모 하나로 변경 |
| `/etc/marketdata/checkpoint-clock-retirement/units/marketdata-checkpoint-retirement.service` | 비활성 자동 service에도 같은 경계 수정 |
| `/etc/systemd/system/marketdata-checkpoint-retirement-apply@.service` | canonical 수동 service와 동일한 root:root/0644 일반 파일 |
| `/etc/marketdata/checkpoint-clock-retirement/recurring.json` | 두 unit 해시와 파생 scope만 갱신, 비활성·승인 null 유지 |

```diff
-ReadWritePaths=/mnt/backup/marketdata-historical/automatic /mnt/backup/marketdata-historical/.automatic.retiring
+ReadWritePaths=/mnt/backup/marketdata-historical
```

공통 부모 자체의 쓰기 권한도 추가된다. 현재 부모에는 `automatic`, `.automatic.retiring`만 있고
실행 직전에 이를 다시 확인한다. 다른 서비스 보호 옵션, UID, /opt 코드, 시각·보관 조건은 변경하지 않는다.
자동 service/timer의 system/user 네 mask도 유지한다. `daemon-reload`는 변경한 수동 유닛을 읽기 위해 포함한다.

before scope는 `da7b03dba075a816d1c8277298dd123ce1db62e54aa9a85920711a18913a6c22`,
after scope는 `84a0b92ae6f8bf0e38ac10ecb4a2615a32af72aebf1c8c28d591c02087cf4983`이다.
전체 before/after 바이트·소유권·해시는 [plan.json](plan.json),
상세 승인 계약은 [authorization.md](authorization.md)에 고정한다.

삭제 대상은 기존 계획 `31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08` 그대로다.
10/06–14의 **10개 후보**, 10/15–17 **유지 3개**, 등록 이전 **제외 3개**이며
정확한 경로·backup ID·manifest·tree hash는 [원래 정식 계획](../t05-formal-preview-2026-10-17/plan.json)을 따른다.
예상 회수량은 약 28.20GB이며 실측 결과는 실제 적용 뒤 확인한다.

## 원래 scope와 새 scope의 연결

설치된 일반 `manual_proposal()`는 현재 scope와 과거 계획 scope가 다르면 **계속 거부한다**.
이번 도구는 그 검사를 고치지 않는다. 원래 plan의 scope나 journal을 새 값으로 덮어쓰지도 않는다.

별도 명시적 승인 경로가 다음을 모두 확인한다.

1. 원래 계획의 ID·바이트 해시·code/policy/binding과 정확한 unit 두 개의 한 줄 차이.
2. 앞선 세 run의 원본 해시와 EXDEV run `5d7deaf59b1f4c99847a732327ce77af`.
3. journal의 기존 실패 1개·대상 10개 prepared·unlink 0, 원래 경로와 비어 있는 staging.
4. 기존 inventory 10개 해시, 원래 사본 16개 manifest, 기존 근거/등록 15개 보존.
5. 현재 boot·보호 입력 88개·설치 구성·권한·manual 비활성·네 mask·Chrony/두 supervisor 신원.

그 뒤 **새 operation plan + 원래 삭제 plan + 검토한 EXDEV run**을 함께 승인한 경우에만
새 scope의 해당 한 계획 manual grant를 만든다. grant에 `scope_transition`으로 승인 경계를 기록한다.
설치 엔진의 `authorize()`와 중단 재개 경로를 사용하며 일반 제안 검사를 통과했다고 표시하지 않는다.
다른 계획/대상/코드/정책/binding 변경이나 추가 시도가 발견되면 보류한다.

## 적용·중단·원복

네 파일 전환과 manager reload 뒤 UID 1000의 실제 설치 guard를 확인한다.
정상 pack/backup/restic 업무가 진행 중이면 강제 종료하지 않는다. reload 이후에는 최대 600초 정상 종료를 기다리고
잠금이 계속 경합하면 보류한다. 그 뒤 정확한 manual grant를 임시 활성화하고 설치 시스템 서비스를 한 번 요청한다.

coordinator SH와 설정 directory flock은 전환을 보호한다. 서비스 종료 대기 중에는 runner/maintenance/archive의
배타 잠금을 점유하지 않는다. 실제 엔진은 기존 U ≤ 5초, 보수적 72시간+60초, 후행 검증 사본 3개,
당일 backup/restic 저장·전체 검사, 유지 사본 무결성·원래 후보 inventory·각 잠금과 삭제 직전 새 시각 관측을 적용한다.
앞선 검사에 약 24분이 걸렸으므로 이번 검사가 즉시 끝난다고 약속하지 않는다.

시작 요청 전 `dispatch_intent`를 내구적으로 기록한다. 이후 같은 apply 명령은 추가 시작을 거부한다.
전환 전후 네 파일의 부분 변경만 발생했다면 알려진 before/after 조합에 한해 재개·원복할 수 있다.
grant 활성화 뒤 intent 이전 중단은 새 run이 없고 원본 journal이 동일함을 확인해 비활성으로 회수하며 재검토로 남긴다.
intent 이후 중단은 `finalize`로 기존 실행만 관찰한다. 실행 중이면 grant를 바꾸거나 서비스를 죽이지 않는다.

정상/실패 종료 후 원래 비활성 manual을 복원한다. 다른 grant가 보이면 덮어쓰지 않는다.
서비스가 또 실패하거나 일부 진행했다면 새로운 실행을 자동 요청하지 않고 해당 run/journal을 검토한다.
`rollback`은 유휴·manual 비활성 아래 네 구성 파일과 manager reload만 되돌린다.
자료·journal·inventory·새 run·기존 근거·삭제 결과는 보존하며 삭제를 원상복구한다고 표시하지 않는다.

root 감사 위치는 `/var/lib/marketdata-clock/t05-mount-repairs/<operation_plan_id>/`다.
plan·도구·승인 문서·정확한 enabled grant·상태·최종 결과를 보존한다.
실제 명령과 최종 고정 ID는 [commands.md](commands.md)를 따른다.

## 검증과 남은 범위

**27개 통과·실패/skip 0개, 89.1초.** [검증 결과](validation.json), [개별 검사](tests.txt), [코드](repair_and_resume.py).
실제 Python 3.12 설치본과 작은 독립 restic 저장소에서 첫 EXDEV 후 같은 journal의 재개·삭제,
원래 계획/실패/inventory·유지 사본·restic·근거 보존을 확인한다.
Chrony/manager/소유권 provider는 해당 격리 연결 시험의 입력 대역이며 실제 운영 실행을 대신하지 않는다.
실제 systemd mount 동작은 위 별도 관리자 시험 결과다.

고정 계획의 실제 호스트 [읽기 대조](preapply-check.json)도 통과했다. 네 파일 모두 before이며
manual/recurring 비활성·네 mask·boot/구성/원래 기록·시각 준비·유휴/잠금·보호 프로세스를 확인했다.
이는 해당 시점의 판단이며 apply에서 다시 확인한다. 실제 root 수정·서비스 재개 성공으로 세지 않는다.

운영 서비스 시작·삭제·반복 자동화는 아직 실행하지 않았다. 읽기 전용 현재 대조와 적용 직전 재검증을 구분한다.
수동 성공 뒤에도 자동 유닛의 root 일반 파일 설치와 새 반복 승인·활성화는 별도다.
현재 고정 실행본의 renderer는 옛 두 경계를 출력하므로, 향후 유닛 재생성/새 배포 때 이번 수정의 회귀 검사를
renderer에 반영해야 한다. 기존 /opt 코드를 지금 수정하지 않으며 이 후속을 TODO에 남긴다.
