# 비활성 scope 갱신의 운영 승인

2026-10-17 사용자 메시지로 다음 계획을 명시적으로 승인받았다.

- 계획: `82d0f54af28cf5190020c7cc57763a401e24dac417c248d37fa47afc9527ec5d`
- 대상: `/etc/marketdata/checkpoint-clock-retirement/recurring.json` 한 파일.
- 변경: 현재 bootstrap 프로필 해시와 파생 scope ID만 맞춘다.
- 유지: `enabled=false`, `approved_at=null`, `approval_reference=null`.
- 적용 직전 현재 boot·구성·ledger·before/after·보호 입력·권한·네 정리 mask를 재대조한다.
  계획 차이 또는 잠금 경합이면 우회하지 않고 보류한다.
- 기존 baseline·완료 상한·생성 순서·등록 기록을 보존하고 정상 신규 근거 객체 추가는 허용한다.
- 적용 후 정확한 after 해시·root 소유권·0644·비활성·보호 입력·수집기/Chrony 유지와 감사 기록을 확인한다.
- 문서에 고정한 원복은 승인 범위에 포함한다.

정식 미리보기·근거 등록·삭제·timer 활성화·서비스 제어·당일 조건/보존 정책 변경은 승인 범위 밖이다.
반영 완료는 T05 전체 완료나 삭제 승인을 뜻하지 않는다.
후속으로 실제 당일 backup과 같은 backup ID의 restic 저장·전체 검사 조건을 확인한다.

이 문서는 이번 한 파일 갱신에 관한 사용자 승인 기록이다.
운영 recurring/manual 문서의 삭제 승인 필드를 활성화하거나 이 문서로 채우지 않는다.
