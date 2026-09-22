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

수집·검증 스크립트가 생성하는 `reports/` 바로 아래의 보고서, 테스트 XML, 로그는
로컬 실행 결과로 Git에서 제외합니다. 최초 페이지 분할 오류를 기록한
`bootstrap-initial-2026-09-14_21.json`도 로컬에 보존하되 baseline에는 최종 검증 결과를 넣었습니다.

Git에는 시장 데이터·SQLite·백테스트 결과 ZIP을 포함하지 않습니다. manifest와 해시는
입력을 식별하는 근거이며 원본 파일의 백업을 대신하지 않습니다. 같은 연구를 재현하려면
해당 `data/`와 일관된 `state/` 백업도 함께 보관해야 합니다.
