# systemd 개발 템플릿

현재 운영은 시스템 manager의 고정 `/opt` 실행본을 사용한다.
이 폴더의 파일 편집이나 renderer 실행은 운영 설치·활성화가 아니다.
실제 실행 경로와 상태 조회는 [운영 안내](../../docs/system-runtime-operations.md)를 따른다.

## 현재 개발 기준

- `scheduled/`: 8개 예약 업무와 전용 waiter의 시스템 유닛 템플릿.
  `@RELEASE@`·`@STORE_ROOT@` 등의 입력을
  [render_scheduled_units.py](../../scripts/render_scheduled_units.py)가 치환한다.
  모든 업무는 같은 `scripts/run_job.py`와 루트 패키지를 사용하며,
  복사한 운영 timer의 주기·활성화 조건은 그대로 유지한다.
- 수집기와 부팅 준비: [runtime](../runtime/README.md)의 renderer를 사용한다.
  시스템 업무 inventory를 검사하는 bootstrap adapter도 같은 소스에 있다.
- T05: [render_retirement_units.py](../../scripts/render_retirement_units.py)가
  미리보기·수동 적용·반복 service와 timer를 출력한다.
  실제 정리 유닛은 source/staging의 **공통 부모 한 경로**를 쓰기 허용한다.
  미리보기에는 그 쓰기 경계를 추가하지 않는다.
- 이 디렉터리 바로 아래 `marketdata-checkpoint-retirement.service`·`.timer`는
  현재 시스템 구성을 바탕으로 한 참고 파일이다. `REVIEWED_UNIFIED_RELEASE`는
  실제 배포 경로가 아닌 자리표시자다.

renderer는 파일 출력만 한다. 현재 유닛 설치, 승인 문서·scope·부팅 ledger 전환,
기존 request 처리와 timer 제어는 고정 실행본을 배포할 때 별도로 검토한다.
기존 수동/반복 승인으로 개발 소스를 바로 실행하지 않는다.

## 기존 사용자 유닛

`scheduled/` 밖의 나머지 `.service`·`.timer`는 시스템 manager 이전 전의 사용자 유닛 예시다.
현재 운영 설치본으로 사용하거나 새 unified 배포의 입력으로 선택하지 않는다.
기존 사용자 예약 업무·수집기의 차단 상태는 현재 운영 계약을 따른다.

## 검사

루트 `tests/test_unified_source.py`는 실제 업무의 wrapper 연결, 동일 timer 바이트,
T05 공통 부모 쓰기 경계, bootstrap inventory 연결을 검사한다.
호스트 service를 시작하거나 checkpoint를 삭제하는 시험이 아니다.

10/25 최종 검토에서는 생성한 scheduled 유닛 17개와 T05 유닛 4개를
`systemd-analyze verify`로 추가 확인했다. 구문 검사 통과이며 실제 설치·보호 namespace 검증은
새 고정 배포 계획의 범위로 남는다. [검사 코드·결과와 한계](../../reports/baseline/source-integration-2026-10-25/final-review.md)를 참고한다.
