# T18 미보관 편입·반복 출력 할당 운영 확인

2026-10-25 **설치·재개, 10/20–25 여섯 사본 편입, 같은 snapshot 집합의 자연 no-op에서 중복 본문 할당 억제**를 각각 확인했다. 사용자 검토에 따라 이 장애 복구 범위는 [T18 완료 보고서](../t18-incident-completion-2026-10-25/summary.md)로 닫았다. 기존 대량 출력 정리·장기 조회/보관 최적화는 T19, 과거 수집 공백은 T18 후속으로 분리했다. 아래 시각은 KST다.

[읽기 전용 대조 결과](archival-audit-20261025T090337Z.json) · [대조 코드](audit_archival.py) · [17:57 원본 관찰](archive-observed-20261025T085709Z.json) · [설치와 두 실패/승인된 복구](deployment-status.md).

설치 당시 root receipt의 `operating_observation_pending=true`는 그 시점의 기록으로 보존했다. 이번 후속 완료는 새 관찰과 이 문서에 연결하며 원래 receipt·authority·실패 이력을 고쳐 쓰지 않는다.

대조는 설치된 Python 3.12 환경의 T05 decoder와 설치된 request contract로 수행했다. 이미 업무가 남긴 receipt·작은 bundle·불변 attempt를 읽고 출력 inode/할당량을 확인했다. 이번 관찰에서 restic 명령·전체 무결성 검사·backup·service start·timer 제어를 실행하지 않았다. 시장 자료나 큰 manifest 본문을 다시 전량 읽지 않았다.

## 실제 두 batch와 두 자연 no-op

| 실제 실행 | archive run | 신규 사본 | 종료 | 저장 snapshot 집합 |
|---|---|---:|---|---:|
| 16:02:03–16:35:26 | `96e57542113d4f82be4c191c883fb34e` | 3 | complete/0 | 27 |
| 16:35:26–17:09:13 | `ad1f78f8e2724eb39ed4a2443884152a` | 3 | complete/0 | 30 |
| 17:09:13–17:10:11 | `7cf6ce33be4849768f5cdda922bf1399` | 0 | complete/0 | 같은 30개 |
| 17:35:00–17:35:57 | `357c3a3bc710468a8d2bd3fe95c39a45` | 0 | complete/0 | 같은 30개 |

네 실행 모두 실제 system manager의 **현재 start job ActivationDetails**에 지정 timer가 있고, 해당 invocation·scheduled report·archive run·request attempt를 연결했다. 뒤의 세 실행은 환경변수에 최초 trigger 시각이 남아 `environment_matches_job=false`였지만, 설치된 기존 adapter가 현재 job을 조회해 `verified_timer`로 판정한 근거가 있다. 과거 환경변수 시각을 현재 실행 시각으로 쓰지 않았다.

각 immutable attempt에 실제 업무 반환 0, 오류 없음, 감시 `stopped=true`가 있으며, 설치된 `request_contract.classify()`로 저장 보고서 해시·내용과 `complete/archive_preserved/0`를 다시 대조했다. 저장 결과를 발견해 복구한 것으로 꾸미지 않았으며 `recovered_committed_result=false`다. batch 3개를 유지했고 다음 batch의 시각은 실제 기록으로 확인했다.

## 정확한 여섯 사본

| 날짜 | Backup ID | 새 snapshot ID | T05 bundle 객체 |
|---|---|---|---:|
| 10/20 | `a284b57feecbb18aa4f3e344d572f886b6877d7032de8cee82fcc7b845c38387` | `1c836a10d19f7254c855dc6bf9157a1aac1befb531eb58b231774068eb22ce64` | 17 |
| 10/21 | `ee64631d41bbad436fc9a24fe589d1ee161f9a143328a5863186254f9cc5c0ed` | `349e82caee0e1522fd25b7807c6719597f80c72598c6241d9644e568facede41` | 18 |
| 10/22 | `b3f6573541bbba432fb2218678350596001aa5da73594d82b040c9af0b5aa298` | `e93cf60e4fc018ef2bb8854204c149a878b98ff9d5165605892fb53fa42ec2c3` | 19 |
| 10/23 | `063c0197ca794fe5a9fe1ca98be0e9fd34ed81d653607cbc472e3d053cbceda1` | `4abb3139f529ec9e80a2eed5bf713b50366b461a05c452655e7eb06df7e648be` | 20 |
| 10/24 | `baf1ad84e2c007175ade99476b20aecd81eeb3dd00b041ccdd9784385dd97033` | `9fb46089883fed60204af8d9d360c99b417fb9b2ceb9cfe9edca4df3e5144f4b` | 21 |
| 10/25 | `051ea9f55634f7556bf094aacff6f1a45c464d724632d5ae855391563b6bab5f` | `e0863fa25a1e5f5454d7a726c429cb4830b142c6a2fc8adb8e7e9af5cac1efbf` | 22 |

여섯 건 모두 정확한 backup ID tag·입력 경로의 `backup` exit 0 summary에 새 snapshot ID가 기록됐다. manifest 해시는 승인된 여섯 항목과 archive 저장 기록에서 일치한다.

첫 batch의 기존 `check --read-data`는 **16:35:25**, 둘째 batch는 **17:09:12**에 exit 0으로 끝났다. 각 snapshot이 해당 run의 저장 집합과 최종 30개 전체 검사 집합에 포함되는 것을 확인했다. no-op 두 회차에서 전체 검사를 다시 수행한 것은 아니다.

각 snapshot에 대해 archiver가 실제 `/checkpoint-evidence.json`을 dump한 작은 stdout을 읽었다. 그 바이트가 로컬 sidecar 및 archive receipt의 SHA-256·객체 목록과 일치하며, 설치된 decoder가 identity·객체 해시·완료/선행 관계를 재구성했다. 공통 baseline은 `5ec056db51157f58a4537cabf374eee65148dfd901007c1138c1cba481b6cf0b`이고 원래 완료 상한·선행 객체는 그대로다. 새로운 restic dump나 근거 등록은 하지 않았다.

## 반복 할당 억제 — 최초 생성 비용과 분리

최초 기존 24개 본문 할당은 **281,415,680 B**였다. 여섯 새 manifest 편입 후 30개 본문의 논리 크기는 **379,678,169 B**, 고유 inode의 실제 할당은 **379,854,848 B**다. 추가 **98,439,168 B**는 새 여섯 본문의 보관 비용이다.

| 자연 no-op | 새 dump·기대 해시 검증 | 출력 경로 논리 크기 합 | 큰 본문 추가 보존 할당 | 측정한 기타 파일 할당 |
|---|---:|---:|---:|---:|
| 17:09 | 30회 | 379,678,169 B | **0 B** | 290,816 B |
| 17:35 | 30회 | 379,678,169 B | **0 B** | 290,816 B |

각 실행의 command receipt는 새 조회 시각·성공·`fresh_dump_expected_hash_match`를 남겼다. run stdout 경로는 해당 body와 동일 device/inode이고, 둘째 batch와 두 no-op의 본문 inode 집합이 동일하다. 경로별 논리 크기를 합산해 약 380MB씩 새 공간이 소비됐다고 계산하지 않는다. 큰 본문의 해시 검사는 실제 archiver receipt에 근거하며 이번 관찰에서 30개 본문을 다시 해시하지 않았다.

기타 파일 측정은 공유 stdout을 제외한 run 파일, scheduled report, 불변 wrapper attempt다. 논리 크기는 각각 167,978/167,976 B이고, 별도 판정 JSONL은 각 attempt 5건·8,225/8,216 B 증가했다. 디렉터리 메타데이터와 현재 request 교체 등의 총량은 이 숫자에 포함하지 않았다. **다운로드·독립 임시 stdout 쓰기·검증 I/O는 계속 수행한다.** 공유 stdout mtime 대신 command receipt의 시작/종료 시각을 사용한다.

## 현재 수집·공간과 남은 한계

[18:04 manager 조회](manager-after-archival.txt)에서 archiver 직전 invocation은 성공 종료·유휴, timer active, 다음 예약 18:05였다. Chrony와 두 collector supervisor는 승인 전과 같은 PID/invocation으로 active다. [18:05:43 표본](collector-sample-20261025T090543Z.json)은 depth 8,600건·aggTrade 5,894건 저장 중인 현재 collection에서 **원문·segment·새 시각 chunk 각 한 개씩**을 실제 읽고 catalog 해시와 대조했다. collection·clock run·systemd supervisor 연결도 같다.

가용 공간은 18:03 **16,036,667,392 B**, 18:05 **16,030,085,120 B(약 16.03GB/14.93GiB)**다. 동시 수집 등 모든 쓰기가 포함된 값이며 감소분을 archiver에만 귀속하지 않는다. 현재 원문 저장과 시각 근거 게시를 확인했지만, clock 표본의 `clock_usable=false`·`prior_hard_fault_blocks_stale_exception`도 그대로 기록했다. 기존 시각 품질·공백 후속을 정상이라고 지우지 않으며 이를 용량 재발이나 새 출력 공유 결함으로 단정하지 않는다.

T05 **16:02 예약 시각 검사 실패/1, 삭제 0개**는 그대로다. 다음 **10/26 15:40** 자연 예약은 당시 당일 backup/restic·시각·무결성·잠금 조건으로 별도 평가해야 한다. 오늘 여섯 사본 편입 완료가 내일 실행 성공을 미리 보장하지 않는다. [후속의 범위와 근거](timer-restoration-followup.md).

향후 조치가 필요할 때 사용할 [정확한 stop/rollback 도구 연결](stop-rollback-tools.md)을 별도로 명시했다. 현재 설정·timer는 유지한다. 장기 조회·보관 정책, 과거 출력 추가 정리, 과거 수집 공백은 [TODO](../../../TODO.md)의 별도 후속이며 이번 완료에 포함하지 않는다.
