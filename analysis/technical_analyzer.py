import pandas as pd

class TechnicalAnalyzer:
    """Provides static methods for technical analysis."""

    @staticmethod
    def add_emas(df: pd.DataFrame, periods: list[int]) -> pd.DataFrame:
        """Adds Exponential Moving Averages (EMAs) to the DataFrame."""
        for period in periods:
            df[f'EMA_{period}'] = df['close'].ewm(span=period, adjust=False).mean()
        return df
