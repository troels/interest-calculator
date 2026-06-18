"""Chart output (matplotlib PNG). Engine-agnostic: takes plain result dicts."""

from __future__ import annotations

from pathlib import Path


def _ensure_parent(path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def plot_strategy_costs(result: dict, out_path: str | Path) -> Path:
    """Stacked bar of total cost (breakage + PV interest) per strategy."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    strategies = result["strategies"]
    names = [s["name"] for s in strategies]
    breakage = [s["breakage"] / 1e6 for s in strategies]
    interest = [s["interest_pv"] / 1e6 for s in strategies]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(names, interest, label="PV interest", color="#3b75af")
    ax.bar(names, breakage, bottom=interest, label="Swap breakage", color="#c0504d")
    for i, s in enumerate(strategies):
        ax.text(i, (s["interest_pv"] + s["breakage"]) / 1e6, f"{s['total_pv']/1e6:.2f}M",
                ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("Total cost (DKK million, PV)")
    ax.set_title(f"Swap vs realkredit — total remaining cost as of {result['as_of']}\n"
                 f"breakage={result['breakage']/1e6:.2f}M  "
                 f"break-even RK rate={result['break_even_rate_pct']:.2f}%")
    ax.legend()
    fig.tight_layout()
    out = _ensure_parent(out_path)
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def plot_backtest(rows: list[dict], out_path: str | Path) -> Path:
    """Time series: swap breakage and convert-vs-stay PV advantage over time."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dates = [r["as_of"] for r in rows]
    breakage = [r["breakage"] / 1e6 for r in rows]
    advantage = [r["convert_advantage_pv"] / 1e6 for r in rows]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    ax1.plot(dates, breakage, color="#c0504d")
    ax1.set_ylabel("Swap breakage (M DKK)")
    ax1.set_title("Historical swap breakage cost")

    ax2.axhline(0, color="grey", lw=0.8)
    ax2.plot(dates, advantage, color="#3b75af")
    ax2.set_ylabel("Convert advantage (M DKK, PV)")
    ax2.set_title("Converting-to-fixed advantage vs staying (positive = convert wins)")
    ax2.set_xlabel("decision date")
    for ax in (ax1, ax2):
        ax.tick_params(axis="x", rotation=45, labelsize=7)
        # thin out x labels
        for i, lbl in enumerate(ax.get_xticklabels()):
            if i % max(1, len(dates) // 12) != 0:
                lbl.set_visible(False)
    fig.tight_layout()
    out = _ensure_parent(out_path)
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out
