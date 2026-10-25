# T05 EXDEV 유닛 수정과 한 계획의 명시적 scope 전환·중단 복구

이 문서는 검토 제안이며 아직 운영 승인이 아니다. 실제 승인은 아래의 새 operation plan,
원래 retirement plan, 검토된 EXDEV run을 함께 지정한 명시적 apply 명령에 한정한다.
파일의 존재나 이전 수동 삭제 승인을 새 scope 승인으로 해석하지 않는다.

- 삭제 계획: `31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08`.
- 검토된 실패: `5d7deaf59b1f4c99847a732327ce77af`, 첫 rename EXDEV, 모든 항목 prepared/삭제 0.
- before scope: `da7b03dba075a816d1c8277298dd123ce1db62e54aa9a85920711a18913a6c22`.
- after scope: `84a0b92ae6f8bf0e38ac10ecb4a2615a32af72aebf1c8c28d591c02087cf4983`.

허용 변경은 canonical 수동/자동 service 2개, 설치된 수동 template 1개,
비활성 recurring scope 1개다. 수동/자동 service의 ReadWritePaths 두 sibling을
공통 부모 `/mnt/backup/marketdata-historical` 하나로 바꾼다. 현재 부모의 항목이
`automatic`, `.automatic.retiring` 두 개뿐인지 재확인한다. 부모 자체가 writable해지는 범위도 승인 대상이다.
다른 유닛 옵션, /opt 코드, 데이터 root, 정책·시각 조건·기존 근거·네 mask는 유지한다.
recurring은 `enabled=false`, `approved_at=null`, `approval_reference=null`을 유지한다.

일반 `manual_proposal()`의 scope 일치 검사는 그대로이며 새 구성에서 옛 계획을 자동 제안하지 않는다.
이 별도 도구는 plan.json의 정확한 unit delta·before/after scope·원본 plan/journal/run/inventory 해시를
모두 대조한 경우에만 새 scope의 **해당 한 계획** manual grant를 제안한다.
원래 plan의 automation.scope_id는 과거 관측값으로 보존한다. 새 grant의 scope_transition과 root 감사 기록에
이 계획을 새 unit scope에서 재개하도록 따로 승인했다는 사실을 남긴다.
이것은 일반 scope 불일치 허용이나 검증 비활성화가 아니다. 다른 code/policy/binding/target 차이는 거부한다.

단계는 4개 파일 전환 → daemon-reload → 설치 guard/실효 구성 대조 →
정상 보존 업무 종료 대기 및 잠금 입장 확인 → exact manual grant 활성화 → 기존 설치 서비스 1회 요청이다.
기존 계획·journal·inventory를 재작성하거나 실패 이력을 완료로 고치지 않는다.
서비스만 journal에 다음 시도를 추가하고 기존 보존·시각·후행 관계·당일 backup/restic·무결성·잠금을 재검증한다.
검사에는 이전 실행에서 약 24분이 걸렸다. 이번 검사를 생략하거나 즉시 종료를 약속하지 않는다.

coordinator SH와 설정 디렉터리 flock은 전환 동안 유지한다. worker 종료를 기다릴 때
runner·maintenance·archive의 배타 잠금을 잡지 않는다. 보존 업무를 중지하지 않으며,
daemon-reload 직후 생기는 예약 업무는 최대 600초 정상 종료를 기다린다. 경합이 다시 생기면 보류한다.

시작 요청 직전 intent를 확정한다. intent가 있으면 같은 명령은 추가 기동하지 않는다.
중단 후 finalize는 기존 실행만 관찰하고 정확한 grant만 비활성 원본으로 복원한다.
grant 활성화 뒤 dispatch intent 이전 중단은 실제 새 run이 없고 journal이 그대로인 경우에만
grant를 회수하고 취소/재검토로 남긴다. 자동 재기동하지 않는다.
실제 서비스가 계속 동작하면 grant를 바꾸거나 강제 종료하지 않는다.

원복은 유휴·manual 비활성 아래 네 구성 파일과 manager reload만 원래대로 되돌린다.
새 run·journal 진행·삭제 결과·inventory·자료·기존 완료 상한/생성 순서/등록은 모두 보존한다.
삭제된 checkpoint를 원복한다고 표시하지 않는다. 실패·부분 진행 후 새로운 재개 승인은 별도 검토다.

수동 정리 성공은 동일 journal의 과거 실패 보존, 새 시도 1개·대상 10개 complete,
원래 경로/staging 소멸, 유지 3개·제외 3개 manifest 보존, original plan/inventory와
기존 근거·프로세스·manual 비활성·네 mask 대조까지 확인한 경우에만 기록한다.
반복 자동화 활성화는 포함하지 않으며 T05 전체 완료로 표시하지 않는다.
