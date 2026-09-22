# 기존 OI 실험 검토와 독립 검증

검토 대상: `/home/yoonhc/projects/archive/oi-5m-research-complete_2026-09-19_82e28ba`.
원래 폴더는 읽기만 했으며, 새 수집 코드나 결과를 그곳에 기록하지 않았다.

## 참고한 실험

- `research/price_shock_oi_5m/OFFSET_FOLLOWUP_2026-09-14.md`
- `research/price_shock_oi_5m/TARDIS_OI_CLOCK_QA_2026-09-14.md`
- `research/price_shock_oi_5m/ARCHIVE_PARITY_2026-09-14.md`
- `research/oi_measurement_uncertainty/ACQUISITION_COMPLETE_RESULTS_2026-09-15.md`
- `research/oi_measurement_uncertainty/VALIDATION_2025H1_CLASSIFICATION_RESULT_2026-09-18.md`
- `research/oi_measurement_uncertainty/VALIDATION_2025H1_ACQUISITION_RESULT_2026-09-18.md`

전략 성과나 특정 OI 신호의 유효성은 이번 검증 대상이 아니다. 원천 품질·시각 해석·재현성에
관한 결과를 데이터 플랫폼에 반영했다. 기존 연구의 실행 승인 절차는 새 프로젝트의 규칙으로 옮기지 않았다.

## 독립 재검산 결과

`scripts/audit_legacy_oi.py`는 표준 라이브러리로 ZIP과 인접 CHECKSUM을 직접 검사한다.
기존 정규화 결과를 그대로 신뢰하지 않고 원본 CSV의 날짜 격자·정렬·0값을 다시 계산했다.

| 항목 | 결과 |
|---|---:|
| 고유 일별 metrics ZIP | 916 |
| 체크섬 일치 | 916 / 916 |
| 원본 행 수 | 263,678 |
| 존재하는 파일 내부에서 빠진 5분 라벨 | 130 |
| 중복 자연키 / 격자 이탈 | 0 / 0 |
| 순서 역전이 있는 파일 | 40 |
| 인접 행 순서 역전 합계 | 2,133 |
| OI 수량 또는 명목가치가 0인 날짜 | 20 |

파일의 최소·최대 날짜는 2023-01-01과 2026-09-13이다. 이 사이 모든 날짜가 있는 것은 아니다.
누락 130개도 존재하는 파일 내부 통계이며 파일 자체가 없는 날짜를 포함한 전체 기간 누락 수가 아니다.
2024년 초기 121개 파일 재검산은 기존 실험의 34,723행·누락 125개·역전 파일 27개와 일치했다.

저장된 2026-09-11~12 비교 표본 18개를 재현했다. 같은 시각에서는 수량·가치가 0/18 일치했고,
`archive label + 5분 == REST timestamp`에서는 18/18 일치했다.
새로 확보한 2026-09-14~20 UTC 자료도 같은 시각 0/2,016, 5분 이동 후 2,015/2,015 일치했다.
첫 REST 표본의 직전 아카이브 라벨은 새 검증 구간 밖이라 후자 비교에서 제외했다.

이것은 **비교한 표본에서의 사후 값 일치**다. 모든 과거 날짜의 시각 정의나 실제 공개 시각,
거래 당시 사용 가능 여부를 증명하지 않는다.

## 구현에 반영한 정책

1. `series=archive`와 `series=rest`를 분리한다. 수치가 같아도 하나의 시계열로 합치지 않는다.
2. archive의 `create_time`을 원래 UTC 라벨 그대로 보존한다. `reference_time`은 미확정이라 null이다.
   REST의 `timestamp`는 원천 문서의 통계 구간 끝 시각으로 기록한다.
3. 수량·명목가치 단위는 검증 전까지 `source_*_units_unverified`로 표시한다.
4. 0값이 있는 날짜는 통째로 격리한다. 이를 OI 감소나 0 포지션 신호로 노출하지 않는다.
5. 고유한 정상 행의 순서만 바뀐 경우 표준 데이터는 정렬하되 원본 순번과 경고를 남긴다.
6. 실제 누락은 채우지 않는다. 완전성을 요구하는 snapshot/export는 해당 구간을 거부한다.
7. `first_observed_at`은 이번 수집기가 확보한 시각이다. 옛 자료의 당시 공개 시각으로 바꾸지 않는다.
8. Tardis derivative ticker의 메시지 시각을 OI의 새 측정 시각으로 채택하지 않는다.
   이전 OI가 다음 메시지에 반복될 수 있으므로 ticker 행 수가 OI 관측 수를 의미하지 않는다.

가져오기는 원본 916일을 모두 보존하고, 유효한 896일을 게시했으며 20일은 격리했다.
896일 안에도 실제 누락은 남아 있을 수 있다. 전체를 연속하고 완전한 연구 기간이라고 표시하지 않는다.

추가 지표 결합 시 archive 시각 이동과 공개 지연을 연구자가 명시하도록 한다.
`historical_assumption`은 가정하에 과거 자료를 쓰는 모드다. `observed` 정렬 함수는 전달받은
행의 최초 수신 시각을 추가로 제한할 뿐, 저장소의 과거 revision 전체를 자동 재구성하지 않는다.
전체 시점별 데이터 버전 조회는 후속 구현 범위다.

## 재현

```bash
.venv/bin/python scripts/audit_legacy_oi.py \
  /home/yoonhc/projects/archive/oi-5m-research-complete_2026-09-19_82e28ba \
  --output reports/legacy-oi-audit.json

.venv/bin/marketdata import-legacy \
  /home/yoonhc/projects/archive/oi-5m-research-complete_2026-09-19_82e28ba \
  --start 2023-01-01 --end 2026-09-14 --datasets open_interest \
  --report reports/legacy-oi-import.json
```

후자 종료 코드 2는 격리된 날짜가 있음을 뜻한다.
상세 근거: [원본 검사 보고서](../reports/baseline/v0.1.0/legacy-oi-audit.json),
[가져오기 보고서](../reports/baseline/v0.1.0/legacy-oi-import.json), [새 비교 표본](../reports/baseline/v0.1.0/smoke.json).
