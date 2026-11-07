# Ingestion Mapping Dictionary (Draft)

公式のダウンロードファイル形式から `historical_races.csv` への最小マッピング。

## 1. 番組表 (KBNファイル等) -> Pre-Race
| 目標列 | 公式TXT位置/キー | 備考 |
| :--- | :--- | :--- |
| race_id | 日付 + 場コード + R | 自動生成 |
| lane | 艇番 | 1-6 |
| racer_id | 登録番号 | |
| racer_class | 級別 | A1-B2 |
| avg_st | 平均ST | |
| national_win_rate | 全国勝率 | |

## 2. 競走成績 (KSEファイル等) -> Post-Race
| 目標列 | 公式TXT位置/キー | 備考 |
| :--- | :--- | :--- |
| finish_position | 着順 | 1-6, 特殊文字(S, L等)は要正規化 |
| win_label | 着順=1 なら 1 | |

## 3. 直前情報 (Web/API) -> Just-Before
| 目標列 | キー名 | 備考 |
| :--- | :--- | :--- |
| exhibition_time | 展示タイム | |
| wind_speed | 風速 | |
| weather | 天候 | |
| wave_height | 波高 | |

## 正規化ルール
- **場名**: コード(01-24)を漢字名に変換。
- **着順**: '01' -> 1, 'L', 'S', 'F' -> 欠損/除外フラグ。
- **日付**: YYYYMMDD -> YYYY-MM-DD。
