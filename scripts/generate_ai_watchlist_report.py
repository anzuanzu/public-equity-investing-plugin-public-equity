from __future__ import annotations

import argparse
import html
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
USER_AGENT += "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


@dataclass
class WatchItem:
    ticker: str
    company: str
    theme: str
    baseline_bucket: str
    baseline_priority: int
    news_query: str
    source_group: str


def fetch_json(url: str) -> Any:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8")


def load_config(path: Path) -> tuple[dict[str, Any], list[WatchItem]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items: list[WatchItem] = []
    for group_name in ("core_watchlist", "candidate_universe"):
        for entry in raw[group_name]:
            items.append(
                WatchItem(
                    ticker=entry["ticker"],
                    company=entry["company"],
                    theme=entry["theme"],
                    baseline_bucket=entry["baseline_bucket"],
                    baseline_priority=entry["baseline_priority"],
                    news_query=entry["news_query"],
                    source_group=group_name,
                )
            )
    return raw, items


def compact_series(values: list[Any]) -> list[float]:
    return [float(value) for value in values if value is not None]


def fetch_market_snapshot(item: WatchItem) -> dict[str, Any]:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{item.ticker}"
        "?range=3mo&interval=1d&includePrePost=false"
    )
    payload = fetch_json(url)
    result = payload["chart"]["result"][0]
    meta = result["meta"]
    quote = result["indicators"]["quote"][0]
    closes = compact_series(quote.get("close", []))
    volumes = compact_series(quote.get("volume", []))
    latest_close = closes[-1] if closes else float(meta.get("regularMarketPrice") or 0)
    prev_close = closes[-2] if len(closes) >= 2 else float(meta.get("previousClose") or latest_close)
    week_anchor = closes[-6] if len(closes) >= 6 else closes[0] if closes else latest_close
    daily_change_pct = pct_change(latest_close, prev_close)
    weekly_change_pct = pct_change(latest_close, week_anchor)
    latest_volume = volumes[-1] if volumes else 0.0
    trailing_volume = mean(volumes[-11:-1]) if len(volumes) >= 11 else mean(volumes[:-1]) if len(volumes) >= 2 else latest_volume
    volume_ratio = round(latest_volume / trailing_volume, 2) if trailing_volume else 1.0
    timestamp = meta.get("regularMarketTime") or meta.get("currentTradingPeriod", {}).get("regular", {}).get("end")

    return {
      "currency": meta.get("currency", "USD"),
      "exchange": meta.get("fullExchangeName", meta.get("exchangeName", "")),
      "price": round(latest_close, 2),
      "previous_close": round(prev_close, 2),
      "daily_change_pct": round(daily_change_pct, 2),
      "weekly_change_pct": round(weekly_change_pct, 2),
      "latest_volume": int(latest_volume) if latest_volume else 0,
      "volume_ratio": volume_ratio,
      "market_time_utc": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat() if timestamp else None,
    }


def fetch_headlines(item: WatchItem, limit: int) -> list[dict[str, str]]:
    query = quote(item.news_query)
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    xml_text = fetch_text(url)
    root = ET.fromstring(xml_text)
    entries: list[dict[str, str]] = []
    for node in root.findall("./channel/item")[:limit]:
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        pub_date = (node.findtext("pubDate") or "").strip()
        source_node = node.find("source")
        source = source_node.text.strip() if source_node is not None and source_node.text else ""
        entries.append(
            {
                "title": title,
                "link": link,
                "published": pub_date,
                "source": source,
            }
        )
    return entries


def pct_change(new_value: float, old_value: float) -> float:
    if not old_value:
        return 0.0
    return ((new_value - old_value) / old_value) * 100.0


def keyword_score(headlines: list[dict[str, str]], positive_keywords: list[str], negative_keywords: list[str]) -> tuple[int, int, int]:
    positive_hits = 0
    negative_hits = 0
    for headline in headlines:
        title = headline["title"].lower()
        if any(keyword in title for keyword in positive_keywords):
            positive_hits += 1
        if any(keyword in title for keyword in negative_keywords):
            negative_hits += 1
    score = (positive_hits * 2) - (negative_hits * 2)
    return score, positive_hits, negative_hits


def classify_item(
    item: WatchItem,
    market: dict[str, Any],
    headlines: list[dict[str, str]],
    rules: dict[str, Any],
) -> dict[str, Any]:
    score = item.baseline_priority
    reasons: list[str] = []

    abs_daily = abs(market["daily_change_pct"])
    abs_weekly = abs(market["weekly_change_pct"])
    if abs_daily >= rules["daily_move_focus_pct"]:
        score += 3
        reasons.append(f"1-day move {market['daily_change_pct']}%")
    elif abs_daily >= rules["daily_move_focus_pct"] / 2:
        score += 1
        reasons.append(f"1-day move {market['daily_change_pct']}%")

    if abs_weekly >= rules["weekly_move_focus_pct"]:
        score += 3
        reasons.append(f"5-day move {market['weekly_change_pct']}%")
    elif abs_weekly >= rules["weekly_move_focus_pct"] / 2:
        score += 1
        reasons.append(f"5-day move {market['weekly_change_pct']}%")

    if market["volume_ratio"] >= rules["volume_spike_ratio"]:
        score += 2
        reasons.append(f"volume spike {market['volume_ratio']}x")

    news_score, positive_hits, negative_hits = keyword_score(
        headlines,
        rules["positive_keywords"],
        rules["negative_keywords"],
    )
    score += news_score
    if positive_hits:
        reasons.append(f"{positive_hits} catalyst headlines")
    if negative_hits:
        reasons.append(f"{negative_hits} risk headlines")

    if market["daily_change_pct"] < -8:
        score += 1
        reasons.append("forced review on sharp drawdown")

    if negative_hits >= 2:
        action = "Re-underwrite"
    elif score >= rules["focus_threshold"]:
        action = "Focus today"
    elif score >= rules["review_threshold"]:
        action = "Core watch"
    else:
        action = "Monitor"

    promotion_candidate = item.source_group == "candidate_universe" and score >= rules["promotion_threshold"]
    return {
        "score": score,
        "action": action,
        "reasons": reasons,
        "positive_hits": positive_hits,
        "negative_hits": negative_hits,
        "promotion_candidate": promotion_candidate,
    }


def compare_with_previous(current_rows: list[dict[str, Any]], history_dir: Path) -> None:
    previous_files = sorted(history_dir.glob("*.json"))
    if len(previous_files) < 2:
        return
    previous_payload = json.loads(previous_files[-2].read_text(encoding="utf-8"))
    previous_map = {row["ticker"]: row for row in previous_payload.get("rows", [])}
    for row in current_rows:
        prior = previous_map.get(row["ticker"])
        if not prior:
            row["change_note"] = "New to saved history"
            continue
        rank_delta = prior["rank"] - row["rank"]
        action_changed = prior["action"] != row["action"]
        if rank_delta > 0:
            row["change_note"] = f"Moved up {rank_delta} places"
        elif rank_delta < 0:
            row["change_note"] = f"Moved down {abs(rank_delta)} places"
        elif action_changed:
            row["change_note"] = f"Action {prior['action']} -> {row['action']}"
        else:
            row["change_note"] = "No major ranking change"


def render_html(report_name: str, rows: list[dict[str, Any]], generated_at: str) -> str:
    focus_rows = [row for row in rows if row["action"] == "Focus today"]
    promotion_rows = [row for row in rows if row["promotion_candidate"]]
    review_rows = [row for row in rows if row["action"] == "Re-underwrite"]

    def render_table_rows(items: list[dict[str, Any]]) -> str:
        html_rows: list[str] = []
        for row in items:
            headlines = "".join(
                f"<li><a href=\"{html.escape(headline['link'])}\">{html.escape(headline['title'])}</a>"
                f"<span class=\"source\">{html.escape(headline['source'])}</span></li>"
                for headline in row["headlines"]
            )
            reasons = ", ".join(row["reasons"]) if row["reasons"] else "Baseline watch"
            html_rows.append(
                "<tr>"
                f"<td><strong>{row['ticker']}</strong><div class=\"sub\">{html.escape(row['company'])}</div></td>"
                f"<td>{html.escape(row['theme'])}</td>"
                f"<td>{row['price']:.2f}</td>"
                f"<td class=\"{'up' if row['daily_change_pct'] >= 0 else 'down'}\">{row['daily_change_pct']:+.2f}%</td>"
                f"<td class=\"{'up' if row['weekly_change_pct'] >= 0 else 'down'}\">{row['weekly_change_pct']:+.2f}%</td>"
                f"<td>{row['volume_ratio']:.2f}x</td>"
                f"<td>{row['score']}</td>"
                f"<td>{html.escape(row['action'])}</td>"
                f"<td>{html.escape(reasons)}</td>"
                f"<td>{html.escape(row.get('change_note', 'First run'))}</td>"
                f"<td><ul class=\"headline-list\">{headlines}</ul></td>"
                "</tr>"
            )
        return "".join(html_rows)

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(report_name)}</title>
  <style>
    :root {{
      --bg: #f7f3ea;
      --card: #fffdf8;
      --ink: #182027;
      --muted: #5f6972;
      --line: #d8cfbf;
      --accent: #0d5f5a;
      --up: #146c43;
      --down: #9f2d24;
      --warn: #8c6412;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background: linear-gradient(180deg, #f8f2e8 0%, #f0e9dc 100%);
    }}
    main {{
      width: min(1280px, calc(100% - 28px));
      margin: 20px auto 40px;
    }}
    .hero, .panel {{
      background: var(--card);
      border: 1px solid var(--line);
      box-shadow: 0 12px 24px rgba(44, 36, 24, 0.06);
    }}
    .hero {{
      padding: 32px;
      background:
        radial-gradient(circle at top right, rgba(13, 95, 90, 0.08), transparent 25%),
        radial-gradient(circle at top left, rgba(140, 100, 18, 0.10), transparent 25%),
        var(--card);
    }}
    h1, h2 {{ margin: 0; }}
    h1 {{ font-size: clamp(28px, 3vw, 48px); }}
    .lede {{
      margin-top: 14px;
      max-width: 920px;
      color: var(--muted);
      font-size: 18px;
      line-height: 1.55;
    }}
    .meta {{
      margin-top: 10px;
      color: var(--muted);
      font-size: 14px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 16px;
      margin-top: 16px;
    }}
    .panel {{
      padding: 22px;
    }}
    .kpi {{
      font-size: 34px;
      margin-top: 8px;
    }}
    .stack {{
      display: grid;
      gap: 16px;
      margin-top: 16px;
    }}
    .table-wrap {{
      overflow-x: auto;
      margin-top: 12px;
    }}
    table {{
      width: 100%;
      min-width: 1180px;
      border-collapse: collapse;
    }}
    th, td {{
      text-align: left;
      padding: 12px 10px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
      font-size: 14px;
    }}
    th {{
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: 12px;
      color: var(--muted);
      background: #f1eadc;
    }}
    .sub {{
      color: var(--muted);
      font-size: 12px;
      margin-top: 4px;
    }}
    .up {{ color: var(--up); }}
    .down {{ color: var(--down); }}
    .headline-list {{
      margin: 0;
      padding-left: 18px;
    }}
    .headline-list li + li {{
      margin-top: 6px;
    }}
    .source {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-top: 2px;
    }}
    .pill {{
      display: inline-block;
      padding: 4px 8px;
      border: 1px solid var(--line);
      color: var(--accent);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-bottom: 10px;
      background: rgba(13, 95, 90, 0.05);
    }}
    ul {{
      margin: 0;
      padding-left: 18px;
    }}
    li + li {{
      margin-top: 8px;
    }}
    a {{ color: var(--accent); }}
    @media (max-width: 900px) {{
      .grid {{
        grid-template-columns: 1fr;
      }}
      .hero, .panel {{
        padding: 18px;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div class="pill">Daily AI Watchlist Monitor</div>
      <h1>{html.escape(report_name)}</h1>
      <p class="lede">這份監控報告會同時追蹤原始核心名單與延伸候選名單，根據最新價格動能、成交量異常、以及新聞催化劑強度，動態把股票分成「今天該聚焦」、「持續核心追蹤」、「需要重估風險」與「可升級候選」。</p>
      <div class="meta">Generated at {html.escape(generated_at)}</div>
    </section>

    <section class="grid">
      <article class="panel">
        <h2>Focus Today</h2>
        <div class="kpi">{len(focus_rows)}</div>
        <div class="meta">今天分數最高、最值得先看的標的</div>
      </article>
      <article class="panel">
        <h2>Promotion Candidates</h2>
        <div class="kpi">{len(promotion_rows)}</div>
        <div class="meta">延伸名單中可升級成主追蹤的股票</div>
      </article>
      <article class="panel">
        <h2>Risk Review</h2>
        <div class="kpi">{len(review_rows)}</div>
        <div class="meta">負面新聞或劇烈波動，需要重新檢視 thesis</div>
      </article>
    </section>

    <section class="stack">
      <article class="panel">
        <h2>Today&apos;s Priority Notes</h2>
        <ul>
          <li><strong>Focus today</strong> 代表事件強度已超過你設定的日常監控門檻，不一定等於買進訊號。</li>
          <li><strong>Promotion candidate</strong> 代表這檔股票本來只在延伸名單，但今天的事件密度或價格/量能足以升級成主追蹤。</li>
          <li><strong>Re-underwrite</strong> 代表應該重新驗證原始投資邏輯，而不是只把回檔當成便宜。</li>
        </ul>
      </article>

      <article class="panel">
        <h2>Full Ranking</h2>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Theme</th>
                <th>Price</th>
                <th>1D</th>
                <th>5D</th>
                <th>Volume</th>
                <th>Score</th>
                <th>Action</th>
                <th>Driver</th>
                <th>Delta</th>
                <th>Recent Headlines</th>
              </tr>
            </thead>
            <tbody>
              {render_table_rows(rows)}
            </tbody>
          </table>
        </div>
      </article>
    </section>
  </main>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a daily AI watchlist monitor HTML report.")
    parser.add_argument("--config", default="config/ai_watchlist.json")
    parser.add_argument("--output-html", default="outputs/ai-watchlist-daily-monitor.html")
    parser.add_argument("--output-json", default="work/daily-monitor/latest-snapshot.json")
    parser.add_argument("--history-dir", default="work/daily-monitor/history")
    args = parser.parse_args()

    config_path = Path(args.config)
    output_html = Path(args.output_html)
    output_json = Path(args.output_json)
    history_dir = Path(args.history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_html.parent.mkdir(parents=True, exist_ok=True)

    config, items = load_config(config_path)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []
    for item in items:
        market = fetch_market_snapshot(item)
        headlines = fetch_headlines(item, config["headline_limit_per_ticker"])
        classification = classify_item(item, market, headlines, config["scoring"])
        row = {
            "ticker": item.ticker,
            "company": item.company,
            "theme": item.theme,
            "baseline_bucket": item.baseline_bucket,
            "source_group": item.source_group,
            "headlines": headlines,
            **market,
            **classification,
        }
        rows.append(row)

    rows.sort(key=lambda row: (-row["score"], -row["daily_change_pct"], row["ticker"]))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index

    history_stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    payload = {
        "name": config["name"],
        "generated_at": generated_at,
        "rows": rows,
    }
    (history_dir / f"{history_stamp}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    compare_with_previous(rows, history_dir)

    payload["rows"] = rows
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_text = render_html(config["name"], rows, generated_at)
    output_html.write_text(html_text, encoding="utf-8")
    print(f"Wrote {output_html}")
    print(f"Wrote {output_json}")


if __name__ == "__main__":
    main()
