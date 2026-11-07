import argparse
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description="Build exacta-mode buy/skip predictions from skip_decisions.csv")
    parser.add_argument(
        "--input",
        default="data/strategy_outputs/skip_decisions.csv",
        help="Input prediction CSV path",
    )
    parser.add_argument(
        "--output",
        default="data/strategy_outputs/skip_decisions_exacta_mode.csv",
        help="Output prediction CSV path",
    )
    parser.add_argument("--min-first-win-proba", type=float, default=0.1673)
    parser.add_argument("--min-approx-prob", type=float, default=0.1552)
    parser.add_argument("--min-ev", type=float, default=34.82)
    parser.add_argument("--tag", default="exacta_mode_filter(v1)")
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    df = pd.read_csv(in_path)

    before_buy = int((df["decision"] == "BUY").sum())
    buy_mask = df["decision"].eq("BUY")
    cond = (
        pd.to_numeric(df["first_win_proba"], errors="coerce").ge(args.min_first_win_proba)
        & pd.to_numeric(df["approx_prob"], errors="coerce").ge(args.min_approx_prob)
        & pd.to_numeric(df["ev"], errors="coerce").ge(args.min_ev)
    )
    keep_buy = buy_mask & cond

    df.loc[buy_mask & ~cond, "decision"] = "SKIP"
    if "reason" in df.columns:
        df.loc[keep_buy, "reason"] = df.loc[keep_buy, "reason"].astype(str) + f" / {args.tag}"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)

    after_buy = int((df["decision"] == "BUY").sum())
    print(f"[saved] {out_path}")
    print(f"[buy] before={before_buy} after={after_buy}")
    print(
        f"[filter] first_win_proba>={args.min_first_win_proba}, "
        f"approx_prob>={args.min_approx_prob}, ev>={args.min_ev}"
    )


if __name__ == "__main__":
    main()
