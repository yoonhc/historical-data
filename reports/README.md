# 검증 기록

`baseline/v0.1.0/`은 첫 버전의 검토된 검증 근거입니다. 이 디렉터리의 기록은
그대로 보존하고, 다음 기준 버전을 만들 때 새 버전 디렉터리에 기록을 추가합니다.

| 파일 | 내용 |
|---|---|
| `validation.json` | 34개 테스트·린트·무결성 검사와 검증한 소스 파일 해시 |
| `bootstrap-2026-09-14_21.json` | 고정 7일 구간의 데이터별 수집 결과와 완전성 |
| `smoke.json` | 스냅샷·출력 ID, 반복 내보내기, OI 시각 비교 |
| `freqtrade-loader.json` | Freqtrade 2026.8의 실제 파일 로딩 검사 |
| `freqtrade-backtests.json` | 현물·선물 백테스트 결과 파일 해시와 연동 검증 |
| `legacy-oi-audit.json` | 기존 OI 원본 916개에 대한 독립 감사 |
| `legacy-oi-import.json` | 원본 가져오기와 20일 격리 기록 |
| `snapshot-manifest.json` | 연구 입력의 정확한 revision·파일 해시·품질 상태 |
| `export-manifest.json` | Freqtrade 출력 파일·형식·변환 규칙·해시 |

검증 구간은 UTC `[2026-09-14, 2026-09-21)`입니다. 보고서에 기록된 실행 시각과
파일 해시는 실제 검증 당시의 값이며, baseline을 정리한 시각으로 덮어쓰지 않았습니다.
`snapshot-manifest.json`의 SHA-256은 보고서의 `snapshot_id`와 일치합니다.

`baseline/operations-2026-09-22/`는 자동 수집 활성화 시점의 별도 검증 기록입니다.
`validation.json`에 56개 테스트, 파일 무결성 검사, 타이머의 실제 실행 결과, OI 서비스
재시작 전후 검증과 소스 해시를 기록했습니다. `status.json`은 검증 당시 9개 데이터셋의
운영 범위·누락·최신 시각과 기존 OI의 격리 20일을 보존합니다. 첫 버전의 검증 자료는
수정하지 않았으며, 현재 상태는 고정 보고서 대신 `marketdata status`로 확인합니다.

`baseline/research-2023_2026/`은 UTC `[2023-09-22, 2026-09-22)`의 과거 수집과
연구 구간 검증 기록입니다. [요약](baseline/research-2023_2026/summary.md)에서 데이터별 수량과
미해결 누락, 월별 CSV, 구간별 스냅샷 ID를 확인합니다. 초기 수집 보고서와 별도 원천 복구 결과를
구분하며 `quality.json`이 복구 후 최종 상태입니다. 19개 스냅샷의 전체 manifest는 로컬
`data/snapshots/`에 보존하고, Git에는 ID·범위·크기·검증 결과를 담은 인덱스를 보관합니다.
이 단계는 전략 성과나 OI 공개 시각의 검증을 포함하지 않습니다.

`baseline/freqtrade-userdata-2026-09-22/`는 기존 `ft_userdata` Docker에 연결한 검증 기록입니다.
[요약](baseline/freqtrade-userdata-2026-09-22/summary.md)에 4개 출력, 실제 native 파일 29개 검사,
4개 백테스트, 20개 신호의 lookahead 분석, 테스트 78개와 기존 설정 보존 확인을 기록했습니다.
출력별 manifest와 실행별 명령·이미지·설정·결과 해시를 함께 보존합니다.

수집·검증 스크립트가 생성하는 `reports/` 바로 아래의 보고서, 테스트 XML, 로그는
로컬 실행 결과로 Git에서 제외합니다. 최초 페이지 분할 오류를 기록한
`bootstrap-initial-2026-09-14_21.json`도 로컬에 보존하되 baseline에는 최종 검증 결과를 넣었습니다.

`baseline/oi-quality-2026-09-22/`는 OI 관측·일별 품질 보고서의 활성화 기록입니다.
[요약](baseline/oi-quality-2026-09-22/summary.md)에 테스트 95개, 7일의 출처 간 값 비교,
v2 관측 42회, 초기 요청 경계 보정 근거와 실제 타이머 실행 결과를 기록했습니다.
최신 누적 결과는 고정 기준 기록 대신 `reports/oi-quality/latest.json`으로 확인합니다.

Git에는 시장 데이터·SQLite·백테스트 결과 ZIP을 포함하지 않습니다. manifest와 해시는
입력을 식별하는 근거이며 원본 파일의 백업을 대신하지 않습니다. 같은 연구를 재현하려면
해당 `data/`와 일관된 `state/` 백업도 함께 보관해야 합니다.
