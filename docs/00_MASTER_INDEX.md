# BoatRace-AI-MVP: 運用資産マスターインデックス

このプロジェクトを実戦で使いこなし、継続的に進化させるための全リソースへの入り口です。

> **最初に読む正本**: [FINAL_PRODUCT_SPEC.md](FINAL_PRODUCT_SPEC.md)
> このファイルはナビゲーション用であり、製品仕様の正本を上書きしません。

文書の権威順位は `FINAL_PRODUCT_SPEC > AGENTS execution rules > CODEX_TASKS > CONTEXT/HANDOFF historical context > reports evidence` とする。

1. [FINAL_PRODUCT_SPEC.md](FINAL_PRODUCT_SPEC.md): 製品仕様の唯一の正本
2. [AGENTS.md](../AGENTS.md): 実行ルール
3. [CODEX_TASKS.md](CODEX_TASKS.md): 作業キュー
4. [CODEX_CONTEXT.md](CODEX_CONTEXT.md) / [CODEX_HANDOFF.md](CODEX_HANDOFF.md): 履歴コンテキスト
5. `reports/**`: 状態を示す証拠

## 🗺️ 運用のロードマップ
1. **[運用仕様](FINAL_PRODUCT_SPEC.md)**: 日次フロー、安全境界、証拠ゲートを確認。
2. **[初回実行コマンド](../commands/01_first_commands.md)**: エイリアス監査とインジェクション。
3. **[Preflight runner](../scripts/run_paper_ops_preflight.bat)**: 実行前のソース準備状態を確認。
4. **[Morning runner](../scripts/run_paper_ops_morning.bat)**: 結果到着前の予想と freeze を実行。
5. **[Evening runner](../scripts/run_paper_ops_evening.bat)**: 公式結果の取込と settlement を実行。
6. **[Monitoring runner](../scripts/run_paper_ops_monitor.bat)**: 日次監視レポートを更新。

---

## 📦 運用パック一覧 (The 7 Pillars)

| パック名 | 主要ファイル / ガイド | 役割 |
| :--- | :--- | :--- |
| **0. Master** | [preflight](../scripts/run_paper_ops_preflight.bat), [morning](../scripts/run_paper_ops_morning.bat), [evening](../scripts/run_paper_ops_evening.bat), [monitor](../scripts/run_paper_ops_monitor.bat) | canonical wrapper で日次処理を段階実行 |
| **1. First Run** | [prompts/01_manager_start.txt](../prompts/01_manager_start.txt) | 初回のエイリアス不整合を確実に解消 |
| **2. Stabilization** | [stabilization_log.md](stabilization_log.md) | 実行結果を記録し、停止原因を特定・修正 |
| **3. Benchmark** | [benchmark_policy.md](benchmark_policy.md) | 変更前後を定量比較し、改善を判断 |
| **4. Daily Ops** | [daily_runbook.md](daily_runbook.md) | 毎日のルーチンと再学習の指針 |
| **5. Orchestration** | [orchestrate_daily.py](../src/orchestration/orchestrate_daily.py) | 全工程の自動化とアーカイブ |
| **6. MiroFish** | [mirofish_blueprint.md](mirofish_blueprint.md) | 高度な予測システムの再構築指針 |

## 🛡️ AI アシスタントへの「型」
困った時や拡張したい時は、以下の専用プロンプトを使用してください：
- **安全な初期プロンプト**: [safe_initial_prompts.md](safe_initial_prompts.md)
- **追加実装ガイド**: [00_safe_extension_guide.txt](../prompts/00_safe_extension_guide.txt)
- **MiroFish 再現プロンプト**: [mirofish_reproduction_prompts.md](mirofish_reproduction_prompts.md)

---
勝利への基盤は盤石です。良き航海を！
