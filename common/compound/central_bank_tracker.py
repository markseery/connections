#!/usr/bin/env python3
"""
Central Bank Gold & US Treasuries Tracker
========================================

Fetches, stores, analyzes, and optionally plots:
- Foreign official institutions' holdings of US Treasuries (proxy for central bank demand) via FRED
- US Treasury TIC "Major Foreign Holders" (latest + historical) for country breakdown context
- Central bank gold reserves / purchases from a user-downloaded World Gold Council XLS snapshot

No sample/placeholder datasets are included: the script only uses fetched/downloaded data.

Environment:
  - FRED_API_KEY is read from `.env` (loaded) or the environment.

Dependencies (already in this repo's `requirements.txt` except where noted):
  - pandas
  - httpx
  - python-dotenv
  - beautifulsoup4 (optional; not required by default)
  - lxml (optional; not required by default)
  - openpyxl (required only for `--update-gold` when reading WGC XLS)
  - matplotlib (optional; required only for `--plot`)
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from common.simple import script_env

script_env.ensure_sys_path()

try:
    # Prefer repo's dotenv loader (keeps behavior consistent across codebase)
    from common.simple.user_dir import load_connections_dotenv
except Exception:  # pragma: no cover
    load_connections_dotenv = None


@dataclass(frozen=True)
class TrackerConfig:
    repo_root: Path
    output_dir: Path

    fred_series_treasury_official: str = "BOGZ1FL263061130Q"
    tic_historical_url: str = (
        "https://treasury.gov/resource-center/data-chart-center/tic/Documents/mfhhis01.csv"
    )
    tic_latest_url: str = (
        "https://treasury.gov/resource-center/data-chart-center/tic/Documents/slt_table5.txt"
    )

    # WGC XLS is user-downloaded; script reads it if present.
    wgc_gold_xlsx: Path | None = None

    @staticmethod
    def default() -> "TrackerConfig":
        repo_root = script_env.repo_root()
        output_dir = repo_root / "application_files" / "data" / "central_bank_tracker"
        return TrackerConfig(repo_root=repo_root, output_dir=output_dir.resolve())


class TreasuryDataFetcher:
    def __init__(self, cfg: TrackerConfig) -> None:
        self.cfg = cfg

    def fetch_fred_treasury_foreign_official(self, fred_api_key: str) -> pd.DataFrame:
        """
        Fetch quarterly foreign official US Treasury holdings from FRED API.

        Returns columns:
          - Date (datetime64)
          - Foreign_Official_Treasury_Holdings_Millions_USD (float)
          - Source
        """
        import httpx

        base = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "api_key": fred_api_key,
            "file_type": "json",
            "series_id": self.cfg.fred_series_treasury_official,
        }
        with httpx.Client(timeout=30.0) as client:
            r = client.get(base, params=params)
            r.raise_for_status()
            payload = r.json()

        obs = payload.get("observations", [])
        rows: list[dict[str, Any]] = []
        for o in obs:
            v = o.get("value")
            if v in (None, ".", ""):
                continue
            try:
                val = float(v)
            except Exception:
                continue
            rows.append(
                {
                    "Date": pd.to_datetime(o.get("date")),
                    "Foreign_Official_Treasury_Holdings_Millions_USD": val,
                    "Source": "FRED Z.1",
                }
            )

        df = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
        return df

    def fetch_tic_latest(self) -> pd.DataFrame:
        """Download and parse latest TIC Major Foreign Holders table."""
        import httpx

        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            r = client.get(self.cfg.tic_latest_url)
            r.raise_for_status()
            content = r.text

        # TIC "slt_table5.txt" has historically been tab-delimited; fall back to whitespace.
        try:
            df = pd.read_csv(StringIO(content), sep="\t", header=0, thousands=",")
        except Exception:
            df = pd.read_csv(StringIO(content), sep=r"\s+", header=0, thousands=",")

        df.columns = [str(c).strip().replace("\n", " ") for c in df.columns]
        return df

    def fetch_tic_historical(self) -> pd.DataFrame:
        """Download historical TIC MFH data (CSV)."""
        import httpx

        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            r = client.get(self.cfg.tic_historical_url)
            r.raise_for_status()
            content = r.text

        df = pd.read_csv(StringIO(content))
        df.columns = [str(c).strip() for c in df.columns]
        return df


class GoldDataFetcher:
    def __init__(self, cfg: TrackerConfig) -> None:
        self.cfg = cfg

    def fetch_imf_gold_reserves_world_via_fetchseries(self) -> pd.DataFrame:
        """
        Fetch monthly official gold reserves (world aggregate) from FetchSeries.

        FetchSeries publishes an IMF-sourced Excel dataset that is directly downloadable:
          https://www.fetchseries.com/central-banks/gold-reserves-imf/gold-reserves-imf.xlsx

        Returns:
          - Date
          - Area
          - Gold_Reserves_Tonnes
          - Gold_Reserves_FineTroyOunces
          - Net_Change_Tonnes
          - Source
        """
        from io import BytesIO

        import httpx

        url = "https://www.fetchseries.com/central-banks/gold-reserves-imf/gold-reserves-imf.xlsx"
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            content = r.content

        try:
            import openpyxl  # noqa: F401
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "Fetching gold reserves downloads an .xlsx file, which requires `openpyxl` to parse.\n"
                "Install it with: `pip install openpyxl`"
            ) from e

        xls = pd.ExcelFile(BytesIO(content), engine="openpyxl")
        if "Global aggregates" not in xls.sheet_names:
            raise RuntimeError("FetchSeries workbook missing expected sheet: 'Global aggregates'.")

        df = pd.read_excel(BytesIO(content), sheet_name="Global aggregates", engine="openpyxl")
        if df.empty:
            raise RuntimeError("FetchSeries workbook 'Global aggregates' sheet is empty.")

        # The first row contains labels; the second column is a parsed Timestamp.
        date_col = "Unnamed: 1"
        if date_col not in df.columns:
            raise RuntimeError("Unexpected FetchSeries sheet format (missing parsed date column).")

        # Find the 'World' series column (contains 'World - IMF - Monthly').
        world_cols = [c for c in df.columns if isinstance(c, str) and "World" in c and "IMF" in c]
        if not world_cols:
            raise RuntimeError("Could not locate the world gold reserves series column in FetchSeries sheet.")
        world_col = world_cols[0]

        out = df[[date_col, world_col]].copy()
        out = out.rename(columns={date_col: "Date", world_col: "Gold_Reserves_FineTroyOunces"})
        out = out[pd.to_datetime(out["Date"], errors="coerce").notna()].copy()
        out["Date"] = pd.to_datetime(out["Date"])
        out["Gold_Reserves_FineTroyOunces"] = pd.to_numeric(out["Gold_Reserves_FineTroyOunces"], errors="coerce")
        out = out.dropna(subset=["Gold_Reserves_FineTroyOunces"]).sort_values("Date").reset_index(drop=True)

        # Convert fine troy ounces to tonnes: 1 oz troy = 0.0311034768 kg.
        out["Gold_Reserves_Tonnes"] = out["Gold_Reserves_FineTroyOunces"] * 0.0311034768 / 1000.0
        out["Net_Change_Tonnes"] = out["Gold_Reserves_Tonnes"].diff()
        out["Area"] = "World"
        out["Source"] = "FetchSeries (IMF source)"
        return out[["Date", "Area", "Gold_Reserves_Tonnes", "Gold_Reserves_FineTroyOunces", "Net_Change_Tonnes", "Source"]]

    def load_wgc_gold_xlsx(self, path: Path) -> pd.DataFrame:
        """
        Load WGC gold reserves snapshot XLS/XLSX (user-downloaded).

        Notes:
          - The workbook schema varies. This function loads the first sheet by default.
          - If you need a custom sheet, pass a different file or rename tabs accordingly.
        """
        try:
            import openpyxl  # noqa: F401
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "Reading WGC XLS requires `openpyxl`. Install it (e.g. `pip install openpyxl`)."
            ) from e

        xl = pd.ExcelFile(path)
        sheet = xl.sheet_names[0]
        df = pd.read_excel(path, sheet_name=sheet)
        df.columns = [str(c).strip() for c in df.columns]
        return df


class TreasuryDataProcessor:
    def add_derived_metrics(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute net quarterly change and YoY percent change (4-quarter lag)."""
        if df.empty:
            return df
        if "Foreign_Official_Treasury_Holdings_Millions_USD" not in df.columns:
            return df

        out = df.copy()
        out["Date"] = pd.to_datetime(out["Date"])
        out = out.sort_values("Date").reset_index(drop=True)
        out["Net_Quarterly_Change_Millions"] = out[
            "Foreign_Official_Treasury_Holdings_Millions_USD"
        ].diff()
        out["YoY_Change_Pct"] = (
            out["Foreign_Official_Treasury_Holdings_Millions_USD"].pct_change(4) * 100
        )
        return out


class TrackerPresenter:
    def __init__(self, cfg: TrackerConfig) -> None:
        self.cfg = cfg

    def print_summary(
        self,
        treas_fred: pd.DataFrame,
        tic_latest: Optional[pd.DataFrame],
        gold_df: Optional[pd.DataFrame],
    ) -> None:
        print("\n" + "=" * 72)
        print("CENTRAL BANK GOLD & US TREASURIES TRACKER - SUMMARY")
        print("=" * 72)

        if not treas_fred.empty and "Foreign_Official_Treasury_Holdings_Millions_USD" in treas_fred.columns:
            latest = treas_fred.iloc[-1]
            print("\nUS TREASURIES - Foreign Official Institutions (FRED)")
            print(
                f"  Latest ({pd.to_datetime(latest['Date']).date()}): "
                f"${latest['Foreign_Official_Treasury_Holdings_Millions_USD']/1e6:.2f}T"
            )
            if "Net_Quarterly_Change_Millions" in treas_fred.columns and pd.notna(
                latest.get("Net_Quarterly_Change_Millions")
            ):
                print(f"  QoQ change: ${latest['Net_Quarterly_Change_Millions']/1e3:,.1f}B")
            if "YoY_Change_Pct" in treas_fred.columns and pd.notna(latest.get("YoY_Change_Pct")):
                print(f"  YoY change: {latest['YoY_Change_Pct']:.1f}%")
        else:
            print("\nUS TREASURIES - Foreign Official Institutions (FRED)")
            print("  No FRED series data found. Run with `--update` and set `FRED_API_KEY`.")

        if tic_latest is not None and not tic_latest.empty:
            print("\nTIC - Latest Major Foreign Holders (head)")
            print(tic_latest.head(10).to_string(index=False))

        if gold_df is not None and not gold_df.empty:
            print("\nGOLD - Official reserves")
            print(f"  Rows loaded: {len(gold_df):,}")
            if {"Date", "Gold_Reserves_Tonnes"}.issubset(set(gold_df.columns)):
                gd = gold_df.copy()
                gd["Date"] = pd.to_datetime(gd["Date"], errors="coerce")
                gd = gd.dropna(subset=["Date"]).sort_values("Date")
                latest = gd.iloc[-1]
                print(
                    f"  Latest ({latest['Date'].date()}): {float(latest['Gold_Reserves_Tonnes']):,.0f} tonnes"
                )
                if "Net_Change_Tonnes" in gd.columns and pd.notna(latest.get("Net_Change_Tonnes")):
                    print(f"  MoM change: {float(latest['Net_Change_Tonnes']):,.1f} tonnes")
            print(
                f"  Columns: {', '.join(map(str, gold_df.columns[:12]))}{' ...' if len(gold_df.columns)>12 else ''}"
            )
        else:
            print("\nGOLD - Official reserves")
            print("  Not loaded. Run `--update-gold`.")

        print("\nOutput directory:", str(self.cfg.output_dir))
        print("=" * 72 + "\n")

    def plot_treasury_holdings(self, treas_fred: pd.DataFrame) -> Optional[Path]:
        """Save a holdings-over-time PNG if matplotlib is available."""
        if treas_fred.empty or "Foreign_Official_Treasury_Holdings_Millions_USD" not in treas_fred.columns:
            return None

        try:
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
        except Exception:  # pragma: no cover
            print("matplotlib not available; skipping plots.")
            return None

        df = treas_fred.copy()
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values("Date")

        fig, ax = plt.subplots(figsize=(12, 6))
        ax.plot(
            df["Date"],
            df["Foreign_Official_Treasury_Holdings_Millions_USD"] / 1000,
            linewidth=2,
            color="#1f77b4",
        )
        ax.set_title(
            "US Treasuries Held by Foreign Official Institutions (FRED)",
            fontsize=13,
            fontweight="bold",
        )
        ax.set_ylabel("Holdings (Trillions USD)")
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax.xaxis.set_major_locator(mdates.YearLocator(2))
        fig.autofmt_xdate()

        out = self.cfg.output_dir / "plot_treasury_holdings.png"
        fig.tight_layout()
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return out

    def plot_gold_reserves(self, gold_df: pd.DataFrame) -> list[Path]:
        """
        Save gold reserve plots (tonnes level + net change) if matplotlib is available.

        Expects columns:
          - Date
          - Gold_Reserves_Tonnes
          - Net_Change_Tonnes (optional; will be computed if missing)
        """
        if gold_df.empty or "Gold_Reserves_Tonnes" not in gold_df.columns:
            return []

        try:
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
        except Exception:  # pragma: no cover
            print("matplotlib not available; skipping plots.")
            return []

        df = gold_df.copy()
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
        df["Gold_Reserves_Tonnes"] = pd.to_numeric(df["Gold_Reserves_Tonnes"], errors="coerce")
        df = df.dropna(subset=["Gold_Reserves_Tonnes"])
        if df.empty:
            return []

        if "Net_Change_Tonnes" not in df.columns:
            df["Net_Change_Tonnes"] = df["Gold_Reserves_Tonnes"].diff()
        else:
            df["Net_Change_Tonnes"] = pd.to_numeric(df["Net_Change_Tonnes"], errors="coerce")

        outputs: list[Path] = []

        # Level plot
        fig1, ax1 = plt.subplots(figsize=(12, 6))
        ax1.plot(df["Date"], df["Gold_Reserves_Tonnes"], linewidth=2, color="#b58900")
        ax1.set_title("Official Gold Reserves (World)", fontsize=13, fontweight="bold")
        ax1.set_ylabel("Tonnes")
        ax1.grid(True, alpha=0.3)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax1.xaxis.set_major_locator(mdates.YearLocator(5))
        fig1.autofmt_xdate()
        out1 = self.cfg.output_dir / "plot_gold_reserves_level.png"
        fig1.tight_layout()
        fig1.savefig(out1, dpi=150, bbox_inches="tight")
        plt.close(fig1)
        outputs.append(out1)

        # Net change plot
        fig2, ax2 = plt.subplots(figsize=(12, 4.5))
        colors = ["#2ca02c" if (x or 0) >= 0 else "#d62728" for x in df["Net_Change_Tonnes"]]
        ax2.bar(df["Date"], df["Net_Change_Tonnes"], color=colors, alpha=0.85, width=20)
        ax2.axhline(0, color="black", linewidth=0.7)
        ax2.set_title("Official Gold Reserves - Net Change (World, monthly)", fontsize=12, fontweight="bold")
        ax2.set_ylabel("Tonnes")
        ax2.grid(True, alpha=0.3, axis="y")
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax2.xaxis.set_major_locator(mdates.YearLocator(5))
        fig2.autofmt_xdate()
        out2 = self.cfg.output_dir / "plot_gold_reserves_net_change.png"
        fig2.tight_layout()
        fig2.savefig(out2, dpi=150, bbox_inches="tight")
        plt.close(fig2)
        outputs.append(out2)

        return outputs

    def plot_combined_treasuries_vs_gold(
        self,
        treas_fred: pd.DataFrame,
        gold_df: pd.DataFrame,
        *,
        start_year: int = 2000,
    ) -> Optional[Path]:
        """
        Combined plot with Treasuries (left Y) and Gold (right Y).

        - Treasuries: trillions USD (FRED series)
        - Gold: tonnes (official reserves series)
        """
        if treas_fred.empty or gold_df.empty:
            return None
        if "Foreign_Official_Treasury_Holdings_Millions_USD" not in treas_fred.columns:
            return None
        if "Gold_Reserves_Tonnes" not in gold_df.columns:
            return None

        try:
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
        except Exception:  # pragma: no cover
            print("matplotlib not available; skipping plots.")
            return None

        start = pd.Timestamp(date(start_year, 1, 1))

        t = treas_fred.copy()
        t["Date"] = pd.to_datetime(t["Date"], errors="coerce")
        t = t.dropna(subset=["Date"]).sort_values("Date")
        t = t[t["Date"] >= start]

        g = gold_df.copy()
        g["Date"] = pd.to_datetime(g["Date"], errors="coerce")
        g["Gold_Reserves_Tonnes"] = pd.to_numeric(g["Gold_Reserves_Tonnes"], errors="coerce")
        g = g.dropna(subset=["Date", "Gold_Reserves_Tonnes"]).sort_values("Date")
        g = g[g["Date"] >= start]

        if t.empty or g.empty:
            return None

        fig, ax_left = plt.subplots(figsize=(12.5, 6.5))
        ax_right = ax_left.twinx()

        line1 = ax_left.plot(
            t["Date"],
            t["Foreign_Official_Treasury_Holdings_Millions_USD"] / 1_000_000,
            color="#1f77b4",
            linewidth=2.2,
            label="Foreign official UST holdings (T USD)",
        )
        line2 = ax_right.plot(
            g["Date"],
            g["Gold_Reserves_Tonnes"],
            color="#b58900",
            linewidth=2.0,
            alpha=0.95,
            label="Official gold reserves (tonnes)",
        )

        ax_left.set_title(
            f"Treasuries vs Gold (from {start_year})",
            fontsize=13,
            fontweight="bold",
        )
        ax_left.set_xlabel("Date")
        ax_left.set_ylabel("Treasuries (Trillions USD)")
        ax_right.set_ylabel("Gold (Tonnes)")

        ax_left.grid(True, alpha=0.25)
        ax_left.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax_left.xaxis.set_major_locator(mdates.YearLocator(2))
        fig.autofmt_xdate()

        # Combined legend
        lines = line1 + line2
        labels = [l.get_label() for l in lines]
        ax_left.legend(lines, labels, loc="upper left", frameon=True)

        out = self.cfg.output_dir / "plot_combined_treasuries_vs_gold.png"
        fig.tight_layout()
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return out


class LocalStore:
    def __init__(self, cfg: TrackerConfig) -> None:
        self.cfg = cfg
        self.cfg.output_dir.mkdir(parents=True, exist_ok=True)

    def save_csv(self, df: pd.DataFrame, name: str) -> Path:
        path = self.cfg.output_dir / name
        df.to_csv(path, index=False)
        return path

    def load_csv(self, name: str) -> pd.DataFrame:
        path = self.cfg.output_dir / name
        if not path.exists():
            return pd.DataFrame()
        return pd.read_csv(path)


def _load_env() -> None:
    """
    Load environment variables from the repo's `.env` (repo root).

    We *also* call the repo's canonical loader if available, but we do not rely on it
    being configured for this script's location.
    """
    if load_connections_dotenv is not None:
        try:
            load_connections_dotenv()
        except Exception:
            # Keep going; we'll still try loading repo-root `.env` below.
            pass

    # Always attempt to load repo-root `.env` (without overriding existing env).
    try:  # pragma: no cover
        from dotenv import load_dotenv

        load_dotenv(script_env.repo_root() / ".env", override=False)
    except Exception:
        pass


def _get_fred_key(explicit: Optional[str]) -> Optional[str]:
    return (explicit or os.getenv("FRED_API_KEY") or "").strip() or None


def _default_wgc_gold_path(cfg: TrackerConfig) -> Path:
    """
    Return the default WGC gold XLS/XLSX path.

    We first try the conventional filename, then fall back to auto-detecting a likely
    WGC download already placed in `data/`.
    """
    data_dir = cfg.repo_root / "data"
    conventional = data_dir / "gold_reserves_wgc.xlsx"
    if conventional.exists():
        return conventional

    candidates: list[Path] = []
    for pat in (
        "*gold*reserve*.xls*",
        "*gold*reserves*.xls*",
        "*wgc*gold*.xls*",
        "*gold*wgc*.xls*",
    ):
        candidates.extend(data_dir.glob(pat))

    # Prefer the newest modified file if there are multiple matches.
    candidates = [p for p in candidates if p.is_file()]
    if candidates:
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]

    return conventional


def build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Track central bank gold and foreign official US Treasury holdings."
    )
    p.add_argument("--update", action="store_true", help="Fetch and save FRED + TIC datasets")
    p.add_argument(
        "--update-gold",
        action="store_true",
        help="Fetch and save gold reserves (IMF SDMX by default; WGC XLS optional)",
    )
    p.add_argument("--plot", action="store_true", help="Generate plots (requires matplotlib)")
    p.add_argument("--summary", action="store_true", help="Print a short summary")
    p.add_argument("--fred-key", type=str, default=None, help="FRED API key (or set FRED_API_KEY)")
    p.add_argument(
        "--gold-xls",
        type=str,
        default=None,
        help="Path to WGC gold reserves XLS/XLSX (user-downloaded)",
    )
    p.add_argument(
        "--gold-source",
        type=str,
        default="fetchseries-imf",
        choices=["fetchseries-imf", "wgc-xls"],
        help="Gold data source: fetchseries-imf (auto-fetch) or wgc-xls (requires file)",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Directory for CSV outputs and plots (default: application_files/data/central_bank_tracker). "
            "Relative paths are from the repo root."
        ),
    )
    return p


def main() -> int:
    _load_env()
    args = build_cli().parse_args()

    output_dir = script_env.resolve_output_dir(args.out_dir, segment="central_bank_tracker")
    cfg = TrackerConfig(repo_root=script_env.repo_root(), output_dir=output_dir)
    store = LocalStore(cfg)

    fetcher = TreasuryDataFetcher(cfg)
    proc = TreasuryDataProcessor()
    gold_fetcher = GoldDataFetcher(cfg)
    presenter = TrackerPresenter(cfg)

    if args.update:
        key = _get_fred_key(args.fred_key)
        if key:
            treas = fetcher.fetch_fred_treasury_foreign_official(key)
            treas = proc.add_derived_metrics(treas)
        else:
            treas = pd.DataFrame()
            print(
                "FRED_API_KEY not found; skipping FRED holdings series. "
                "Set `FRED_API_KEY` in `.env` or pass `--fred-key` to enable."
            )
        tic_latest = fetcher.fetch_tic_latest()
        tic_hist = fetcher.fetch_tic_historical()

        if not treas.empty:
            store.save_csv(treas, "us_treasury_foreign_official_fred.csv")
        store.save_csv(tic_latest, "tic_major_foreign_holders_latest.csv")
        store.save_csv(tic_hist, "tic_mfh_historical.csv")
        print(f"Saved datasets to: {cfg.output_dir}")

    if args.update_gold:
        if args.gold_source == "wgc-xls":
            gold_path = (
                Path(args.gold_xls).expanduser()
                if args.gold_xls
                else _default_wgc_gold_path(cfg)
            )
            if not gold_path.exists():
                raise SystemExit(
                    "WGC gold XLS not found.\n"
                    f"- Looked for: {gold_path}\n"
                    "- Provide it via `--gold-xls /path/to/file.xlsx` "
                    "or place it in `data/` (recommended name: `data/gold_reserves_wgc.xlsx`)."
                )
            gold = gold_fetcher.load_wgc_gold_xlsx(gold_path)
            store.save_csv(gold, "central_bank_gold_wgc.csv")
            print(f"Saved WGC gold snapshot to: {cfg.output_dir}")
        else:
            gold = gold_fetcher.fetch_imf_gold_reserves_world_via_fetchseries()
            store.save_csv(gold, "central_bank_gold_imf_world.csv")
            print(f"Saved IMF world gold reserves to: {cfg.output_dir}")

    # Load for summary/plot (or default behavior)
    want_default = not (args.update or args.update_gold or args.plot or args.summary)
    if args.summary or args.plot or want_default:
        treas = store.load_csv("us_treasury_foreign_official_fred.csv")
        if not treas.empty and "Date" in treas.columns:
            treas["Date"] = pd.to_datetime(treas["Date"])

        tic_latest = store.load_csv("tic_major_foreign_holders_latest.csv")
        gold = store.load_csv("central_bank_gold_imf_world.csv")
        if gold.empty:
            gold = store.load_csv("central_bank_gold_wgc.csv")

        presenter.print_summary(treas, tic_latest if not tic_latest.empty else None, gold if not gold.empty else None)

        if args.plot:
            out = presenter.plot_treasury_holdings(treas)
            if out is not None:
                print(f"Saved plot: {out}")
            for p in presenter.plot_gold_reserves(gold) if not gold.empty else []:
                print(f"Saved plot: {p}")
            combined = presenter.plot_combined_treasuries_vs_gold(treas, gold, start_year=2000)
            if combined is not None:
                print(f"Saved plot: {combined}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

