# T05 비활성 승인 범위 갱신 준비

**10/17 09:23–09:28 KST 최종 T16 운영 구성 대조·한 파일 갱신 계획·격리 검증을 완료했다. 운영 설치는 하지 않았다.**
이번 작업은 삭제 없는 정식 미리보기의 `authorization_scope_changed`를 해소하기 위한 준비다.
`daily_not_today`를 제거하거나 T05 삭제·반복 자동화를 승인하는 변경은 아니다.

## 고정 범위

| 항목 | 값 |
|---|---|
| 계획 | `82d0f54af28cf5190020c7cc57763a401e24dac417c248d37fa47afc9527ec5d` |
| 갱신 대상 | `/etc/marketdata/checkpoint-clock-retirement/recurring.json` 한 파일 |
| 기존 scope | `529cb1c08746e19c5d1ca8841cb80d8b3439c1040ffcfe021412d3e17b42a53c` |
| 제안 scope | `da7b03dba075a816d1c8277298dd123ce1db62e54aa9a85920711a18913a6c22` |
| 의미상 변경 | `scope.clock_binding.bootstrap_profile_sha256` 한 필드와 그로부터 계산한 `scope_id` |
| 유지되는 권한 | `enabled=false`, `approved_at=null`, `approval_reference=null` |
| manual 문서 | scope 없는 비활성 템플릿 그대로. 이번 갱신 대상 아님 |
| 현재 T16 | `scheduled-clock-policy-20261016-686bb70c9ee4ef11` |
| 현재 T05 | `t05-completion-evidence-20261011-c9d413cfe11ec0e7` 그대로 |

[원본 문서](before.json), [제안 문서](after.json), [정확한 경로·내용·권한·해시를 고정한 계획](plan.json),
[설치·중단·원복 절차](deployment-review.md)를 함께 검토한다. scope를 맞추면서 과거 승인이나 보호 기간을 승계·초기화하지 않는다.

## 실제 호스트에서 대조한 범위

[사전 대조](preparation.json)에서 적용된 T16의 구성 경로 13개와 원본 root receipt를 확인했다.
실제 `boot-state.json`은 그 receipt의 `ledger_after`와 같으며, 같은 boot의 `closed`·준비 이력·최종 프로필 연결을 유지한다.
T16 이후 새벽에 만든 제안만 재사용한 것이 아니라 현재 설치본 `propose`를 UID 1000으로 실행해 scope를 다시 산정했다.

설정·유닛·프로필·실제 소유권/모드·원본 receipt·기존 불변 근거 등 **91개 경로**를 보호 입력으로 고정했다.
기존 baseline·완료/순서 객체 **13개**의 원본 해시를 보존하며, 이후 정기 백업이 새 객체를 추가하는 것은 허용한다.
고정한 기존 객체가 변경·삭제되면 설치를 보류한다. registry 재등록·시각 재관측·자료/원장 원복은 하지 않는다.

`recurring.json`은 현재 bootstrap/runtime 프로필과 T16 binding의 해시 입력으로 참조되지 않는다.
따라서 이번 한 파일 변경은 bootstrap 프로필이나 ledger를 다시 전환하는 작업이 아니다.
사용자·시스템 자동 정리 service/timer 네 mask와 비활성 manual 문서도 보호한다.

## 검증과 한계

- **격리 17개 통과:** 허용된 한 필드 변경, enabled/승인/정책 변경 거부, 기존 파일 변경·symlink 거부,
  원자 교체·바이트/권한 원복, 반복 실행, 교체 전·후 예외와 재개, 기록 변조 거부, 읽기 전용 점검을 확인했다.
  파일은 임시 디렉터리만 변경했다. transaction 검사에서 root 자격·manager 조회는 대역이며 실제 root 설치 시험은 아니다.
  중단은 예외 주입으로 확인했고 실제 SIGKILL 시험으로 세지 않는다. [검증 결과](validation.json), [시험 코드](test_refresh_scope.py).
- **설치된 T05 판정 함수 확인:** 기존 문서는 `authorization_scope_changed`, 임시로 읽은 제안 문서는
  preview 승인 범위 검사 통과·`enabled=false`, recurring 실행은 `authorization_pending`, 기존 manual은 계속 거부됐다.
  실제 설정·코드·scope를 사용하고 정확한 recurring 경로의 파일 읽기만 임시 입력으로 대체했다.
  실제 preview·삭제·timer는 실행하지 않았다. [입력과 판정](authorization-validation.json).
- **독립 CLI 읽기 전용 점검 통과:** 현재 운영 파일은 여전히 before이며 제안과 보호 입력이 일치했다.
  systemd 조회·기존 coordinator 공유 잠금·설정 디렉터리 잠금만 사용했다. 설정 쓰기나 서비스 제어는 없었다.

처음 호스트 대조는 timer에 service 전용 `MainPID`를 요구한 점검 코드 오류로 계획 생성 전에 중단됐다.
service/timer 구분과 두 회귀를 보완한 최종 17개 및 실제 호스트 재대조가 통과했다.
[첫 실패](preparation-failure.json)는 보존한다. 이어진 승인 함수 시험의 첫 입력 대체가 실제 grant 읽기에 연결되지 않아
옛 grant를 그대로 거부한 [시험 연결 오류](authorization-probe-first-attempt.json)도 보존했다.
운영 T05 코드·정책·승인 문서를 바꿔 통과시킨 것은 아니다.

## 다음 단계

비활성 한 파일 갱신은 이 계획의 별도 확인 후 설치한다. 당일 백업 전에도 범위 갱신은 가능하지만,
그것만으로 정식 미리보기의 모든 조건이 충족되는 것은 아니다.
실제 backup과 같은 backup ID의 restic 저장·전체 검사 완료를 확인한 뒤,
전체 inventory·유지 사본 무결성을 포함한 새 정식 미리보기를 만든다.
적용 직전 재검증·정확한 삭제 계획 승인·첫 수동 결과 확인과 반복 자동화 승인은 따로 남아 있다.
이전의 시간/후행 관계상 9개를 최종 삭제 목록으로 사용하지 않는다.

이번 완료 범위는 **비활성 scope 갱신 준비**다. 실제 설치·정식 미리보기·T05 전체 완료로 표시하지 않는다.
