import pandas as pd
import numpy as np

class Normalizer:
    """
    データ値をスキーマ定義に合わせて正規化する。
    """
    def normalize_results(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        着順等の値を数値化
        """
        if 'finish_position' in df.columns:
            # '01' -> 1 変換, L/S/Fなどは一旦NaN
            df['finish_position'] = pd.to_numeric(df['finish_position'], errors='coerce')
            df['win_label'] = (df['finish_position'] == 1).astype(int)
        return df

    def convert_types(self, df: pd.DataFrame) -> pd.DataFrame:
        # 型変換
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        return df
