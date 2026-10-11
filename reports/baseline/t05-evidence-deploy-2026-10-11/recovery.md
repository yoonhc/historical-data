# T05 두 실행 명령 전환의 중단·재개·원복

2026-10-11. [현재 결과](status.md). 설치된 후보·설정은 변경하지 않았다.
원래 [application.md](application.md)는 root 설치 입력으로 고정된 문서여서 보존한다.
현재 활성화·원복 명령은 이 보완 문서를 따른다. 설치기를 다시 실행하지 않는다.

## 중단 원인과 실제 상태

첫 `activate_evidence.py --run`은 16:22:01 KST에 두 새 drop-in을 게시한 뒤 user daemon-reload를 수행했다.
`After`와 `Requires`의 구성원이 그대로여도 systemd 출력 순서가 달라질 수 있는데,
원래 도구는 문자열 전체를 비교해 `Service property differs: After`로 중단했다.
도구가 게시한 두 파일은 정확한 해시 대조 후 모두 제거됐고 다시 daemon-reload했다.
원복 확인도 같은 출력 순서 비교에서 실패했으므로 첫 결과에는 rollback_error가 남았다.

16:26의 별도 읽기 조회에서 두 파일 부재, 양쪽 기존 ExecStart/ExecStartPre/drop-in,
의존성 구성원 일치와 보호 경로·정리 mask·Chrony/수집 PID/invocation 유지를 확인했다.
실제 의존성·시작 검사 변화나 진행 중 backup 중지는 없었다.
이 시간 동안 기존 명령과 예약이 유지됐으며, 전환 도구가 수집을 중단하지 않았다.

- [첫 실패](activation-result.json), [실제 여러 번의 systemctl 출력](activation-events.jsonl).
- [원복 상태 읽기 확인](failed-activation-readback.json).
- [보완 판독기](dependency_reader.py), [재개/원복 도구](activation_recovery.py), [회귀 검사](test_recovery.py).

보완은 `After`/`Requires`의 단일 속성 안에서 이름을 정렬해 원래 판독기에 전달하는 것으로 한정했다.
다른 속성·ExecStartPre 실행 순서·명령 인자·추가 drop-in·보호 옵션은 원래 비교를 그대로 적용한다.
원래 세 도구와 root 사본의 bytes 일치, 코드/설정/보호 경로와 원래 승인 범위를 계속 검증한다.
같은 승인 아래 보완했으며 삭제 권한이나 초기 시각 보정 권한을 열지 않는다.

실제 실패 출력 5개 회귀와 원래 11개 검사가 통과한 뒤 **17:22:52 KST** 재개가 성공했다.
[재개 receipt](activation-recovery-result.json)에 원래 실패/명령/원복 조회와 보완 코드의 해시를 연결했다.
과거 결과를 덮어쓰거나 root 설치를 다시 수행하지 않았다.
대기하던 restic job은 취소하지 않았으며 추가 start/backup 요청은 없었다.

## 현재 원복 절차

원복 필요성이 확인되면 먼저 아래 **읽기 검증**을 yoonhc로 실행한다.

```sh
/usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-evidence-deploy-2026-10-11/activation_recovery.py --rollback
```

`validated=true`와 `idle=true`를 함께 확인한다. 백업/archiver 또는 시작 검사가 실제 실행 중이면
완료를 기다리며 종료하지 않는다. gate의 inactive/PID 0 대기 job은 기록하며 취소하지 않는다.
운영 변경 범위가 승인된 원복을 실제 수행할 때만 같은 명령에 `--run`을 붙인다.

```sh
/usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-evidence-deploy-2026-10-11/activation_recovery.py --rollback --run
```

이 명령은 성공한 recovery receipt와 테스트된 보완 코드 해시, root에 보존한 원래 도구,
실제 두 새 drop-in의 정확한 해시, 기존 시작 검사·의존성·예약·보호 경로를 대조한다.
이번에 추가한 두 파일만 제거하고 user manager를 reload한 뒤 이전 명령을 확인한다.
예상하지 못한 수정이나 이전 원복 receipt가 있으면 중단한다.

**registry·baseline·완료 상한·sidecar·시장 자료·catalog·고정 release·실패 기록은 보존한다.**
새 시스템/기존 사용자 자동 정리 mask도 유지한다. 기존 `rollback_evidence.py`는 첫 실패 receipt를
성공으로 해석하지 못하므로 현재 전환의 원복에 사용하지 않는다. 원본으로만 보존한다.

17:24에 위 읽기 검증을 실제 최종 설치 경로에서 통과했다. [결과](rollback-readiness.json).
성공한 활성화를 다시 되돌렸다가 재적용하는 시험은 하지 않았다.
구형 archiver로 원복하면 새 T05 sidecar 보존 receipt가 추가되지 않는 한계가 돌아온다.
원복을 자동 정리 활성화나 새 근거의 보존 완료로 표현하지 않는다.
