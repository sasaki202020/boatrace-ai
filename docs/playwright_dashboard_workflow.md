# Playwright Dashboard Workflow

`src/web/app.py` のローカルダッシュボードを Playwright で開き、必要に応じてフィルタ適用と運用ボタン実行まで回すための手順です。

## スクリプト

- [scripts/dashboard_workflow.js](/C:/Users/goo10/競艇/boatrace-ai-mvp/scripts/dashboard_workflow.js)

## 既定動作

- `DASHBOARD_URL` があればその URL を使う
- なければ `http://127.0.0.1:8090` を使う
- 実サーバーが無い場合は `WORKFLOW_USE_MOCK=1` で自己完結の smoke test を回せる
- ダッシュボードを開く
- `更新` を押す
- 画面状態を `output/playwright/dashboard-workflow.png` に保存
- 実行結果を `output/playwright/dashboard-workflow.json` に保存

## 実行コマンド

PowerShell から:

```powershell
cd C:\Users\goo10\競艇\boatrace-ai-mvp
node scripts\dashboard_workflow.js
```

## 運用ボタンを回す

`WORKFLOW_MODE` を指定すると、ダッシュボード上の運用ボタンも実行します。

例:

```powershell
$env:WORKFLOW_MODE = 'guard'
node scripts\dashboard_workflow.js
```

自動起動を許可するなら:

```powershell
$env:WORKFLOW_START_SERVER = '1'
node scripts\dashboard_workflow.js
```

mock で確実に通すなら:

```powershell
$env:WORKFLOW_USE_MOCK = '1'
$env:WORKFLOW_MODE = 'guard'
node scripts\dashboard_workflow.js
```

使える値:

- `refresh`
- `predict`
- `pre-race`
- `odds-refresh`
- `post-race`
- `backtest`
- `guard`
- `full`

## フィルタ指定

必要なら以下も指定できます。

- `WORKFLOW_DATE=2026-04-19`
- `WORKFLOW_VENUE=住之江`
- `WORKFLOW_DECISION=BUY`
- `WORKFLOW_LIMIT=50`
- `WORKFLOW_HEADLESS=0` で headed 実行
- `WORKFLOW_TIMEOUT_MS=900000` で待機時間を変更
- `DASHBOARD_URL=http://127.0.0.1:8091` のように実ダッシュボードの URL を指定

## 期待する完了条件

- ページが開く
- `待機中` が確認できる
- 指定したボタンが実行される
- 実行後に `待機中` に戻る
- スクリーンショットと JSON が保存される
