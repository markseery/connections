"""Presentation helpers for Robinhood position CLI output."""

from __future__ import annotations

import json
from typing import Any, TextIO

from common.compound.finance_pipeline.robinhood_positions import PositionSummary, RobinhoodPositionAnalyzer


def position_totals(positions: list[dict[str, Any]]) -> dict[str, float]:
    return {
        "current_value": round(sum(float(p.get("current_value") or 0.0) for p in positions), 2),
        "unrealized_pl": round(sum(float(p.get("unrealized_pl") or 0.0) for p in positions), 2),
        "all_in_pl": round(
            sum(
                float(p.get("all_in_pl_including_dividends_and_options") or 0.0)
                for p in positions
            ),
            2,
        ),
        "dividends": round(sum(float(p.get("dividend_sum") or 0.0) for p in positions), 2),
    }


def print_position_summary_report(
    *,
    summary: PositionSummary,
    analyzer: RobinhoodPositionAnalyzer,
    output_path: str,
    excel_path: str,
    cash_forecast_excel_path: str,
    stream: TextIO,
) -> None:
    print(
        f"Analyzed account '{summary.account_name}': "
        f"records_read={summary.records_read}, positions={len(summary.positions)}",
        file=stream,
    )
    print(f"Summary file: {output_path}", file=stream)
    print(f"Excel file: {excel_path}", file=stream)
    print(f"Cash Forecast file: {cash_forecast_excel_path}", file=stream)
    print(
        "Monte Carlo constraints path: "
        f"{analyzer.monte_carlo_constraints_path}",
        file=stream,
    )
    print(
        "Monte Carlo constraints in use: "
        f"{json.dumps(analyzer.monte_carlo_constraints, ensure_ascii=False)}",
        file=stream,
    )

    totals = position_totals(summary.positions)
    print(
        "Totals: "
        f"current_value={totals['current_value']}, "
        f"unrealized_pl={totals['unrealized_pl']}, "
        f"all_in_pl={totals['all_in_pl']}, "
        f"dividends={totals['dividends']}",
        file=stream,
    )

    cr = summary.cash_reconciliation or {}
    print("Cash Reconciliation:", file=stream)
    print(
        "  "
        f"trade_cash_delta_buy_sell={cr.get('trade_cash_delta_buy_sell')}, "
        f"transfer_adjusted_cash_delta={cr.get('transfer_adjusted_cash_delta')}, "
        f"full_cash_delta_all_amount_rows={cr.get('full_cash_delta_all_amount_rows')}",
        file=stream,
    )
    print(
        "  "
        f"transfer_cash_delta_acati_acato={cr.get('transfer_cash_delta_acati_acato')}, "
        f"dividends_cash_delta={cr.get('dividends_cash_delta')}, "
        f"options_cash_delta={cr.get('options_cash_delta')}, "
        f"fees_cash_delta={cr.get('fees_cash_delta')}, "
        f"match_cash_delta={cr.get('match_cash_delta')}",
        file=stream,
    )

    cf = summary.cash_forecast or {}
    cft = cf.get("totals") or {}
    print("Cash Forecast:", file=stream)
    print(
        "  "
        f"as_of_date={cf.get('as_of_date')}, "
        f"projected_next_30_days={cft.get('projected_next_30_days')}, "
        f"projected_next_60_days={cft.get('projected_next_60_days')}, "
        f"projected_next_90_days={cft.get('projected_next_90_days')}",
        file=stream,
    )
    for row in (cf.get("symbols") or []):
        cadence_label = row.get("cadence_label")
        cadence_days = row.get("cadence_days")
        cadence_text = (
            f"{cadence_label}({cadence_days}d)"
            if cadence_label is not None and cadence_days is not None
            else "unknown"
        )
        print(
            "  "
            f"{row.get('instrument')}: cadence={cadence_text}, "
            f"median_dividend={row.get('median_dividend_amount')}, "
            f"inflow_30={row.get('projected_next_30_days')}, "
            f"inflow_60={row.get('projected_next_60_days')}, "
            f"inflow_90={row.get('projected_next_90_days')}, "
            f"note={row.get('forecast_note')}, "
            f"yield_source={row.get('yield_source')}, "
            f"yield_used={row.get('dividend_yield_used')}",
            file=stream,
        )

    if summary.positions:
        print("Positions:", file=stream)
        for position in summary.positions:
            print(
                "  "
                f"{position['instrument']}: qty={position['quantity']}, "
                f"avg_cost={position['estimated_avg_cost']}, "
                f"cost_basis={position['estimated_cost_basis']}, "
                f"price={position.get('current_price')}, "
                f"value={position.get('current_value')}",
                file=stream,
            )
            print(
                "    "
                f"unrealized_pl={position.get('unrealized_pl')}, "
                f"all_in_pl={position.get('all_in_pl_including_dividends_and_options')}, "
                f"dividend_sum={position.get('dividend_sum')}, "
                f"options_cash_net={position.get('options_cash_net')}",
                file=stream,
            )
            print(
                "    "
                f"net_cash_flow={position.get('net_cash_flow')}, "
                f"cash_in={position.get('cash_in')}, "
                f"cash_out={position.get('cash_out')}, "
                f"buy_quantity={position.get('buy_quantity')}, "
                f"sell_quantity={position.get('sell_quantity')}",
                file=stream,
            )
            transactions = position.get("buy_sell_transactions") or []
            if transactions:
                for transaction in transactions:
                    print(
                        "    "
                        f"{transaction.get('activity_date')} {transaction.get('trans_code')}: "
                        f"qty={transaction.get('quantity')}, "
                        f"price={transaction.get('price')}, "
                        f"amount={transaction.get('amount')}",
                        file=stream,
                    )
            else:
                print("    buy/sell transactions: none", file=stream)
            print("", file=stream)
    else:
        print("Positions: none", file=stream)

