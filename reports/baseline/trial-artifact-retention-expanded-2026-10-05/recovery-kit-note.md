# 시험 산출물 정리와 이후 복구 경로

2026-10-05 정리 계획에 맞춘 안내다. 이 문서만으로 실제 삭제가 완료됐다고 판단하지 않는다.
승인 후 정리 결과를 별도 기록하며, 그때 이 문서와 완료 기록의 사본을 복구 키트에도 추가한다.
키트의 기존 코드·프로필·manifest·원래 검증 기록은 수정하지 않는다.

- 실제로 계속 갱신되는 백업은 `/mnt/backup/marketdata-restic`이다.
- `/mnt/backup/marketdata-restic-pilot`과
  `/mnt/backup/marketdata-independent-recovery-20261003/repository`는 사용이 끝난 시험 저장소다.
  이번 정리 후보이며, 향후 복구 절차가 이 경로의 존재를 요구해서는 안 된다.
- 복구 키트는 `/mnt/backup/marketdata-recovery-kit-20261003`에 보존한다.
  키트의 `secrets/restic-password`와 운영 암호 파일은 정리 대상이 아니다.

복구 시 키트의 `RECOVERY.md`에 있는 **원본 호스트 없이 복구하는 순서**를 따른다.
그 문서의 시험 경로·`profile.toml`·`isolation_entry.py`는 10/3 격리 환경의 기록이며,
삭제된 시험 디렉터리를 다시 만드는 일반 복구 명령이 아니다.

새 작업 프로필에서 현재 마운트·UUID를 확인하고, 실제 운영 restic 저장소 또는 그 저장소를
검증해 복사한 새 목적지를 지정한다. 저장소 ID는
`b5a4434c95fcf4a9eb1339689cdea42c1da08bd34b845193dffce2aa8203e558`로 대조한다.
기존 `source_root`·원래 checkpoint 경로·scope의 의미를 유지하며, 출력·상태·캐시는 새 전용 경로를 쓴다.
원래 manifest의 출처 문자열을 고쳐 검사를 통과시키지 않는다.

10/3 복구에 사용한 명시적 snapshot
`d27a0a557e1795a4651132016491f1b2c6735e8181b71303f3adccd414f0f8ad`는 운영 저장소에 남긴다.
9/28 보호 지점 `7ddea5243f9262eb058b15e97df1976eb5c369de386010c736b449a10a250713`도 유지한다.
운영 복원 작업 루트 `/mnt/backup/marketdata-restic-restore`는 남기며, 과거 run 한 개만 정리 후보로 삼는다.

시험 전용 `backup-host.toml`의 원문은 historical_data의 비공개 로컬 보고서에 복사해 보존했다.
실제 정리 시 기존 설정 경로에서 해제할 계획이다. 새 시험은 예시에서 새 프로필을 만들고,
검증된 입력·새 저장소·새 작업 폴더·새 보고서 경로를 명시한다. 오래된 프로필이나 9/27 실행 명령을
운영 복구 명령으로 재사용하지 않는다.

시험 저장소 삭제는 운영 restic의 `forget`·`prune`이나 복구 키트 삭제를 뜻하지 않는다.
암호의 PC 외부 보관, 다른 장비의 백업 사본, 새 Proxmox VM의 부팅·서비스 재개 확인은 별도 후속 과제다.
