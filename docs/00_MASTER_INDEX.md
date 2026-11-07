# BoatRace-AI-MVP: 運用資産マスターインデックス

このプロジェクトを実戦で使いこなし、継続的に進化させるための全リソースへの入り口です。

## 🗺️ 運用のロードマップ
1. **[開始ガイド](file:///c:/Users/goo10/競艇/boatrace-ai-mvp/boatrace_start_here.md)**: 実データ投入の最初の一歩。
2. **[初回実行コマンド](file:///c:/Users/goo10/競艇/boatrace-ai-mvp/commands/01_first_commands.md)**: エイリアス監査とインジェクション。
3. **[日次自動実行 (run_today.bat)](file:///c:/Users/goo10/競艇/boatrace-ai-mvp/run_today.bat)**: 毎日1クリックでレポート生成とアーカイブ。

---

## 📦 運用パック一覧 (The 7 Pillars)

| パック名 | 主要ファイル / ガイド | 役割 |
| :--- | :--- | :--- |
| **0. Master** | `master_run.py`, `run_master.bat` | パイプライン全行程を統合実行 |
| **1. First Run** | `prompts/01_manager_start.txt` | 初回のエイリアス不整合を確実に解消 |
| **2. Stabilization** | `docs/stabilization_log.md` | 実行結果を記録し、停止原因を特定・修正 |
| **3. Benchmark** | `docs/benchmark_policy.md` | 変更前後を定量比較し、改善を判断 |
| **4. Daily Ops** | `docs/daily_runbook.md` | 毎日のルーチンと再学習の指針 |
| **5. Orchestration** | `src/orchestration/orchestrate_daily.py` | 全工程の自動化とアーカイブ |
| **6. MiroFish** | `docs/mirofish_blueprint.md` | 高度な予測システムの再構築指針 |

## 🛡️ AI アシスタントへの「型」
困った時や拡張したい時は、以下の専用プロンプトを使用してください：
- **安全な初期プロンプト**: `docs/safe_initial_prompts.md`
- **追加実装ガイド**: `prompts/00_safe_extension_guide.txt`
- **MiroFish 再現プロンプト**: `docs/mirofish_reproduction_prompts.md`

---
勝利への基盤は盤石です。良き航海を！
