# 정오에 완료된 네 daily 대상의 실행 결과와 제공 방식

**9/23–10/10 정오 18일·90회에서는 현물 봉·펀딩 이력·현물/선물 종목 정보가 각각 90회 모두 complete였다.**
매일 최초 실행부터 완료됐으며, 전체 daily를 미완료로 만든 것은 별도의 선물 아카이브 네 종류였다.
이번 결과는 저장된 실행 보고서의 완료 계약을 대조한 것이며 모든 과거 데이터 파일의 독립 전수 검사가 아니다.

| 대상 | 정오 90회 | 추가 실행을 포함한 107회 |
|---|---|---|
| 현물 BTCUSDT 1분봉 | complete 90 | complete 100, partial 7 |
| 선물 펀딩 이력 | complete 90 | complete 107 |
| 현물 종목 정보 | complete 90 | complete 107 |
| 선물 종목 정보 | complete 90 | complete 107 |

107회는 [기존 조사](../daily-schedule-history-2026-10-10/summary.md)에서 동일한 여덟 대상이 함께 실행된
9/22–10/10의 묶음이다. 별도 CLI·개별 대상 실행 전체나 HTTP 요청의 무오류율로 확대하지 않는다.
이번 카탈로그 조회에서도 그 조사 이후 새 현물 daily 실행은 없었다.

현물의 partial 7회는 모두 **9/27 09:35–09:46 KST**의 추가 실행이다.
9/26 UTC ZIP의 마지막 저장 404는 **09:46:36**, 최초 저장 200은 **12:21:43 KST**였다.
따라서 정오에는 일관되게 성공했지만, 더 이른 조회까지 항상 제공됐다고 말할 수 없다.

## 현물 ZIP의 시각

9/22–10/9 UTC의 현물 ZIP과 CHECKSUM **36개 URL**에 대해 기존 카탈로그의 최초 성공 응답 헤더를 읽었다.
날짜별 두 파일의 Last-Modified 중 늦은 값을 사용했다. 모두 대상 UTC 날짜의 다음 KST 날짜였다.

| 항목 | KST |
|---|---|
| 가장 이른 수정 시각 | 10:26:12 |
| 중앙값 | 10:45:38 |
| 평균 | 10:56:00 |
| 가장 늦은 수정 시각 | 11:40:40 |

이는 **처음 받았던 파일의 수정 시각 분포**다. 정확한 최초 조회 가능 시각이나 고정 공개 시각이 아니다.
현재 원격 목록이나 ZIP 본문을 추가 요청하지 않았다. 실제로 확인된 성공은 정오 조회 18일이며,
9/27 외에는 첫 성공 이전의 404를 확보하지 못했다.
Binance 공식 안내도 일별 자료를 다음 날 제공한다고 설명하고 고정된 시·분을 보장하지 않는다.
[공식 공개 데이터 안내](https://github.com/binance/binance-public-data#binance-public-data).

## 나머지 세 대상의 시각

- 펀딩은 `/fapi/v1/fundingRate`에서 이력을 조회한다. 일별 ZIP 공개 대기가 아니며,
  응답의 `fundingTime`과 그 항목이 처음 API에 나타난 시각은 구분한다. 후자는 이번에 측정하지 않았다.
  프로젝트의 complete는 원천 조회 범위를 충족했다는 판정이며 모든 펀딩 사건의 독립적인 존재 증명은 아니다.
  [공식 펀딩 이력 API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#get-funding-rate-history).
- 현물·선물 종목 정보는 각각 `/api/v3/exchangeInfo`, `/fapi/v1/exchangeInfo`의 현재 거래 규칙과
  종목 정보를 조회한다. 고정된 하루 한 번의 공개를 기다리는 자료가 아니다.
  코드의 `current_observation_only`처럼 조회 당시 관측이며 하루 전체의 변경 이력을 복원했다는 뜻은 아니다.
  [현물 API](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/general#exchange-information),
  [선물 API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#exchange-information).

[카운트·예외 실행·36개 원본 응답 헤더](evidence.json)와 [재현 코드](review.py)에 근거를 남겼다.
기존 카탈로그는 읽기 전용으로 조회했다. 예약·재시도·운영 수집·백업 설정을 변경하거나 관측기를 활성화하지 않았다.
