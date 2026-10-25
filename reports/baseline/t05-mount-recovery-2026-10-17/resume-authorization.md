# 설치 완료 후 repository Busy 한 건의 명시적 재개

이 문서는 새로운 재개 검토안이며 아직 승인받은 것은 아니다.
원래 operation `eced87a499269ebe5da84be1b0795ebfd2c109ce06f37db4e5e26f8027b9e231`은
네 구성 파일 적용을 완료하고 한 번의 서비스 요청을 발행했다.
run `2d9f7b8b4a434183a875dfa441171295`는 원래 journal을 변경하기 전에 repository lock Busy로 종료했다.

승인할 범위는 **현재 after 구성 아래 같은 plan의 한 번 재개 및 종료 후 manual 비활성 복원**이다.
기존 삭제 plan `31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08`,
원래 EXDEV journal의 해시와 inventory, 이번 Busy run/root 결과를 모두 연결한다.
후보 10·유지 3·제외 3, 기존 보관/시각/restic/잠금 조건과 기존 근거는 동일하다.

유닛·recurring·설정 재설치, daemon-reload, timer stop/start/mask, 다른 업무 중지 명령은 없다.
이미 설치된 네 파일의 after 바이트·권한·실효 guard·현재 boot/보호 입력·기존 근거·프로세스·네 mask,
원래 사본/journal/inventory, 정확한 네 과거 적용 run만 대조한다.
manual grant는 새 승인과 이번 Busy run을 참조해 임시 활성화하고 한 시스템 서비스만 요청한다.

실행 중인 pack/backup/restic·대기 job과 공유 잠금을 확인한다.
세 보존 timer의 다음 realtime/monotonic 기동이 60초 이내이면 grant/기동 전에 보류한다.
이 60초는 이번 18.8초짜리 사전 근거 조회 중 예약과 겹치는 것을 줄이기 위한 시작 여유이며,
UTC 정확도나 삭제 보호 기간 정책을 바꾸는 값이 아니다. 미래 경합이 불가능하다는 보장이 아니며
실제 엔진은 계속 동일한 잠금을 직접 획득해야 한다. 수동 외부 기동 등으로 경합하면 그대로 실패/보류한다.

원래 설치 계획과 도구는 수정하거나 다시 실행하지 않는다.
새 scope 전환을 다시 설치하지 않고 이미 적용된 scope에 원래 계획을 연결하는 승인 근거를 재사용하며
새 grant에 이번 Busy run/해시와 새 operation ID를 추가한다.
원래 계획·실패/journal·inventory·근거를 덮어쓰지 않는다.

시작 intent를 먼저 확정하고 같은 명령의 두 번째 시작을 거부한다.
중단 후 finalize는 기존 실행의 종료와 정확한 grant 복원만 수행한다.
서비스가 동작 중이거나 grant가 달라지면 덮어쓰거나 강제 종료하지 않는다.
성공 판정과 실패 이력 보존은 검증된 기존 finalizer를 사용한다.
또 실패하면 자동 반복하지 않고 새 원본 상태를 점검한다. 자동 정리 활성화는 포함하지 않는다.
