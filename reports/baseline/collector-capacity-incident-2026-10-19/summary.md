# 두 실시간 수집기의 용량 보호 중단 — 2026-10-19

**후속 상태:** 승인한 timer 일시 정지와 제한된 중복 제거로 10/19 약 15.45GB를 확보했고, 두 수집기와 pack의 자연 재개·실제 저장/종결을 확인했다. 10/21 현재 가용 17.09GB, archiver 정지는 유지되며 10/20·21 checkpoint 두 개의 편입이 지연 중이다. [완료 조치·현재 상태·미적용 후보·남은 공백](../t18-capacity-recovery-2026-10-19/status.md). 아래 최초 장애·조사 기록을 복구 완료 이후의 현재 상태와 구분한다.

T05 사후 점검 중 **depth·aggTrade 수집 중단**을 확인했다. 읽기 전용 조사에서 드러난 활성 장애이며, 수집 복구·공백 대조는 아직 하지 않았다. T18로 최우선 기록한다.

## 확인된 순서

| KST | 실제 근거 |
|---|---|
| 15:40–16:09 | T05가 자동 정리를 수행. 두 수집기는 기존 invocation에서 계속 자료 처리. [해당 구간 표본](during-retirement.jsonl) |
| 16:53:55 / 16:53:58 | 기존 depth PID 224785 / aggTrade PID 224784가 `Clock evidence capacity reserve exhausted`로 exit 1. child는 interrupted·원문 유지 메시지를 남김 |
| 16:54:11 / 16:54:14 | 기존 Restart 정책으로 새 supervisor 실행, 자료 수집 재개 |
| 17:05:37 / 17:05:41 | 새 supervisor도 동일한 용량 오류로 종료. 이후 시작한 프로세스도 같은 오류로 종료 |
| 21:35 점검 | 두 서비스는 자동 재시작 대기. 시작 전 gate의 `Current clock is not ready: reference_age`/exit 75도 반복됨 |

[용량 오류 원문](capacity-failure.jsonl), [최근 시작 gate 로그](latest-journal.jsonl), [현재 계측·설치 코드 해시](observation.json), [systemd 상태](../t05-recurring-operating-completion-2026-10-19/evidence/systemd-show.txt).

최초 중단은 reference age가 아니라 **시각 근거 Journal.write의 용량 보호**다. 이후 strict 시작 gate의 reference age 대기가 추가됐다. 두 조건을 구분해야 하며, gate만 통과시켜도 공간 부족이 해결되는 것은 아니다.

## 공간과 T05의 관계

설치 `clock_evidence.py:146,168`의 기본 최소 여유는 **10GiB + chunk/record 여유**이고 `shutil.disk_usage(...).free`를 검사한다. 점검 시 운영 데이터/시각 근거 파일시스템의 일반 계정 가용 공간은 **8,123,760,640바이트(약 7.57GiB)**였다. root 예약 공간을 포함한 free와 혼동하지 않는다.

운영 데이터 파일시스템 device는 **66312**, T05 checkpoint의 `/mnt/backup`은 **66314**로 다르다. T05의 7.46GB 공간 증가가 운영 데이터 디스크의 여유 공간을 늘려주지 않는다. 최초 용량 오류는 T05 종료 약 45분 뒤에 발생했고, T05가 수집기 중지 명령을 실행한 기록은 없다. 최초 장애 점검에서는 용량 증가의 구체적인 원인을 아직 분해하지 않았다.

**21:50 KST 후속 사용량 조사:** `/home` 사용 약 277.73GB 중 프로젝트가 229.43GB이고, restic 실행 보고서가 137.15GB다. 그중 136.27GB는 30분마다 기존 snapshot의 `/backup.json`을 다시 읽어 영구 저장한 출력이며, 24개 snapshot에 13,182개가 쌓였다. 실제 설치 코드·receipt·지정한 중복 출력 두 개의 해시로 원인을 연결했다. 최근 완전한 UTC 하루 출력은 약 10–13GB다. 운영 자료 본문·현재 백업 디스크와 구분하며 아직 어떤 파일도 삭제하지 않았다. [사용량 분해와 정확한 조사 범위](disk-usage.md).

`state/runtime-clock/v2/*/latest.json`은 마지막 17:05 근처의 `state=running` 표본이 남아 있다. 이를 현재 프로세스가 살아 있다는 근거로 사용하지 않는다. [depth 마지막 표본](depth-last-clock.json), [aggTrade 마지막 표본](aggtrade-last-clock.json).

## 남은 대응

**장기 개선보다 긴급 복구 우선:** 22:19 가용량이 7.84GB로 더 감소했고, 22:30에는 시각 준비를 통과한 두 수집기가 다시 용량 오류로 종료했다. pack도 용량 부족·후속 시작 제한이 확인됐다. [추가 원문·정확한 범위·대응 순서](recovery-priority.md).

**후속 작업창:** 승인된 archiver timer 일시 정지를 사용자가 실행했고, timer inactive·다음 예약 없음/enable 유지, archiver 유휴·job 없음, 관측 보호 파일과 ledger 불변을 확인했다. 미보관 편입 checkpoint는 0개였으며 향후 편입 지연과 timer 재개 미승인을 별도로 기록했다. [정지 결과](../t18-capacity-recovery-2026-10-19/timer-stop-result.json).

제한된 두 snapshot의 성공 출력 1,956개를 직접 대조하고, 원본 4개/하드링크 교체 1,952개로 예상 15.45GB를 회수하는 고정안을 준비했다. 설치 요청 계약 호환과 12개 검사·운영 읽기 전용 check를 통과했지만 **공간 확보는 승인 대기, 교체 0개**다. [정확한 목록·방법·보존/변경·회수량·복구 범위](../t18-capacity-recovery-2026-10-19/review.md).

장기 구현이나 전체 출력 분류를 기다리며 수집 복구를 미루지 않는다. 원문·DB·미편입 시각 근거·receipt·현재 요청·T05/T16 기록의 복구 의존성을 보존하고, 기존 승인 밖의 임의 삭제·로그 절단·보호 한도 완화·서비스 강제 시작은 하지 않는다. 공간 확보 뒤 실제 자연 재시작/수집 저장과 시각 근거 연결을 먼저 확인하고, pack 후속·interrupted collection과 관측 공백은 별도로 대조해야 한다. 이후 첫 수정은 기존 조회·검증을 유지하며 출력 중복 누적만 없애고, 조회 빈도 조정은 분리한다.

후속에서 승인한 archiver timer의 runtime 정지만 변경했다. 원문·설정·정책·enable/mask·T05 활성 권한은 유지한다. T05 자동 삭제 검증 완료나 timer 정지를 수집 장애 복구 완료로 확대하지 않는다.
