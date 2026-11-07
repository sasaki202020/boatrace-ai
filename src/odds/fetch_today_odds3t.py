from src.odds.fetch_daily_trifecta_odds import build_odds_url as build_url
from src.odds.fetch_daily_trifecta_odds import main, parse_odds_table, parse_trifecta_odds_table


__all__ = ["build_url", "main", "parse_odds_table", "parse_trifecta_odds_table"]


if __name__ == "__main__":
    main()
