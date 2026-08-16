# Prospective Commit-Reveal Runbook

## Safety

`tree_15`、feature schema、production、BUY/EV、betting、paymentを変更しない。実サイトへ接続しない。入力は人が配置したローカルファイルだけを使用する。

## Daily sequence

1. race date、6艇、result field不在、model/schema hashを確認する。
2. 正確なrace開始時刻を証明できなければrace date 00:00 JSTをcutoffとする。
3. `prepare_shadow_commit_v2.py`で秘密package、32-byte salt、public anchor JSON、local ledger recordを作る。
4. `prepare_day1_readiness_v2.py --audit-only`でnetwork writeなしの監査を行う。外部commitは承認manifestと明示confirmationを満たす専用経路だけを使用する。
5. receiptを保存し、allowlist、body hash、server created_at、updated_atを検証する。
6. cutoff後にreveal bundleを作る。raw inputは含めない。
7. 結果は別package/tableへappendする。predictionを再生成・変更しない。

`created_at < cutoff`のみverified prospectiveへ含める。同時刻と締切後は`LATE_COMMIT_REJECTED`。
