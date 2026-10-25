# T05 비활성 scope 갱신 운영 결과

**2026-10-17 14:12:23 KST 승인한 한 파일 갱신 완료, 14:12:44 독립 읽기 대조 통과.**
현재 구성에 맞춘 비활성 `recurring.json` 설치만 완료했다. T05 전체 완료나 삭제 승인이 아니다.
준비 당시의 [summary](summary.md)·[적용 검토안](deployment-review.md)·기존 manifest는 그대로 보존한다.

## 적용 범위와 실제 결과

| 항목 | 확인 결과 |
|---|---|
| 승인 계획 | `82d0f54af28cf5190020c7cc57763a401e24dac417c248d37fa47afc9527ec5d` |
| 운영 변경 대상 | `/etc/marketdata/checkpoint-clock-retirement/recurring.json` 한 파일 |
| 의미상 변경 | `scope.clock_binding.bootstrap_profile_sha256`와 파생 `scope_id` |
| 현재 scope | `da7b03dba075a816d1c8277298dd123ce1db62e54aa9a85920711a18913a6c22` |
| after SHA-256 | `14523bb85f8dcdbe3cd4fa40d21e3c11ca8a7bae181071464c68579d4a24fc65` |
| 실제 파일 권한 | root:root, 0644, 일반 파일, 링크 수 1 |
| 승인 상태 | `enabled=false`, `approved_at=null`, `approval_reference=null` |
| 자동 정리 | user/system service·timer 네 mask 유지 |
| 기존 근거 | baseline·완료 상한·생성 순서 객체 13개와 등록 receipt 1개 해시 보존 |
| 실행 상태 | Chrony와 두 collector supervisor의 PID·invocation·active 상태 유지 |

[사용자 승인 범위](authorization.md)에 따라 [적용 직전](operating-evidence/before.json)에는
현재 boot `eb163bf2-6642-4336-b4d6-6cd58771b13d`, 최종 T16 구성·closed ledger·대상 before,
소유권/모드·보호 입력 91개·네 mask를 다시 대조했다.
고정 도구는 coordinator 공유 잠금과 설정 디렉터리 배타 잠금을 비차단으로 확보한 뒤 동일 조건을 재검증했다.
업무 종료를 기다리거나 시각·서비스를 제어하지 않았다.

도구 세션의 `sudo -n` 시도는 관리자 인증이 없어 설치기 실행 전에 종료됐다.
[인증 대기 기록](operating-evidence/noninteractive-install-attempt.json)을 보존하며,
이후 사용자가 터미널에서 승인한 동일 명령을 실행해 적용했다. 코드·계획·보호 조건을 변경한 재개가 아니다.

[root 적용 결과](operating-evidence/root-apply-b72775c8cb8643b0acc9b30df125108d.result.json)는
`position_before=before`, `position_after=after`, `grant_changed=true`, `passed=true`다.
[독립 after 대조](operating-evidence/after.json)에서도 승인된 after 바이트·권한·비활성 필드,
보호 입력 91개와 기존 registry 파일의 보존, 동일 프로세스 신원을 확인했다.
기존 객체의 변경·삭제를 거부하되 새 객체 추가는 허용하는 비교이며, 이번 직전/직후 사이 추가 객체는 없었다.
기존 baseline이나 사후 상한을 재관측·재등록하거나 보호 시작점을 초기화하지 않았다.

| 보호 프로세스 | 동일 PID | 동일 InvocationID |
|---|---:|---|
| Chrony | 1245 | `19420480ab3249529a075ca7cb2bd3c1` |
| depth supervisor | 224785 | `41b1747221894331a5e0b4fdf3f9751e` |
| aggTrade supervisor | 224784 | `1aa42e55ff804c1f9e5ec68b31116c6b` |

## 감사 기록과 원복

root 원본은 아래 디렉터리에 보존된다.

```text
/var/lib/marketdata-clock/t05-disabled-scope-updates/82d0f54af28cf5190020c7cc57763a401e24dac417c248d37fa47afc9527ec5d/
  plan.json
  refresh_scope.py
  before.json
  after.json
  apply-b72775c8cb8643b0acc9b30df125108d.intent.json
  apply-b72775c8cb8643b0acc9b30df125108d.result.json
```

원본 경로·root 소유권·해시와 보고서 사본의 연결은 [after 대조의 source_copies](operating-evidence/after.json),
이번 보고서·승인·점검 코드·운영 근거의 해시는 [operating-manifest](operating-manifest.json)에 기록한다.
원래 고정 파일 14개의 [evidence-manifest](evidence-manifest.json)는 변경하지 않았다.
원복은 [검토안](deployment-review.md)의 같은 계획·동일 보호 조건 아래 기존 비활성 before 문서로만 되돌리는 범위다.
이번에는 원복하지 않았다. 과거 scope로 되돌리면 범위 불일치가 다시 생길 수 있으며 T16 구성을 원복하는 기능은 아니다.

## 남은 T05 단계

[14:12:44 KST 읽기 전용 관찰](operating-evidence/backup-progress.json)에서
13:10:01에 시작한 maintenance daily `1de6965ae3844c999a672653571a5c8c`는
`state=running`, `phase=validate`, `finished_at=null`이었다. 아직 backup ID와 완료 경로가 없었다.
진행 중 보고서의 `ok=false`를 최종 백업 실패로 판정하지 않는다.
14:05 restic 실행의 `processed=0`과 10/16 14:52:42의 이전 전체 읽기 검사는
10/17 신규 checkpoint의 저장·전체 검사 완료를 대신하지 않는다.

이번 scope 정합성 갱신과 당일 backup/restic 조건은 별개다.
실제 당일 backup 완료와 같은 backup ID의 restic snapshot 저장·전체 검사 근거를 확인한 다음,
별도 실행 범위를 확인해 정식 미리보기 단계로 이어간다.
현재 승인은 정식 미리보기·근거 등록·삭제·timer 활성화·서비스 제어를 포함하지 않으며 이번에도 실행하지 않았다.
당일 조건과 U·72시간+60초·후행 검증 사본 3개·무결성/잠금 정책은 그대로다.

후행 사본이 부족해 새 사본을 하나 더 요구하는 상황은 아니다.
01:19의 시간·후행 조건상 9개는 사전 검토 대상일 뿐 최종 삭제 목록이 아니다.
전체 inventory·유지 사본 무결성을 포함한 새 정식 계획, 적용 직전 재검증,
정확한 한 계획의 수동 삭제 승인·결과 확인과 반복 자동화 별도 승인은 남아 있다.
