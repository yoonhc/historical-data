# T18 최신 입력 재고정 — 운영 적용 승인 검토안

**2026-10-25 15:10:46 KST 입력으로 고정했다. 작성 당시 새 operation은 운영 미승인·미적용이었다.** 이후 별도 승인과 적용 현황은 [실행 기록](deployment-status.md)에 구분한다. 오늘 정기 backup이 15:07:06에 정상 종결된 뒤 최종 manifest 목록을 확보했다. 기존 operation과 10/22 승인은 [원래 기록](../t18-archive-deploy-2026-10-22/deployment-status.md)에 그대로 보존했다.

## 고정 식별자

| 항목 | 값 |
|---|---|
| 새 operation | `27dd5101bfa0b7005ce0a931b075bc9cccad5a5331e9350a30c4765cbf1ee225` |
| Operation JSON 바이트 SHA-256 | `5fe462588a59c9bfecc94c68d5473143bb4f1e02c56ca834744acd8bb02f2198` |
| 소스 실행본 — 동일 | `restic-output-20261022-585c3e3686704e5c` |
| 적용 도구 — 동일 | `t18-output-operation-20261022-7bb526de6c7fbaef` |
| `handoff.py` SHA-256 — 동일 | `d9cc290b7cefc52c7e35d5d9930da7dcc2829bc383691d4025729f97ec4032e9` |
| 변경 후 T05 scope — 동일 | `2875edd74ccef07cd4a1f09917d147ab0a73cde0a6921a5f3f7cd9930344ef83` |

[새 계획 전체](operation-plan.json) · [실제 차이와 불변 필드 대조](operation-delta.json) · [해시 재사용 근거](reuse-hash-verification.json).

## 이전 계획 대비 실제 차이

| 입력 | 이전 고정 계획 | 새 고정 계획 |
|---|---|
| 완료 manifest 목록 | 8개 | 12개: 기존 8개 유지, 10/22·23·24·25 정상 추가 |
| 미보관 사본 | 10/20·21의 2개 | 10/20–25의 6개 |
| 첫 공유 body 예상량 | 314,548,669 B | 379,678,169 B |
| 최대 독립 임시 stdout | 16,983,273 B | 17,194,286 B |
| 총 요구 공간 | 12,142,692,006 B | 12,208,032,519 B |

기존 8개와 앞서 관찰한 10/22–24 항목의 manifest 바이트 변경·누락은 **0개**다. 오늘 추가 사본을 기존 근거 변조로 처리하지 않았다. 준비 관측 시각·pending 목록·용량 세 값·파생 준비 ID와 그 근거 해시를 갱신했고, 이 때문에 operation ID와 authority/감사 경로가 달라졌다.

공간 계산은 기존 저장 사본 본문 **281,273,837 B** + 미보관 6개 manifest **98,404,332 B** + 최대 임시 출력 **17,194,286 B** + 기존 보호 여유 **10,737,418,240 B** + 작업창 추가 여유 **1,073,741,824 B**다. 당시 `/home` 가용 **16,642,568,192 B(약 16.64GB)**, 요구량 초과 여유 **4,434,535,673 B(약 4.43GB)**로 통과했다. 설치 전과 timer 재개 직전에 도구가 다시 확인한다.

## 정확한 미보관 목록

| UTC 폴더 날짜 | Backup ID | Manifest SHA-256 | Manifest 바이트 |
|---|---|---|---|
| 2026-10-20 | `a284b57feecbb18aa4f3e344d572f886b6877d7032de8cee82fcc7b845c38387` | `fe107f15ab7554c6c293e5e19b0ad8d22c892751b19ba0f03f09c0cc995dd443` | 16,291,559 |
| 2026-10-21 | `ee64631d41bbad436fc9a24fe589d1ee161f9a143328a5863186254f9cc5c0ed` | `089267358b822cdb67bb7a04c2e9d5a6018fa6546d2a2ffd1b9724986889b69a` | 16,983,273 |
| 2026-10-22 | `b3f6573541bbba432fb2218678350596001aa5da73594d82b040c9af0b5aa298` | `94075b7ef8349bc5e69b9a3bffa9f5a26a72afe9032c20e6e07bef84ac74d1bc` | 15,272,331 |
| 2026-10-23 | `063c0197ca794fe5a9fe1ca98be0e9fd34ed81d653607cbc472e3d053cbceda1` | `f7d30426e66045830b9e8a04d70dac37963d7b804d47e46b2970e79bb5a0eee7` | 17,194,286 |
| 2026-10-24 | `baf1ad84e2c007175ade99476b20aecd81eeb3dd00b041ccdd9784385dd97033` | `a736a2d4fcb82edd8df174f34bd5d05c54ca61325e75f31c0a8215c49abf234a` | 16,120,661 |
| 2026-10-25 | `051ea9f55634f7556bf094aacff6f1a45c464d724632d5ae855391563b6bab5f` | `1a46e7f57a34707af118063cf0629a9ad64d119c8d78fcf8c6ca2435212f9c08` | 16,542,222 |

[전체 12개 경로·기존 항목 비교·archive state 연결](current-inventory.json). 미보관 판단은 현재 archiver 기록에 해당 snapshot 연결이 없다는 대조다. 새 restic 조회나 전체 읽기 검사를 수행한 것으로 표시하지 않는다. 편입 전 제외 3개와 이미 보관한 로컬 3개도 목록에 유지했다.

## 재사용과 유지 범위

소스 137개, 기존 Python 환경 1,648개 항목, 적용 도구 5개 및 원래 관리자 시험 도구/결과·66개(10+42+14) 검사 결과 파일의 해시가 일치했다. 새로운 전체 시험·업무 기능·검증 정책 변경은 없다. 새 JSON의 digest·schema·허용된 입력 차이와 CLI `inspect`만 확인했다.

**원래 관리자 시험은 10/22 실행 기록 그대로다.** 원본의 preparation ID를 고쳐 새 시험인 것처럼 만들지 않았다. 새 준비 ID에는 [명시적인 재사용 확인서](system-preflight-reuse.json)를 연결했다. 원본 관리자 보고서·seal·prepared bytes는 각각 보존하고 원래 seal의 준비 파일은 `original-prepared-plan.json`으로 대조한다. 이 확인서의 `passed`는 해시와 변경 범위의 재사용 대조 성공이며, 새 root 시험 성공을 뜻하지 않는다.

구성 6개·boot ledger 연결 전환, 9개 timer 신규 예약 보류/정상 종료/복원, 비활성 recurring 아래 전환과 전체 propose 일치 뒤 새 authority 확정은 [기존 적용 계약](../t18-archive-deploy-2026-10-22/review.md#적용보류원복-순서)을 그대로 따른다. 기존 Python 환경·6개 before/after·T05 scope·600초 정상 종료 한도·coordinator 해제 구간·조회 빈도·전체 읽기 검사·batch 3개·원복 시 archiver 정지 계약도 동일하다. 이전 승인으로 새 operation을 실행하지 않는다.

15:07에는 pack이 정상 작업 중이어서 전체 요청 대조가 `Unfinished request: pack`으로 멈췄다. 상태를 초기화하지 않았고, 자연 종결 뒤 [8개 요청의 저장 근거·종결 대조](terminal-request-proofs.json)가 통과했다. 준비 중 관찰은 적용 직전 잠금 아래 검사를 대신하지 않는다. Chrony·두 supervisor의 PID/invocation, timer 의존관계·보호 입력·현재 grant/ledger는 유지했다.

## 별도 승인 후 사용할 경로와 명령

감사 디렉터리: `/var/lib/marketdata-clock/t18-output-handoffs/27dd5101bfa0b7005ce0a931b075bc9cccad5a5331e9350a30c4765cbf1ee225`

이 디렉터리의 `authority.json`에서 새 operation의 승인 수락 시각을 한 번만 확정하고 `apply-receipt.json`, `rollback-receipt.json`, `attempt-*.json`을 연결한다. [정확한 모든 경로](artifact-paths.json). 현재 해당 root 경로·authority·활성 grant는 생성하지 않았다.

```sh
sudo /usr/bin/python3 -B -I \
  /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t18-archive-deploy-2026-10-25/handoff.py \
  --action apply \
  --approve-operation 27dd5101bfa0b7005ce0a931b075bc9cccad5a5331e9350a30c4765cbf1ee225
```

승인 후에도 최신 목록·boot·입력·유휴/잠금·공간이 맞아야 실행한다. `check`, 같은 operation의 관측 가능한 재개, `stop`, `rollback` 경계는 그대로다. 부분 전환의 stop을 전체 원복으로 표시하지 않으며 새 기록·자료를 지우지 않는다. 실제 service 직접 시작이나 확인용 backup/삭제/전체 복원은 없다.

## 남은 운영 관찰

1. 설치·구성 연결·timer 재개 및 보호 상태 확인.
2. 위 미보관 6개 각각의 snapshot·기존 전체 검사·T05 bundle·요청 종결 확인. batch 3개를 유지하므로 한 번의 실행에 모두 끝난다고 가정하지 않는다.
3. 같은 snapshot 집합의 자연 no-op에서 새 dump/해시 검증과 큰 본문 할당 증가 억제를 함께 확인. 작은 receipt 증가와 본문 할당을 구분한다.

이 세 단계는 모두 미완료다. 정상 신규 사본 추가에 따른 재승인 반복은 [TODO](../../../TODO.md)의 별도 배포 계약 후속이며 이번 재개의 선행 조건으로 추가하지 않았다. 기존 공간 확보·현재 수집/pack 복구 완료와 과거 공백·장기 보관/조회 개선 후속은 별개로 유지한다.
