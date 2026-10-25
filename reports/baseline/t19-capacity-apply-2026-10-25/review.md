# T19 다음 제한된 공간 회수 — 최종 적용 검토안

**운영 결과:** 이후 정확한 operation을 승인받아 20:13에 1,022개 교체와 사후 대조를 완료했다. [실제 결과·공간·남은 범위](result.md). 아래 검토안과 중간 기록은 당시 상태로 보존하며, 완료한 apply/resume은 반복하지 않는다.

2026-10-25 19:30 KST. **적용 도구·격리 검사·실제 호스트의 삭제 없는 check까지 완료했다. 운영 출력 교체는 0건이며, 파일 변경 승인은 아직 받지 않았다.** 현재 archiver timer·배포본을 유지한다. T18의 완료 범위를 다시 열거나 전체 과거 출력 정리로 확대하지 않는다.

**후속 20:06:** 사용자가 이 고정 operation의 적용·동일 범위 재개를 승인했다. 진행 중이던 20:05 자연 archiver가 종료한 뒤 고정 apply를 한 번 실행했다. [승인·진입 기록](approval-observation.json), [한 번 확정한 authority](audit/authority.json). 아래는 승인 전 검토안의 원문이며 실제 완료 결과는 별도 사후 기록으로 연결한다.

## 승인 대상으로 고정한 것

| 구분 | 식별자 |
|---|---|
| Operation | `7ce8007352fb195b713f2efc067cf5712c453a826dc6d8d96657f3f49c30eb65` |
| [operation.json](operation.json) 바이트 SHA-256 | `98f9cb59adc39581bfa3d3d6c32379e686b4580b270574a21c4c56c1d141b3e4` |
| [reclaim.py](reclaim.py) SHA-256 | `295c59c538b3cee939dbc6f842ddf38a08c6c363869a24d2529eb1de0db23422` |
| 기존 정확한 목록 proposal | `a15f778666f6f377765466bd27076f4474977c5629ca1228b9cdbd7d5cf85e93` |
| [전체 선택 목록](../archive-capacity-planning-2026-10-25/verified-selection.json) 바이트 SHA-256 | `975299ec183583d3345bf5b35a5619285d4a74604ca95f4083a3a69c8f0d12a5` |

종료된 성공 manifest 출력 1,024개 중 두 원본은 유지하고 **정확한 1,022개 경로를 하드링크로 원자 교체**한다. 두 snapshot·backup ID·manifest SHA·전체 경로와 최초 inode/권한/시각/receipt는 [목록 검토안](../archive-capacity-planning-2026-10-25/review.md)에 있다. 10/19 이미 처리한 1,952개나 새 공유 body pool은 대상에 포함하지 않는다.

승인 범위는 이 목록의 교체, 작은 감사 파일 생성, 같은 operation의 검증된 중단 재개다. 새 대상 선택·삭제/압축·payload 복사·timer/service 제어·설정/원장 변경은 포함하지 않는다. 기존 완료 operation이나 승인을 재사용하지 않는다.

## 공간과 유지되는 참조

- 예상 회수 **8,612,974,592 B = 8.61GB / 8.02GiB**. 실제 host check의 `/home` 가용은 **15,948,533,760 B**다. 같은 조건이면 감사 예산 8MiB 차감 뒤 약 **24.55GB**가 된다. 동시 쓰기와 열린 inode 때문에 실제 `df` 증가는 달라질 수 있다.
- 큰 임시 payload는 **0 B**, 한 번에 임시 하드링크 하나와 작은 감사 기록만 필요하다. `/home` 가용이 기존 보호 여유 **10GiB + 감사 예산 8MiB** 미만이면 보류하며, 적용 중 32개마다 다시 확인한다.
- 기존 파일 경로·전체 바이트·소유권·0600·xattr와 **1,568개 command/run receipt**를 유지한다. inode·link 수·ctime은 달라지고 mtime은 유지 원본과 공유한다. 실제 조회 시각은 바꾸지 않는 receipt를 사용한다.
- 현재 request 계약 해시는 기존 실제 하드링크 호환 검사와 같다. 완료한 오래된 UUID 출력만 다루며 현재 writer는 새 UUID를 `xb`로 만든다. 이후 공유 경로를 제자리 쓰기/일괄 chmod하는 사용법은 이 계약에 포함하지 않는다.
- 이전 바이트/metadata 대조·원자 교체 알고리즘을 해시로 고정해 재사용한다. 적용 직전 전체 선택 바이트를 다시 읽고 각 교체 직전 양쪽 바이트도 대조한다. 마지막에는 두 공유 inode를 해시로 확인하고 모든 경로가 해당 inode를 가리키는지 대조한다. 초기 적용의 논리 읽기 합은 약 **25.87GB**, 64MiB/s 읽기 예산상 약 **6분 26초**이며 실제 시간은 부하/동기화에 따라 길어진다. 시장 데이터나 전체 저장소 무결성 검사를 새로 수행하는 것은 아니다.

## 활성 timer와 잠금 경계

1. UID 1000·현재 boot·고정 도구/코드/유닛/프로필/승인 파일의 내용·권한, enable 링크와 사용자 T05 두 mask를 대조한다. archiver timer는 active/enabled, 서비스는 PID/job 없는 유휴여야 한다.
2. 도구 자체 잠금 뒤 **기존 archive.lock을 비차단으로 획득**한다. 잠금 파일을 새로 만들거나 잘라 쓰지 않는다. 경합이나 진입 때 발행된 archiver 업무가 있으면 보류한다. coordinator·request 잠금은 잡지 않는다.
3. 잠금 아래 미완료 archive operation, 선택 항목·바이트·receipt·공간을 재대조하고 승인된 경로만 처리한다. 처음 authority를 한 번 기록한 뒤 재실행에서 승인 시각/범위를 새로 만들지 않는다.
4. 작업 중 자연 예약이 오면 기존 archive 잠금 진입에서 본문이 차단될 수 있다. 현재 wrapper의 표시는 **failed/75**, run의 사유는 `lock_busy`다. 이 기록을 정상 완료로 바꾸거나 지우지 않는다. 같은 요청/pins는 다음 자연 평가에 남기며 수동 start/reset을 하지 않는다. 이 작업 때문에 해당 회차의 보관이 지연될 수 있다.
5. 선택 경로·receipt·임시 링크 부재·archive state와 보호 입력을 대조하고 잠금을 해제한다. 실제 회수 공간을 따로 기록한다. 자연 예약에 따른 새 request/attempt/observation 쓰기는 우리 도구의 변경이나 과거 선택 항목의 변조로 취급하지 않는다.

새 checkpoint·snapshot·무관한 run의 정상 추가는 이 operation의 목록 불일치가 아니다. **이미 선택한 경로의 바이트/권한/receipt 불일치, 코드·구성 변경, 불명확한 링크는 거부**한다. 대상 목록을 자동 확대하거나 요청/pins/예산을 초기화하지 않는다.

## 중단·재개

SIGINT/SIGTERM이면 기록을 시도하고 잠금을 놓는다. SIGKILL은 기록되지 않은 교체 또는 정확한 임시 링크 하나를 남길 수 있다. 기존 출력 바이트는 유지한다. 원본 상태·승인한 공유 inode·이번 operation의 임시 링크만 인정하고, 다른 링크·소스 변경·receipt 변경은 자동 해결하지 않는다.

`replace` 직후 디렉터리 fsync 전에 중단된 경우, 재호출에서 이미 공유된 경로라도 디렉터리를 다시 동기화한다. **프로세스 SIGKILL 시험을 정전 내구성 시험으로 표시하지 않는다.**

중간 실패/공간 보류 시 일부 경로는 이미 공유됐을 수 있다. 결과에서 불확실한 교체 수를 0건으로 단정하지 않고, JSONL과 실제 경로를 대조해 같은 operation으로 재개한다. 독립 파일을 대량 복사해 되돌리는 자동 원복은 제공하지 않는다. 그 복사는 이미 확보한 공간을 다시 소비하며, 경로/내용 기반 참조 복구에 필요하지 않다. 다른 operation으로 우회하지 않는다.

최종 성공은 `complete.json`에 남는다. 완료한 operation을 다시 호출해도 출력 교체를 반복하지 않는다. 감사 파일 자체가 불명확하거나 예산을 소진하면 덮어쓰지 않고 확인 필요로 멈춘다.

## 실제 확인과 한계

**[29개 검사](tests-final.xml) 통과:** 기존 교체 알고리즘 12개와 새 adapter 17개다. [시험 코드](test_reclaim.py)는 활성 timer/유휴 조건, 비차단 실제 flock, 동일 operation 재개, SIGKILL 두 지점, 교체 후 fsync 실패 재호출, 공간/감사 실패, receipt/경로 거부와 정상 신규 기록 보존을 확인했다.

설치된 `run_job.py`·`request_contract.py`·`job_clock.py`·`restic_schedule.py`·`archive_backups.py`를 사용해 **잠금 중 본문 진입 0회 → 실패/75와 원래 pins 유지 → 잠금 해제 후 같은 request ID/identity로 complete/0**를 확인했다. 실제 파일·lock·감시 thread를 썼지만 Chrony와 업무 본문·manager 출처는 격리 입력이다. 운영 systemd의 예약을 인위적으로 발생시키거나 실제 restic 쓰기를 한 시험은 아니다. 과거 실패 attempt/run 바이트도 유지했다.

**[19:30:07 실제 호스트 check](audit/2434c97e601f4d8b90244b7fcc024223.json)는 통과했다.** timer active/enabled·서비스 유휴, 실제 archive 잠금, 보호 입력과 1,568개 receipt, 아직 교체되지 않은 정확한 경로 1,022개를 확인했다. 현재 request `5d6f78dd5e524c538e3916e89a291b26`은 complete/0이었다. 이 check는 metadata와 작은 receipt 대조이며 `payload_rehashed=false`다. 19:07까지 마친 전체 선택 바이트 검증을 다시 했다고 주장하지 않는다. 실제 적용 시에는 바이트 재검증을 수행한다.

최초 미승인 operation `9d24fbb7…`의 [실패 check](audit/550e7f49626e4e2ead35cbd856b35707.json)도 남긴다. sandbox가 root 파일의 소유권을 65534로 표시한 값을 계획에 사용해 host check가 거부했다. [실제 호스트 소유권](host-protected-inputs.json)을 읽었고 **내용 해시/mode 차이 없이 실제 UID/GID 0임을 확인**했다. 소유권 검사를 없애지 않고 host 값으로 다시 고정했다. [이전 계획과 코드](superseded/9d24fbb7c0651a30635014c3f652f3b59b8e459f12dbd1ff601dc61ab2939afb/operation.json)는 보존했으며 출력 공유 도구 본문/29개 검사 결과는 같고, 운영 파일 변경은 없었다.

## 승인 후 실행할 정확한 명령

현재는 검토용 명령이며 아직 실행하지 않았다. UID 1000으로 **sudo 없이** 실행한다.

```sh
/usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t19-capacity-apply-2026-10-25/reclaim.py \
  --action apply \
  --approve-operation 7ce8007352fb195b713f2efc067cf5712c453a826dc6d8d96657f3f49c30eb65
```

중단 후 정확한 경로/기록을 검토하고 같은 범위로 이어갈 때만 `--action resume`을 사용한다. 승인 인자는 같다. 삭제 없는 점검은 `--action check`이며 approval 인자를 받지 않는다. tool sandbox에서는 root 소유권이 다르게 보일 수 있으므로 실제 host 실행을 기준으로 한다.

## 별도 상태

[collector 작은 관찰](collector-observation.json)에서도 두 supervisor는 같은 PID/invocation이며 `between_sessions`, child 없음, `prior_hard_fault_blocks_stale_exception`이다. 다음 자연 child는 아직 확인하지 못했다. 공간 장애로 단정하거나 시각 정책/프로세스를 바꾸지 않았다. T05의 **10/26 15:40 자연 예약**과 별도로 관찰한다.

전체 과거 출력 정리·일반 기록 예산·조회 빈도·독립 복구·과거 수집 공백은 [TODO](../../../TODO.md)의 각 범위에 남아 있다. 이 8.61GB 묶음을 장기 관리 전체 완료로 표시하지 않는다.
