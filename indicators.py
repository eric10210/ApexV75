"""
indicators.py — Full indicator engine.
Calculates all indicators using pandas-ta on a candle DataFrame.
"""
import logging
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

try:
    import pandas_ta as ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False
    log.warning("pandas-ta not installed — using manual indicator calculations.")


def safe(fn, default=np.nan):
    try:
        return fn()
    except Exception:
        return default


class IndicatorEngine:
    """
    Given a candle DataFrame (open, high, low, close, volume),
    calculate all indicators and return a flat dict.
    """

    def calculate(self, df: pd.DataFrame, tf: str = "M5") -> dict:
        if df is None or len(df) < 30:
            return {}

        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        r = {"tf": tf}

        try:
            r.update(self._trend(df))
            r.update(self._momentum(df))
            r.update(self._volatility(df))
            r.update(self._volume(df))
            r.update(self._ichimoku(df))
            r.update(self._price_action(df))
        except Exception as e:
            log.error(f"Indicator error on {tf}: {e}", exc_info=True)

        return r

    # ── Trend ─────────────────────────────────────────────────────────────────
    def _trend(self, df: pd.DataFrame) -> dict:
        c = df["close"]
        r = {}

        # EMAs
        for p in [8, 21, 50, 200]:
            val = safe(lambda p=p: float(ta.ema(c, length=p).iloc[-1]) if TA_AVAILABLE
                       else float(c.ewm(span=p, adjust=False).mean().iloc[-1]))
            r[f"ema{p}"] = val

        # EMA stack
        if all(f"ema{p}" in r and not np.isnan(r[f"ema{p}"]) for p in [8,21,50,200]):
            r["ema_stack_bull"] = r["ema8"] > r["ema21"] > r["ema50"] > r["ema200"]
            r["ema_stack_bear"] = r["ema8"] < r["ema21"] < r["ema50"] < r["ema200"]
            r["ema_cross_bull"] = self._cross_up(
                c.ewm(span=8,adjust=False).mean(),
                c.ewm(span=21,adjust=False).mean()
            )
            r["ema_cross_bear"] = self._cross_down(
                c.ewm(span=8,adjust=False).mean(),
                c.ewm(span=21,adjust=False).mean()
            )
        else:
            r["ema_stack_bull"] = False
            r["ema_stack_bear"] = False

        # Price vs EMA200
        r["price_above_ema200"] = float(c.iloc[-1]) > r.get("ema200", 0) if not np.isnan(r.get("ema200",np.nan)) else None

        # MACD
        if TA_AVAILABLE and len(df) >= 35:
            macd_df = ta.macd(c, fast=12, slow=26, signal=9)
            if macd_df is not None and len(macd_df) > 0:
                r["macd_line"]   = safe(lambda: float(macd_df.iloc[-1, 0]))
                r["macd_signal"] = safe(lambda: float(macd_df.iloc[-1, 2]))
                r["macd_hist"]   = safe(lambda: float(macd_df.iloc[-1, 1]))
                r["macd_hist_prev"] = safe(lambda: float(macd_df.iloc[-2, 1]))
                r["macd_cross_bull"] = (r["macd_hist"] > 0 and r["macd_hist_prev"] <= 0)
                r["macd_cross_bear"] = (r["macd_hist"] < 0 and r["macd_hist_prev"] >= 0)
                r["macd_bull"] = r["macd_hist"] > 0
                r["macd_bear"] = r["macd_hist"] < 0
                r["macd_expanding"] = abs(r["macd_hist"]) > abs(r["macd_hist_prev"])
        else:
            r["macd_bull"] = False
            r["macd_bear"] = False

        # ADX
        if TA_AVAILABLE and len(df) >= 20:
            adx_df = ta.adx(df["high"], df["low"], c, length=14)
            if adx_df is not None and len(adx_df) > 0:
                r["adx"]   = safe(lambda: float(adx_df.iloc[-1, 0]))
                r["di_pos"] = safe(lambda: float(adx_df.iloc[-1, 1]))
                r["di_neg"] = safe(lambda: float(adx_df.iloc[-1, 2]))
                r["adx_trending"]  = r["adx"] > 25 if not np.isnan(r["adx"]) else False
                r["adx_strong"]    = r["adx"] > 40 if not np.isnan(r["adx"]) else False
                r["adx_ranging"]   = r["adx"] < 20 if not np.isnan(r["adx"]) else True
                r["adx_bull_dir"]  = r.get("di_pos",0) > r.get("di_neg",0)
                r["adx_bear_dir"]  = r.get("di_neg",0) > r.get("di_pos",0)
        else:
            r["adx"] = np.nan
            r["adx_ranging"] = True
            r["adx_trending"] = False

        return r

    # ── Momentum ──────────────────────────────────────────────────────────────
    def _momentum(self, df: pd.DataFrame) -> dict:
        c = df["close"]
        r = {}

        # RSI
        if TA_AVAILABLE:
            rsi_s = ta.rsi(c, length=14)
            r["rsi"] = safe(lambda: float(rsi_s.iloc[-1]))
            r["rsi_prev"] = safe(lambda: float(rsi_s.iloc[-2]))
        else:
            delta = c.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rs    = gain / loss.replace(0, np.nan)
            rsi_s = 100 - (100 / (1 + rs))
            r["rsi"]      = safe(lambda: float(rsi_s.iloc[-1]))
            r["rsi_prev"] = safe(lambda: float(rsi_s.iloc[-2]))

        rsi = r.get("rsi", 50)
        r["rsi_oversold"]   = rsi <= 30 if not np.isnan(rsi) else False
        r["rsi_overbought"] = rsi >= 70 if not np.isnan(rsi) else False
        r["rsi_bull"]       = rsi < 40  if not np.isnan(rsi) else False
        r["rsi_bear"]       = rsi > 60  if not np.isnan(rsi) else False
        r["rsi_cross50_up"] = self._cross_up(rsi_s, pd.Series([50]*len(rsi_s), index=rsi_s.index))
        r["rsi_cross50_dn"] = self._cross_down(rsi_s, pd.Series([50]*len(rsi_s), index=rsi_s.index))

        # RSI divergence (last 20 bars)
        r["rsi_bull_div"], r["rsi_bear_div"] = self._detect_divergence(c, rsi_s, lookback=20)

        # Stochastic
        if TA_AVAILABLE and len(df) >= 20:
            stoch_df = ta.stoch(df["high"], df["low"], c, k=14, d=3, smooth_k=3)
            if stoch_df is not None and len(stoch_df) > 0:
                r["stoch_k"]      = safe(lambda: float(stoch_df.iloc[-1, 0]))
                r["stoch_d"]      = safe(lambda: float(stoch_df.iloc[-1, 1]))
                r["stoch_k_prev"] = safe(lambda: float(stoch_df.iloc[-2, 0]))
                r["stoch_d_prev"] = safe(lambda: float(stoch_df.iloc[-2, 1]))
                k, d = r["stoch_k"], r["stoch_d"]
                r["stoch_oversold"]   = k < 20 if not np.isnan(k) else False
                r["stoch_overbought"] = k > 80 if not np.isnan(k) else False
                r["stoch_cross_bull"] = (r["stoch_k_prev"] < r["stoch_d_prev"] and k > d
                                         and k < 30)
                r["stoch_cross_bear"] = (r["stoch_k_prev"] > r["stoch_d_prev"] and k < d
                                         and k > 70)
                # Divergence
                if not stoch_df.empty:
                    r["stoch_bull_div"], r["stoch_bear_div"] = self._detect_divergence(
                        c, stoch_df.iloc[:, 0], lookback=20)
        return r

    # ── Volatility ────────────────────────────────────────────────────────────
    def _volatility(self, df: pd.DataFrame) -> dict:
        c   = df["close"]
        h   = df["high"]
        l   = df["low"]
        r   = {}

        # ATR
        if TA_AVAILABLE:
            atr_s = ta.atr(h, l, c, length=14)
            r["atr14"] = safe(lambda: float(atr_s.iloc[-1]))
        else:
            tr = pd.concat([h - l,
                            (h - c.shift(1)).abs(),
                            (l - c.shift(1)).abs()], axis=1).max(axis=1)
            r["atr14"] = float(tr.rolling(14).mean().iloc[-1])

        # Volatility regime
        atr = r.get("atr14", 250)
        if np.isnan(atr): atr = 250
        if atr < 150:
            r["regime"] = "LOW"
        elif atr > 400:
            r["regime"] = "HIGH"
        else:
            r["regime"] = "MEDIUM"

        # Bollinger Bands
        if TA_AVAILABLE:
            bb = ta.bbands(c, length=20, std=2.0)
            if bb is not None and len(bb) > 0:
                r["bb_upper"]  = safe(lambda: float(bb.iloc[-1, 0]))
                r["bb_mid"]    = safe(lambda: float(bb.iloc[-1, 1]))
                r["bb_lower"]  = safe(lambda: float(bb.iloc[-1, 2]))
                r["bb_width"]  = safe(lambda: float(bb.iloc[-1, 3]))
                r["bb_pct"]    = safe(lambda: float(bb.iloc[-1, 4]))
                # Rolling average width for squeeze detection
                if len(bb) >= 20:
                    r["bb_avg_width"] = float(bb.iloc[-20:, 3].mean())
                    r["bb_squeeze"]   = r["bb_width"] < r["bb_avg_width"] * 0.5
                # Band touch
                price = float(c.iloc[-1])
                r["bb_touch_lower"] = price <= r.get("bb_lower", 0) * 1.001
                r["bb_touch_upper"] = price >= r.get("bb_upper", 0) * 0.999
                r["bb_riding_upper"] = float(c.iloc[-1]) > r.get("bb_upper", float("inf")) * 0.998
                r["bb_riding_lower"] = float(c.iloc[-1]) < r.get("bb_lower", 0) * 1.002

        # Keltner Channels
        if TA_AVAILABLE and len(df) >= 20:
            kc = ta.kc(h, l, c, length=20, scalar=2.0)
            if kc is not None and len(kc) > 0:
                r["kc_upper"] = safe(lambda: float(kc.iloc[-1, 0]))
                r["kc_lower"] = safe(lambda: float(kc.iloc[-1, 2]))
                # TTM Squeeze: BB inside KC
                if all(k in r for k in ["bb_upper","bb_lower","kc_upper","kc_lower"]):
                    r["ttm_squeeze"] = (r["bb_upper"] < r["kc_upper"] and
                                        r["bb_lower"] > r["kc_lower"])

        # Autocorrelation (lag-1, last 50 bars)
        if len(c) >= 50:
            c50 = c.tail(50)
            try:
                ac = float(c50.autocorr(lag=1))
                r["autocorr"] = ac
                if ac > 0.3:
                    r["auto_regime"] = "TRENDING"
                elif ac < -0.1:
                    r["auto_regime"] = "MEAN_REVERTING"
                else:
                    r["auto_regime"] = "NEUTRAL"
            except Exception:
                r["auto_regime"] = "NEUTRAL"

        return r

    # ── Volume Profile ────────────────────────────────────────────────────────
    def _volume(self, df: pd.DataFrame) -> dict:
        r = {}
        if len(df) < 20:
            return r
        try:
            # VWAP (rolling session approximation)
            tp = (df["high"] + df["low"] + df["close"]) / 3
            vol = df["volume"] if "volume" in df.columns else pd.Series(1, index=df.index)
            vwap = (tp * vol).cumsum() / vol.cumsum()
            r["vwap"] = float(vwap.iloc[-1])
            r["price_above_vwap"] = float(df["close"].iloc[-1]) > r["vwap"]

            # Simple POC (price level with most ticks)
            bins = 50
            hist, edges = np.histogram(df["close"].values, bins=bins)
            poc_idx = int(np.argmax(hist))
            r["poc"] = float((edges[poc_idx] + edges[poc_idx+1]) / 2)

            # Value area (70% of volume)
            total = hist.sum()
            cum   = 0
            indices = np.argsort(hist)[::-1]
            va_indices = []
            for i in indices:
                va_indices.append(i)
                cum += hist[i]
                if cum / total >= 0.70:
                    break
            r["vah"] = float(edges[max(va_indices)+1])
            r["val"] = float(edges[min(va_indices)])
        except Exception as e:
            log.debug(f"Volume profile error: {e}")
        return r

    # ── Ichimoku ──────────────────────────────────────────────────────────────
    def _ichimoku(self, df: pd.DataFrame) -> dict:
        r = {}
        if len(df) < 60:
            return r
        try:
            h, l, c = df["high"], df["low"], df["close"]
            tenkan = (h.rolling(9).max() + l.rolling(9).min()) / 2
            kijun  = (h.rolling(26).max() + l.rolling(26).min()) / 2
            span_a = ((tenkan + kijun) / 2)
            span_b = (h.rolling(52).max() + l.rolling(52).min()) / 2
            chikou = c.shift(-26)

            r["tenkan"]  = float(tenkan.iloc[-1])
            r["kijun"]   = float(kijun.iloc[-1])
            r["span_a"]  = float(span_a.iloc[-1])
            r["span_b"]  = float(span_b.iloc[-1])

            price = float(c.iloc[-1])
            cloud_top    = max(r["span_a"], r["span_b"])
            cloud_bottom = min(r["span_a"], r["span_b"])

            r["price_above_cloud"]  = price > cloud_top
            r["price_below_cloud"]  = price < cloud_bottom
            r["price_in_cloud"]     = cloud_bottom <= price <= cloud_top
            r["cloud_bull"]         = r["span_a"] > r["span_b"]
            r["cloud_bear"]         = r["span_a"] < r["span_b"]
            r["tenkan_bull"]        = r["tenkan"] > r["kijun"]
            r["tenkan_bear"]        = r["tenkan"] < r["kijun"]
            r["ichi_full_bull"]     = (r["price_above_cloud"] and r["tenkan_bull"] and r["cloud_bull"])
            r["ichi_full_bear"]     = (r["price_below_cloud"] and r["tenkan_bear"] and r["cloud_bear"])

            # Kumo twist (upcoming cloud flip)
            if len(span_a) > 26 and len(span_b) > 26:
                fut_a = float(span_a.iloc[-1])
                fut_b = float(span_b.iloc[-1])
                prev_fut_a = float(span_a.iloc[-2])
                prev_fut_b = float(span_b.iloc[-2])
                r["kumo_twist_bull"] = (prev_fut_a <= prev_fut_b and fut_a > fut_b)
                r["kumo_twist_bear"] = (prev_fut_a >= prev_fut_b and fut_a < fut_b)
        except Exception as e:
            log.debug(f"Ichimoku error: {e}")
        return r

    # ── Price Action ──────────────────────────────────────────────────────────
    def _price_action(self, df: pd.DataFrame) -> dict:
        r = {}
        if len(df) < 5:
            return r
        try:
            c = df["close"]
            o = df["open"]
            h = df["high"]
            l = df["low"]

            # Current candle properties
            body  = abs(float(c.iloc[-1]) - float(o.iloc[-1]))
            wick_h = float(h.iloc[-1]) - max(float(c.iloc[-1]), float(o.iloc[-1]))
            wick_l = min(float(c.iloc[-1]), float(o.iloc[-1])) - float(l.iloc[-1])
            r["body"]    = body
            r["wick_h"]  = wick_h
            r["wick_l"]  = wick_l
            r["is_bull"] = float(c.iloc[-1]) > float(o.iloc[-1])
            r["is_bear"] = float(c.iloc[-1]) < float(o.iloc[-1])

            # Candlestick patterns
            patterns = self._detect_patterns(df)
            r.update(patterns)

            # Market structure (last 20 bars)
            r.update(self._market_structure(df))

            # Support / Resistance (key swing levels)
            r.update(self._swing_levels(df))

            # Fibonacci
            r.update(self._fibonacci(df))

            # Pivot points
            r.update(self._pivots(df))

            # Order blocks
            r.update(self._order_blocks(df))

            # Fair Value Gaps
            r.update(self._fvg(df))

            # Liquidity levels (equal highs/lows)
            r.update(self._liquidity(df))

        except Exception as e:
            log.debug(f"Price action error: {e}")
        return r

    def _detect_patterns(self, df: pd.DataFrame) -> dict:
        r = {"pattern": "None", "pattern_score": 0, "pattern_bull": False, "pattern_bear": False}
        if len(df) < 4:
            return r
        try:
            o = df["open"].values
            h = df["high"].values
            l = df["low"].values
            c = df["close"].values

            i = -1   # current (last) candle
            body_curr  = abs(c[i] - o[i])
            body_prev  = abs(c[i-1] - o[i-1])
            wick_h     = h[i] - max(c[i], o[i])
            wick_l     = min(c[i], o[i]) - l[i]
            atr_proxy  = np.mean([h[j]-l[j] for j in range(-10, 0)]) if len(df) >= 10 else 200

            # Bullish Engulfing
            if (c[i-1] < o[i-1] and c[i] > o[i] and
                c[i] > o[i-1] and o[i] < c[i-1] and
                body_curr > body_prev):
                r["pattern"] = "Bullish Engulfing"
                r["pattern_score"] = 10
                r["pattern_bull"] = True
                return r

            # Bearish Engulfing
            if (c[i-1] > o[i-1] and c[i] < o[i] and
                c[i] < o[i-1] and o[i] > c[i-1] and
                body_curr > body_prev):
                r["pattern"] = "Bearish Engulfing"
                r["pattern_score"] = 10
                r["pattern_bear"] = True
                return r

            # Hammer / Pin Bar (bullish)
            if (wick_l >= 2 * body_curr and wick_h <= 0.3 * body_curr and
                body_curr > 0 and wick_l >= 0.5 * atr_proxy * 0.3):
                r["pattern"] = "Hammer / Pin Bar"
                r["pattern_score"] = 7
                r["pattern_bull"] = True
                return r

            # Shooting Star (bearish)
            if (wick_h >= 2 * body_curr and wick_l <= 0.3 * body_curr and
                body_curr > 0 and wick_h >= 0.5 * atr_proxy * 0.3):
                r["pattern"] = "Shooting Star"
                r["pattern_score"] = 7
                r["pattern_bear"] = True
                return r

            # Doji
            if body_curr < atr_proxy * 0.05:
                r["pattern"] = "Doji"
                r["pattern_score"] = 3
                return r

            # Morning Star (3-candle bullish)
            if len(df) >= 3:
                if (c[i-2] < o[i-2] and abs(c[i-1]-o[i-1]) < body_curr*0.3
                        and c[i] > o[i] and c[i] > (o[i-2]+c[i-2])/2):
                    r["pattern"] = "Morning Star"
                    r["pattern_score"] = 10
                    r["pattern_bull"] = True
                    return r

                # Evening Star (3-candle bearish)
                if (c[i-2] > o[i-2] and abs(c[i-1]-o[i-1]) < body_curr*0.3
                        and c[i] < o[i] and c[i] < (o[i-2]+c[i-2])/2):
                    r["pattern"] = "Evening Star"
                    r["pattern_score"] = 10
                    r["pattern_bear"] = True
                    return r

            # Inside Bar
            if h[i] < h[i-1] and l[i] > l[i-1]:
                r["pattern"] = "Inside Bar"
                r["pattern_score"] = 5
                return r

            # Marubozu
            if wick_h < atr_proxy*0.03 and wick_l < atr_proxy*0.03 and body_curr > atr_proxy*0.5:
                if c[i] > o[i]:
                    r["pattern"] = "Bullish Marubozu"
                    r["pattern_score"] = 7
                    r["pattern_bull"] = True
                else:
                    r["pattern"] = "Bearish Marubozu"
                    r["pattern_score"] = 7
                    r["pattern_bear"] = True
        except Exception:
            pass
        return r

    def _market_structure(self, df: pd.DataFrame) -> dict:
        r = {"structure_bull": False, "structure_bear": False, "structure": "Unknown"}
        if len(df) < 10:
            return r
        try:
            h = df["high"].values[-20:]
            l = df["low"].values[-20:]
            c = df["close"].values[-20:]

            # Find last 2 swing highs and lows
            sh, sl = [], []
            for i in range(1, len(h)-1):
                if h[i] > h[i-1] and h[i] > h[i+1]:
                    sh.append(h[i])
                if l[i] < l[i-1] and l[i] < l[i+1]:
                    sl.append(l[i])

            if len(sh) >= 2 and len(sl) >= 2:
                if sh[-1] > sh[-2] and sl[-1] > sl[-2]:
                    r["structure"] = "HH/HL Uptrend"
                    r["structure_bull"] = True
                elif sh[-1] < sh[-2] and sl[-1] < sl[-2]:
                    r["structure"] = "LH/LL Downtrend"
                    r["structure_bear"] = True
                else:
                    r["structure"] = "Ranging"

            # BOS / ChoCH detection
            if sh and sl:
                last_swing_high = sh[-1]
                last_swing_low  = sl[-1]
                price = float(c[-1])
                r["bos_bull"]  = price > last_swing_high and r["structure_bull"]
                r["bos_bear"]  = price < last_swing_low  and r["structure_bear"]
                r["choch_bull"] = price > last_swing_high and r["structure_bear"]
                r["choch_bear"] = price < last_swing_low  and r["structure_bull"]

        except Exception:
            pass
        return r

    def _swing_levels(self, df: pd.DataFrame) -> dict:
        r = {}
        if len(df) < 20:
            return r
        try:
            h = df["high"].values
            l = df["low"].values
            # Recent swing high and low
            r["swing_high"] = float(max(h[-20:]))
            r["swing_low"]  = float(min(l[-20:]))
            r["range_pts"]  = r["swing_high"] - r["swing_low"]
        except Exception:
            pass
        return r

    def _fibonacci(self, df: pd.DataFrame) -> dict:
        r = {}
        if len(df) < 30:
            return r
        try:
            h50 = float(df["high"].tail(50).max())
            l50 = float(df["low"].tail(50).min())
            diff = h50 - l50
            r["fib_swing_high"] = h50
            r["fib_swing_low"]  = l50

            # Retracement levels (from high)
            for pct, label in [(0.236,"236"), (0.382,"382"), (0.500,"500"),
                                (0.618,"618"), (0.786,"786")]:
                r[f"fib_{label}"] = h50 - diff * pct

            # Extension levels
            r["fib_1272"] = h50 + diff * 0.272
            r["fib_1618"] = h50 + diff * 0.618
            r["fib_2000"] = h50 + diff * 1.000

            price = float(df["close"].iloc[-1])
            # At-level check (within 0.1% of level)
            tol = diff * 0.002
            for pct, label in [(0.382,"382"), (0.500,"500"), (0.618,"618"), (0.786,"786")]:
                level = h50 - diff * pct
                r[f"at_fib_{label}"] = abs(price - level) <= tol
            r["at_fib_any"] = any(r.get(f"at_fib_{l}", False) for l in ["382","500","618","786"])
        except Exception:
            pass
        return r

    def _pivots(self, df: pd.DataFrame) -> dict:
        r = {}
        if len(df) < 5:
            return r
        try:
            # Use yesterday's (last 24-hr equivalent) data
            ph = float(df["high"].tail(288).max())   # ~24hr on M5
            pl = float(df["low"].tail(288).min())
            pc = float(df["close"].iloc[-1])
            pp = (ph + pl + pc) / 3
            r["pivot"] = pp
            r["r1"] = 2*pp - pl
            r["r2"] = pp + (ph - pl)
            r["r3"] = ph + 2*(pp - pl)
            r["s1"] = 2*pp - ph
            r["s2"] = pp - (ph - pl)
            r["s3"] = pl - 2*(ph - pp)

            price = float(df["close"].iloc[-1])
            tol   = float(df["high"].tail(14).max() - df["low"].tail(14).min()) / 14 * 0.5
            r["at_pivot"] = abs(price - pp)  <= tol
            r["at_r1"]    = abs(price - r["r1"]) <= tol
            r["at_s1"]    = abs(price - r["s1"]) <= tol
        except Exception:
            pass
        return r

    def _order_blocks(self, df: pd.DataFrame) -> dict:
        r = {"ob_bull_zone": None, "ob_bear_zone": None,
             "at_bull_ob": False, "at_bear_ob": False}
        if len(df) < 20:
            return r
        try:
            o = df["open"].values
            h = df["high"].values
            l = df["low"].values
            c = df["close"].values
            price = float(c[-1])

            # Bullish OB: last bearish candle before a significant up move
            for i in range(len(c)-5, len(c)-1):
                if c[i] < o[i]:   # bearish candle
                    # Check if followed by bullish move
                    if c[i+1] > o[i+1] and (c[i+1] - o[i+1]) > abs(c[i]-o[i]):
                        ob_high = o[i]
                        ob_low  = c[i]
                        r["ob_bull_zone"] = (ob_low, ob_high)
                        r["at_bull_ob"]   = ob_low <= price <= ob_high * 1.002
                        break

            # Bearish OB: last bullish candle before a significant down move
            for i in range(len(c)-5, len(c)-1):
                if c[i] > o[i]:   # bullish candle
                    if c[i+1] < o[i+1] and abs(c[i+1]-o[i+1]) > abs(c[i]-o[i]):
                        ob_high = c[i]
                        ob_low  = o[i]
                        r["ob_bear_zone"] = (ob_low, ob_high)
                        r["at_bear_ob"]   = ob_low * 0.998 <= price <= ob_high
                        break
        except Exception:
            pass
        return r

    def _fvg(self, df: pd.DataFrame) -> dict:
        r = {"fvg_bull": False, "fvg_bear": False,
             "fvg_bull_zone": None, "fvg_bear_zone": None}
        if len(df) < 5:
            return r
        try:
            h = df["high"].values
            l = df["low"].values
            c = df["close"].values
            price = float(c[-1])

            # Last 10 candles, look for FVG
            for i in range(-8, -2):
                # Bullish FVG: candle[i] high < candle[i+2] low
                if h[i] < l[i+2]:
                    fvg_low  = h[i]
                    fvg_high = l[i+2]
                    r["fvg_bull"] = fvg_low <= price <= fvg_high
                    r["fvg_bull_zone"] = (fvg_low, fvg_high)

                # Bearish FVG: candle[i] low > candle[i+2] high
                if l[i] > h[i+2]:
                    fvg_low  = h[i+2]
                    fvg_high = l[i]
                    r["fvg_bear"] = fvg_low <= price <= fvg_high
                    r["fvg_bear_zone"] = (fvg_low, fvg_high)
        except Exception:
            pass
        return r

    def _liquidity(self, df: pd.DataFrame) -> dict:
        r = {"liq_sweep_bull": False, "liq_sweep_bear": False,
             "equal_highs": None, "equal_lows": None}
        if len(df) < 20:
            return r
        try:
            h = df["high"].values[-20:]
            l = df["low"].values[-20:]
            c = df["close"].values[-20:]
            tol = 5.0   # points tolerance for "equal"

            # Equal highs
            high_clusters = []
            for i in range(len(h)-4, len(h)-1):
                for j in range(i+1, len(h)):
                    if abs(h[i] - h[j]) <= tol:
                        high_clusters.append(h[i])
            if high_clusters:
                r["equal_highs"] = float(np.mean(high_clusters))

            # Equal lows
            low_clusters = []
            for i in range(len(l)-4, len(l)-1):
                for j in range(i+1, len(l)):
                    if abs(l[i] - l[j]) <= tol:
                        low_clusters.append(l[i])
            if low_clusters:
                r["equal_lows"] = float(np.mean(low_clusters))

            price = float(c[-1])
            # Liquidity sweep: wick beyond equal level but close back inside
            if r["equal_highs"]:
                eq_h = r["equal_highs"]
                wicked_above = h[-1] > eq_h
                closed_below = c[-1] < eq_h
                r["liq_sweep_bear"] = wicked_above and closed_below

            if r["equal_lows"]:
                eq_l = r["equal_lows"]
                wicked_below = l[-1] < eq_l
                closed_above = c[-1] > eq_l
                r["liq_sweep_bull"] = wicked_below and closed_above
        except Exception:
            pass
        return r

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _cross_up(a: pd.Series, b: pd.Series) -> bool:
        try:
            return float(a.iloc[-2]) < float(b.iloc[-2]) and float(a.iloc[-1]) >= float(b.iloc[-1])
        except Exception:
            return False

    @staticmethod
    def _cross_down(a: pd.Series, b: pd.Series) -> bool:
        try:
            return float(a.iloc[-2]) > float(b.iloc[-2]) and float(a.iloc[-1]) <= float(b.iloc[-1])
        except Exception:
            return False

    @staticmethod
    def _detect_divergence(price: pd.Series, indicator: pd.Series, lookback: int = 20):
        """Returns (bull_div, bear_div)."""
        try:
            p = price.tail(lookback).values
            ind = indicator.tail(lookback).values
            if len(p) < lookback or np.isnan(ind).any():
                return False, False

            # Price higher high, indicator lower high = bearish div
            p_hh  = p[-1] > max(p[:-5])
            i_lh  = ind[-1] < max(ind[:-5])
            bear_div = p_hh and i_lh

            # Price lower low, indicator higher low = bullish div
            p_ll  = p[-1] < min(p[:-5])
            i_hl  = ind[-1] > min(ind[:-5])
            bull_div = p_ll and i_hl

            return bull_div, bear_div
        except Exception:
            return False, False
