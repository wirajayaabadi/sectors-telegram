"""Turn a Report into Telegram-safe HTML messages.

The agents produce a structured Report; this turns it into the actual text a
subscriber sees. Two jobs code must do (never the LLM): escape <, > & so Telegram's
HTML parser doesn't choke, and split under Telegram's 4096-char-per-message limit.
"""
from __future__ import annotations

import html
import re
from datetime import datetime

from telegram.constants import MessageLimit

from app.config import settings
from app.schemas import Comparison, Report
from app.subsector import SECTION_LABELS as SECTION_NAMES
from app.subsector import VIEWS, Fetched, SubsectorReport, label_for
from app.stock import SECTION_LABELS as STOCK_SECTION_NAMES
from app.stock import VIEWS as STOCK_VIEWS
from app.stock import PriceInfo, StockCard, StockReport
from app.rankings import ActiveDay, ActiveError, ActiveQuery, MoverList, MoversResult

DISCLAIMER = "This is not investment advice."
SAFE_LEN = MessageLimit.MAX_TEXT_LENGTH - 96  # stay safely under 4096

SENTIMENT = {"bullish": "🟢", "neutral": "⚪", "bearish": "🔴"}

e = html.escape


def render_report(report: Report) -> list[str]:
    """Return one or more Telegram-HTML messages for the report."""
    blocks: list[str] = [
        f"🌅 <b>{e(report.headline)}</b>\n<i>As of {e(report.as_of)}</i>",
    ]

    for s in report.sections:
        lines = [f"<b>{e(s.title)}</b> {SENTIMENT.get(s.sentiment, '')}".rstrip()]
        lines += [f"• {e(b)}" for b in s.bullets]
        blocks.append("\n".join(lines))

    if report.watchlist:
        lines = ["👀 <b>On the Radar</b>"]
        lines += [f"• <code>{e(w.symbol)}</code> — {e(w.reason)}" for w in report.watchlist]
        blocks.append("\n".join(lines))

    blocks.append(f"<i>{e(DISCLAIMER)}</i>")

    return _pack(blocks)


def render_comparison(comparison: Comparison) -> list[str]:
    """Return one or more Telegram-HTML messages for a head-to-head comparison."""
    blocks: list[str] = [
        f"⚖️ <b>{e(comparison.headline)}</b>\n"
        f"<i>{e(' vs '.join(comparison.symbols))} — as of {e(comparison.as_of)}</i>"
    ]

    for row in comparison.rows:
        lines = [f"<b>{e(row.metric)}</b>"]
        lines += [f"• <code>{e(v.symbol)}</code> {e(v.value)}" for v in row.values]
        if row.note:
            lines.append(f"<i>{e(row.note)}</i>")
        blocks.append("\n".join(lines))

    if comparison.takeaways:
        lines = ["📌 <b>Takeaways</b>"]
        lines += [f"• {e(item)}" for item in comparison.takeaways]
        blocks.append("\n".join(lines))

    blocks.append(f"<i>{e(DISCLAIMER)}</i>")

    return _pack(blocks)


# ----------------------------------------------------------------------------- /sector
# Deterministic templates only: every figure below was fetched or computed in subsector.py.

SECTOR_DISCLAIMER = "Data deskriptif, bukan rekomendasi beli/jual."
SECTOR_LOADING = "⏳ Mengambil data subsektor…"
NA = "–"
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def _id_num(value: float, decimals: int = 2) -> str:
    """1234567.891 -> '1.234.567,89' (Indonesian separators)."""
    s = f"{value:,.{decimals}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def _pct(value: float | None) -> str:
    """A fraction from the API (0.0244) as a signed percent ('+2,44%'). 0 is a real 0."""
    if value is None:
        return NA
    if value == 0:
        return "0,00%"
    return f"{'+' if value > 0 else '−'}{_id_num(abs(value) * 100)}%"


def _share(value: float | None) -> str:
    """An unsigned-style percent (ROE, yield, share): only negatives carry a sign."""
    if value is None:
        return NA
    return f"{'−' if value < 0 else ''}{_id_num(abs(value) * 100)}%"


def _ratio(value: float | None) -> str:
    if value is None:
        return NA
    return f"{'−' if value < 0 else ''}{_id_num(abs(value))}x"


def _idr(value: float | None) -> str:
    if value is None:
        return NA
    sign, v = ("−" if value < 0 else ""), abs(value)
    for size, unit in ((1e12, " T"), (1e9, " M"), (1e6, " jt")):
        if v >= size:
            return f"{sign}Rp {_id_num(v / size)}{unit}"
    return f"{sign}Rp {_id_num(v, 0)}"


def _when(dt) -> str:
    local = dt.astimezone(settings.tz)
    return f"{local.day:02d} {_MONTHS[local.month - 1]} {local.year} {local:%H:%M} {local.tzname()}"


def _day(iso: str) -> str:
    try:
        y, m, d = (int(x) for x in iso.split("-"))
        return f"{d} {_MONTHS[m - 1]} {y}"
    except (ValueError, IndexError):
        return iso


def render_subsector(fetched: Fetched[SubsectorReport], view: str = "ov") -> list[str]:
    """One result tab as Telegram-HTML messages. `view` is a key of VIEWS."""
    r = fetched.value
    blocks = [
        f"📊 <b>{e(label_for(r.slug))}</b> — {e(VIEWS.get(view, VIEWS['ov']))}\n"
        f"<i>Tingkat subsektor · sektor {e(r.sector or NA)} · nama di API: {e(r.name)} · "
        f"slug <code>{e(r.slug)}</code></i>"
    ]
    body = {"co": _sector_companies, "gr": _sector_growth, "va": _sector_valuation}
    blocks += body.get(view, _sector_overview)(r)
    blocks.append(_sector_footer(fetched))
    return _pack([b for b in blocks if b])


def _list_block(title: str, rows: list[str]) -> str:
    return "\n".join([title, *rows]) if rows else ""


def _mcap_change_line(r: SubsectorReport) -> str:
    names = {"1w": "1 minggu", "ytd": "YTD", "1y": "1 tahun"}
    parts = [f"{names[k]} {_pct(v)}" for k, v in r.mcap_change.items() if k in names]
    if not parts:
        return ""
    when = f" (titik data bulanan terakhir: {_day(r.last_monthly_date)})" if r.last_monthly_date else ""
    return f"• Perubahan market cap: {' · '.join(parts)}{e(when)}"


def _sector_overview(r: SubsectorReport) -> list[str]:
    general = []
    if r.total_companies is not None:
        general.append(f"• Jumlah perusahaan: {r.total_companies} (seluruh emiten subsektor menurut API)")
    if r.total_mcap is not None:
        general.append(f"• Total market cap: {_idr(r.total_mcap)}; rata-rata per emiten {_idr(r.avg_mcap)}")
    if r.top_mcap_share is not None:
        general.append(
            f"• {len(r.top_mcap)} emiten terbesar = {_share(r.top_mcap_share)} dari total market cap"
        )
    general.append(_mcap_change_line(r))

    growth = []
    if (g := r.latest_growth) is not None:
        growth.append(
            f"• {g.year} vs {g.year - 1} (rata-rata tertimbang): pendapatan {_pct(g.revenue)} · "
            f"laba {_pct(g.earnings)}"
        )
    if r.forecasts:
        f = r.forecasts[0]
        base = f" (basis {f.base_year})" if f.base_year else ""
        growth.append(
            f"• Proyeksi Sectors {f.year}{base}: pendapatan {_pct(f.revenue)} · EPS {_pct(f.eps)}"
        )

    valuation = []
    if r.median_pe is not None or r.weighted_pe is not None:
        valuation.append(f"• PE median (terfilter): {_ratio(r.median_pe)}")
        valuation.append(f"• PE rata-rata tertimbang (terfilter): {_ratio(r.weighted_pe)}")

    top = [
        f"{i}. <code>{e(c.symbol)}</code> {_idr(c.value)} ({_share(c.share)})"
        for i, c in enumerate(r.top_mcap, 1)
    ]
    return [
        _list_block("<b>Gambaran umum</b>", [x for x in general if x]),
        _list_block("<b>Pertumbuhan</b> <i>(tahunan, per tahun fiskal)</i>", growth),
        _list_block("<b>Valuasi</b> <i>(agregat dari Sectors)</i>", valuation),
        _list_block("<b>Terbesar menurut market cap</b> <i>(porsi dari total subsektor)</i>", top),
    ]


def _sector_growth(r: SubsectorReport) -> list[str]:
    history = [
        f"• {g.year}: pendapatan {_pct(g.revenue)} · laba {_pct(g.earnings)}" for g in r.growth
    ]
    forecasts = [
        f"• {f.year}{f' (basis {f.base_year})' if f.base_year else ''}: "
        f"pendapatan {_pct(f.revenue)} · EPS {_pct(f.eps)}"
        for f in r.forecasts
    ]
    companies = [
        f"{i}. <code>{e(c.symbol)}</code> {e(c.name)} — {_pct(c.value)}"
        for i, c in enumerate(r.top_growth, 1)
    ]
    blocks = [
        _list_block(
            "<b>Pertumbuhan tahunan subsektor</b> <i>(year-on-year per tahun fiskal, "
            "rata-rata tertimbang)</i>",
            history,
        ),
        _list_block("<b>Proyeksi</b> <i>(dari Sectors, bukan dari bot)</i>", forecasts),
        _list_block(
            "<b>Pertumbuhan pendapatan tertinggi</b> <i>(5 teratas versi API, urut tertinggi; "
            "periode tidak dirinci API)</i>",
            companies,
        ),
    ]
    if history:
        blocks.append(f"<i>Bobot rata-rata ditentukan Sectors dan tidak dirinci di dokumentasinya. {NA} = tidak tersedia.</i>")
    return blocks


def _sector_valuation(r: SubsectorReport) -> list[str]:
    current = []
    if r.median_pe is not None or r.weighted_pe is not None:
        current += [
            f"• PE median (terfilter): {_ratio(r.median_pe)}",
            f"• PE rata-rata tertimbang (terfilter): {_ratio(r.weighted_pe)}",
        ]
    if r.min_pe is not None and r.max_pe is not None:
        current.append(
            f"• Rentang PE per emiten: {_ratio(r.min_pe)} s.d. {_ratio(r.max_pe)} (PE negatif = rugi)"
        )
    history = [
        f"• {v.year}: PE {_ratio(v.pe)} · PB {_ratio(v.pb)} · PS {_ratio(v.ps)} · PCF {_ratio(v.pcf)}"
        for v in r.valuation
    ]
    blocks = [
        _list_block("<b>PE subsektor saat ini</b> <i>(agregat dari Sectors)</i>", current),
        _list_block("<b>Valuasi historis per tahun</b> <i>(agregat subsektor dari Sectors)</i>", history),
    ]
    if current or history:
        blocks.append(
            "<i>Kriteria filter, bobot, dan metode agregasi ditentukan Sectors dan tidak dirinci "
            "di dokumentasinya. Bot tidak merata-ratakan rasio emiten sendiri.</i>"
        )
    return blocks


def _sector_companies(r: SubsectorReport) -> list[str]:
    def row(i, c, value: str) -> str:
        return f"{i}. <code>{e(c.symbol)}</code> {e(c.name)} — {value}"

    of = f"dari {r.total_companies} emiten" if r.total_companies is not None else "jumlah emiten tidak tersedia"
    blocks = [
        f"<i>Setiap daftar berisi maks. 5 emiten teratas versi API ({of}), diurutkan ulang oleh bot.</i>",
        _list_block(
            "<b>Market cap terbesar</b> <i>(urut market cap)</i>",
            [row(i, c, f"{_idr(c.value)} · {_share(c.share)} subsektor") for i, c in enumerate(r.top_mcap, 1)],
        ),
        _list_block(
            "<b>Pendapatan TTM terbesar</b>",
            [row(i, c, _idr(c.value)) for i, c in enumerate(r.top_revenue, 1)],
        ),
        _list_block(
            "<b>Laba TTM terbesar</b> <i>(margin = laba TTM ÷ pendapatan TTM, dihitung bot)</i>",
            [
                row(i, c, _idr(c.value) + (f" · margin {_share(c.margin)}" if c.margin is not None else ""))
                for i, c in enumerate(r.top_profit, 1)
            ],
        ),
        _list_block(
            "<b>Pergerakan harga menonjol</b> <i>(dipilih API, urut perubahan 1 tahun)</i>",
            [
                row(
                    i,
                    m,
                    f"1 thn {_pct(m.change_1y)} · 1 bln {_pct(m.change_1m)} · "
                    f"{_idr(m.last_close)} · PE {_ratio(m.pe)}",
                )
                for i, m in enumerate(r.movers, 1)
            ],
        ),
    ]
    return blocks


def _sector_footer(fetched: Fetched[SubsectorReport]) -> str:
    r = fetched.value
    lines = [
        "Sumber: Sectors API v2 · Tanggal data tidak tersedia",
        f"Waktu pengambilan: {_when(fetched.fetched_at)}{' (dari cache)' if fetched.from_cache else ''}",
    ]
    if fetched.stale_reason == "api_failed":
        lines.append(f"⚠️ Sectors API gagal saat diperbarui; ini data cache dari {_when(fetched.fetched_at)}.")
    elif fetched.stale_reason == "aged":
        lines.append(f"ℹ️ Data cache sudah lewat masa segar. Kirim /sector {e(r.slug)} untuk memperbarui.")
    if r.missing:
        names = ", ".join(SECTION_NAMES.get(s, s) for s in r.missing)
        lines.append(f"⚠️ Data tidak lengkap: bagian {e(names)} tidak dikembalikan API.")
    lines.append(f"<i>{e(SECTOR_DISCLAIMER)}</i>")
    return "\n".join(lines)


def render_subsector_picker(page: int, pages: int) -> str:
    return (
        "📂 <b>Pilih subsektor IDX</b>\n"
        "/sector menyajikan data di tingkat <b>subsektor</b> (mis. Bank), lebih rinci dari "
        "sektor (mis. Keuangan): perusahaan, pertumbuhan, dan valuasinya.\n\n"
        "Ketuk salah satu di bawah, atau ketik langsung, mis. <code>/sector banks</code>.\n"
        f"<i>Halaman {page + 1}/{pages}</i>"
    )


def render_subsector_candidates(query: str, has_candidates: bool) -> str:
    if has_candidates:
        return (
            f"Subsektor “{e(query)}” tidak cocok persis. Maksud Anda salah satu ini?\n"
            "<i>Ketuk untuk memastikan; bot tidak memilih otomatis.</i>"
        )
    return (
        f"Subsektor “{e(query)}” tidak dikenali.\n"
        "Lihat daftar lengkap di bawah, atau coba <code>/sector banks</code>."
    )


# ----------------------------------------------------------------------------- /stock
# Deterministic templates only: every figure was fetched or computed in stock.py.

MISSING = "Tidak tersedia"


def stock_loading(symbol: str) -> str:
    return f"⏳ Mengambil data {e(symbol)}…"


STOCK_HELP = (
    "Gunakan /stock &lt;kode saham&gt;.\n"
    "Contoh: /stock BBCA.\n\n"
    "<i>Belum tahu kodenya? Cari dengan satu kata nama perusahaan, mis. /stock telkom. "
    "Untuk beberapa saham sekaligus, pakai /compare BBCA BBRI.</i>"
)


def render_stock_multi(tickers: tuple[str, ...]) -> str:
    example = " ".join(tickers[:4])
    return (
        "/stock menampilkan satu saham per perintah.\n"
        f"Untuk membandingkan beberapa saham, pakai <code>/compare {e(example)}</code> (2–4 saham)."
    )


def render_stock_candidates(query: str, found: bool, not_listed: bool = False) -> str:
    head = (
        f"Kode saham <b>{e(query.upper())}</b> tidak ditemukan di IDX."
        if not_listed
        else f"Hasil pencarian nama “{e(query)}”."
    )
    if found:
        return f"{head}\nPilih salah satu di bawah; bot tidak memilih otomatis."
    return f"{head}\nTidak ada perusahaan yang cocok. Coba kode lain, mis. <code>/stock BBCA</code>."


def _or_missing(text: str) -> str:
    return MISSING if text == NA else text


def render_stock(card: StockCard, view: str = "ov") -> list[str]:
    r = card.report.value if card.report else None
    title = f"📈 <b>{e(card.symbol)}</b>" + (f" · {e(r.name)}" if r and r.name else "")
    sub = " › ".join(x for x in ((r.sector, r.sub_sector) if r else ()) if x)
    head = title + f" — {e(STOCK_VIEWS.get(view, STOCK_VIEWS['ov']))}"
    if sub:
        head += f"\n<i>{e(sub)}</i>"
    blocks = [head]
    if r is None or view == "ov":
        blocks += _stock_overview(card)
    else:
        blocks += {"pr": _stock_profile, "fi": _stock_financials, "va": _stock_valuation, "dv": _stock_dividend}[view](card, r)
    blocks.append(_stock_footer(card, view))
    return _pack([b for b in blocks if b])


def _price_lines(p: PriceInfo) -> list[str]:
    last, prev = p.last, p.prev
    lines = [f"• Penutupan {_day(last.date)}: <b>{_idr(last.close)}</b>"]
    if prev is not None and p.change is not None:
        sign = "+" if p.change > 0 else ""
        lines.append(
            f"• Perubahan vs {_day(prev.date)} ({_idr(prev.close)}): "
            f"{sign}{_idr(p.change)} ({_pct(p.change_pct)})"
        )
    else:
        lines.append("• Perubahan: tidak dihitung (hanya ada satu harga penutupan valid)")
    if last.volume is not None:
        lines.append(f"• Volume {_day(last.date)}: {_id_num(last.volume, 0)} lembar")
    return lines


def _ratio_note(r: StockReport, p: PriceInfo | None) -> str:
    """P/E and P/B carry no date. If their market cap matches exactly ONE daily row, say
    which; two days with the same close give the same market cap, so then we can't tell."""
    if r.ratio_market_cap is not None and p is not None:
        dates = [
            row.date
            for row in p.rows
            if row.market_cap is not None and abs(row.market_cap - r.ratio_market_cap) < 1
        ]
        if len(dates) == 1:
            return f"Tanggal acuan tidak dicantumkan API; market cap acuannya sama dengan penutupan {_day(dates[0])}."
    return "Tanggal acuan P/E dan P/B tidak dicantumkan API."


def _pe_text(pe: float | None) -> str:
    if pe is None:
        return MISSING
    if pe < 0:
        return f"{_ratio(pe)} (negatif karena laba TTM rugi; bukan tanda murah)"
    return _ratio(pe)


def _stock_overview(card: StockCard) -> list[str]:
    r = card.report.value if card.report else None
    p = card.price.value if card.price else None

    price = []
    if p is not None:
        price += _price_lines(p)
    elif r is not None and r.close is not None:  # daily failed: the report's own dated close
        price.append(f"• Penutupan {_day(r.close_date) if r.close_date else '(tanggal tidak tersedia)'}: <b>{_idr(r.close)}</b>")
        price.append("• Perubahan: tidak dihitung (data harian tidak tersedia)")
    else:
        price.append(f"• Harga penutupan: {MISSING}")
    if p is not None and p.last.market_cap is not None:
        price.append(f"• Market cap {_day(p.last.date)}: {_idr(p.last.market_cap)}")
    elif r is not None and r.market_cap is not None:
        when = _day(r.close_date) if r.close_date else "(tanggal tidak tersedia)"
        price.append(f"• Market cap {when}: {_idr(r.market_cap)}")
    else:
        price.append(f"• Market cap: {MISSING}")
    if r is not None and r.low_52w and r.high_52w:
        price.append(
            f"• Rentang 52 minggu: {_idr(r.low_52w[1])} ({_day(r.low_52w[0])}) – "
            f"{_idr(r.high_52w[1])} ({_day(r.high_52w[0])})"
        )
    blocks = [_list_block("<b>Harga &amp; ukuran</b>", price)]
    if r is None:
        return blocks

    valuation = [
        f"• P/E TTM: {_pe_text(r.pe_ttm)}",
        f"• P/B MRQ: {_or_missing(_ratio(r.pb_mrq))}",
    ]
    if r.pe_ttm is not None or r.pb_mrq is not None:
        valuation.append(f"  <i>{e(_ratio_note(r, p))}</i>")
    if r.yield_ttm is not None:
        basis = f"Rp {_id_num(r.dividend_ttm, 0)}/saham" if r.dividend_ttm is not None else "dividen"
        valuation.append(f"• Dividend yield TTM: {_share(r.yield_ttm)} ({basis} 12 bln terakhir ÷ harga penutupan)")
    else:
        valuation.append(f"• Dividend yield TTM: {MISSING}")
    blocks.append(_list_block("<b>Valuasi</b>", valuation))

    f = r.latest_fin
    fy = f"FY{f.year}" if f else ""
    perf = [
        f"• Pendapatan {fy}: {_or_missing(_idr(f.revenue))}" if f else f"• Pendapatan: {MISSING}",
        f"• Laba bersih {fy}: {_or_missing(_idr(f.earnings))}" if f else f"• Laba bersih: {MISSING}",
        f"• ROE {fy}: {_or_missing(_share(f.roe))}" if f else f"• ROE: {MISSING}",
    ]
    if r.ttm_revenue_growth is not None or r.ttm_earnings_growth is not None:
        perf.append(
            f"• Pertumbuhan YoY (TTM): pendapatan {_pct(r.ttm_revenue_growth)} · laba {_pct(r.ttm_earnings_growth)}"
        )
    blocks.append(_list_block("<b>Kinerja keuangan</b> <i>(FY = tahun fiskal)</i>", perf))
    return blocks


def _stock_profile(card: StockCard, r: StockReport) -> list[str]:
    rows = [
        ("Nama", e(r.name)),
        ("Papan", e(r.listing_board)),
        ("Tercatat sejak", _day(r.listing_date) if r.listing_date else ""),
        ("Sektor", e(" › ".join(x for x in (r.sector, r.sub_sector) if x))),
        ("Industri", e(" › ".join(x for x in (r.industry, r.sub_industry) if x))),
        ("Karyawan", _id_num(r.employees, 0) if r.employees is not None else ""),
        ("Grup/afiliasi", e(", ".join(r.affiliates))),
        ("Indeks", e(", ".join(r.indices))),
        ("Situs", e(r.website)),
        ("Alamat", e(r.address)),
    ]
    return [_list_block("<b>Profil perusahaan</b>", [f"• {k}: {v}" for k, v in rows if v])]


def _stock_financials(card: StockCard, r: StockReport) -> list[str]:
    recent = r.fin_years[-4:]
    pnl = [
        f"• FY{f.year}: pendapatan {_idr(f.revenue)} ({_pct(f.revenue_growth)} YoY) · "
        f"laba bersih {_idr(f.earnings)} ({_pct(f.earnings_growth)} YoY)"
        for f in recent
        if f.revenue is not None or f.earnings is not None
    ]
    ratios = [
        f"• FY{f.year}: ROE {_share(f.roe)} · ROA {_share(f.roa)} · margin laba bersih {_share(f.net_margin)}"
        for f in recent
        if f.roe is not None or f.roa is not None or f.net_margin is not None
    ]
    latest = []
    if r.ttm_revenue_growth is not None or r.ttm_earnings_growth is not None:
        latest.append(
            f"• TTM vs TTM setahun sebelumnya: pendapatan {_pct(r.ttm_revenue_growth)} · laba {_pct(r.ttm_earnings_growth)}"
        )
    if r.quarter_revenue_growth is not None or r.quarter_earnings_growth is not None:
        latest.append(
            f"• Kuartal terakhir vs kuartal sama tahun lalu: pendapatan {_pct(r.quarter_revenue_growth)} · "
            f"laba {_pct(r.quarter_earnings_growth)}"
        )
    if r.eps is not None:
        latest.append(f"• EPS {f'FY{r.eps_year}' if r.eps_year else '(periode tidak dicantumkan)'}: Rp {_id_num(r.eps)}")
    return [
        _list_block("<b>Laba rugi tahunan</b> <i>(per tahun fiskal; YoY dihitung bot)</i>", pnl),
        _list_block("<b>Rasio profitabilitas tahunan</b>", ratios),
        _list_block("<b>Pertumbuhan terbaru</b> <i>(dari API)</i>", latest),
        f"<i>YoY tidak dihitung bila tahun sebelumnya rugi atau kosong. Kuartal acuan tidak dicantumkan API. {NA} = tidak tersedia.</i>",
    ]


def _stock_valuation(card: StockCard, r: StockReport) -> list[str]:
    p = card.price.value if card.price else None
    now = [f"• P/E TTM: {_pe_text(r.pe_ttm)}", f"• P/B MRQ: {_or_missing(_ratio(r.pb_mrq))}"]
    if r.pe_ttm is not None or r.pb_mrq is not None:
        now.append(f"  <i>{e(_ratio_note(r, p))}</i>")
    blocks = [_list_block("<b>Valuasi saat ini</b>", now)]

    if card.valuation is not None:
        v = card.valuation.value
        extra = []
        if v.forward_pe is not None:
            extra.append(f"• P/E forward (proyeksi Sectors): {_ratio(v.forward_pe)}")
        extra += [
            f"• {y['year']}: P/E {_ratio(y['pe'])} (sejawat {_ratio(y['pe_peer_avg'])}) · "
            f"P/B {_ratio(y['pb'])} (sejawat {_ratio(y['pb_peer_avg'])}) · P/S {_ratio(y['ps'])}"
            for y in v.years[-4:]
        ]
        blocks.append(
            _list_block("<b>Valuasi historis per tahun</b> <i>(sejawat = rata-rata yang dihitung Sectors)</i>", extra)
        )
    elif card.valuation_error:
        blocks.append(f"⚠️ Rincian valuasi historis belum tersedia. {e(card.valuation_error)}")

    peers = [
        f"• <code>{e(x.symbol)}</code> {e(x.name)} — P/E TTM {_ratio(x.pe_ttm)} · P/B MRQ {_ratio(x.pb_mrq)}"
        for x in r.peers
    ]
    blocks.append(_list_block("<b>Sejawat</b> <i>(pilihan Sectors, urut market cap)</i>", peers))
    return blocks


def _stock_dividend(card: StockCard, r: StockReport) -> list[str]:
    summary = []
    if r.yield_ttm is not None:
        summary.append(f"• Dividend yield TTM: {_share(r.yield_ttm)} (dividen 12 bln terakhir ÷ harga penutupan)")
    if r.dividend_ttm is not None:
        summary.append(f"• Dividen 12 bln terakhir: Rp {_id_num(r.dividend_ttm, 0)}/saham")
    if r.avg_yield is not None:
        summary.append(f"• Rata-rata yield {r.avg_yield_years or '?'} tahun: {_share(r.avg_yield)}")
    if r.payout_ratio is not None:
        summary.append(
            f"• Payout ratio: {_share(r.payout_ratio)} · cash payout ratio {_share(r.cash_payout_ratio)} "
            "(periode tidak dirinci API)"
        )
    if r.last_ex_date:
        summary.append(f"• Ex-dividend terakhir: {_day(r.last_ex_date)}")
    if r.has_upcoming:
        summary.append("• Ada jadwal dividen mendatang di data Sectors (formatnya belum diverifikasi, tidak ditampilkan)")
    this_year = datetime.now(settings.tz).year
    years = [
        f"• {d.year}{' (tahun berjalan)' if d.year == this_year else ''}: "
        f"Rp {_id_num(d.total, 0) if d.total is not None else NA}/saham · yield {_share(d.yield_)}"
        for d in reversed(r.div_years[-5:])
    ]
    return [
        _list_block("<b>Dividen</b>", summary),
        _list_block("<b>Dividen per tahun</b> <i>(total per saham · yield tahun itu, dari API)</i>", years),
    ]


def _stock_footer(card: StockCard, view: str) -> str:
    lines = ["Sumber: Sectors API v2"]
    for label, f in (("harga", card.price), ("laporan", card.report), ("valuasi", card.valuation if view == "va" else None)):
        if f is None:
            continue
        lines.append(f"Waktu pengambilan {label}: {_when(f.fetched_at)}{' (cache)' if f.from_cache else ''}")
        if f.stale_reason == "api_failed":
            lines.append(f"⚠️ Sectors API gagal saat memperbarui {label}; ini data cache dari {_when(f.fetched_at)}.")
        elif f.stale_reason == "aged":
            lines.append(f"ℹ️ Data {label} dari cache sudah lewat masa segar. Kirim /stock {e(card.symbol)} untuk memperbarui.")
    if card.report_error:
        lines.append(f"⚠️ Laporan perusahaan belum tersedia: {e(card.report_error)}")
    if card.price_error:
        lines.append(f"⚠️ Data harga harian belum tersedia: {e(card.price_error)}")
    elif card.price is not None and card.price.value is None:
        lines.append("⚠️ Tidak ada harga penutupan valid dalam 30 hari terakhir (rentang bawaan API).")
    if card.report is not None and card.report.value.missing:
        names = ", ".join(STOCK_SECTION_NAMES.get(s, s) for s in card.report.value.missing)
        lines.append(f"⚠️ Data tidak lengkap: bagian {e(names)} tidak dikembalikan API.")
    if view == "ov":
        lines.append(
            "<i>Perubahan = selisih harga penutupan, bukan total return. API tidak menyatakan "
            "apakah harga disesuaikan aksi korporasi.</i>"
        )
    lines.append(f"<i>{e(SECTOR_DISCLAIMER)}</i>")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- /movers, /active
# Deterministic templates only: the ranking and its numbers come from rankings.py.

MOVERS_LOADING = "⏳ Mengambil pergerakan saham…"
ACTIVE_LOADING = "⏳ Mengambil saham paling aktif…"

MOVERS_HELP = (
    "Gunakan /movers [gainers|losers] [periode].\n"
    "Periode: 1d, 7d, 14d, 30d, 365d (bawaan 1d). Tanpa gainers/losers: keduanya.\n"
    "Contoh: /movers · /movers 7d · /movers gainers 30d · /movers losers 1d"
)

_ACTIVE_USAGE = (
    "Gunakan /active [YYYY-MM-DD] [jumlah].\n"
    "Jumlah 1–10 (batas API), bawaan 5. Tanpa tanggal: tanggal perdagangan terbaru.\n"
    "Contoh: /active · /active 10 · /active 2026-10-07 · /active 2026-10-07 10"
)
_ACTIVE_ERRORS = {
    "usage": "",
    "bad_date": "Tanggal tidak valid. Pakai format YYYY-MM-DD, mis. 2026-10-07.\n\n",
    "future": "Tanggal itu belum terjadi (zona waktu Asia/Jakarta).\n\n",
    "count": "Jumlah harus bilangan bulat 1–10 (API membatasi 10 emiten per tanggal).\n\n",
}


def render_active_help(error: ActiveError | None = None) -> str:
    return _ACTIVE_ERRORS.get(error.reason if error else "usage", "") + _ACTIVE_USAGE


_PERIOD_TEXT = {
    "1d": "dibanding penutupan sesi perdagangan sebelumnya",
    "7d": "dibanding penutupan 7 hari kalender sebelumnya",
    "14d": "dibanding penutupan 14 hari kalender sebelumnya",
    "30d": "dibanding penutupan 30 hari kalender sebelumnya",
    "365d": "dibanding penutupan 365 hari kalender sebelumnya",
}
_MOVER_TITLES = {"gainers": "🟢 <b>Top Gainers</b>", "losers": "🔴 <b>Top Losers</b>"}


def _volume(shares: float) -> str:
    """Shares, never lots. Big numbers in words so the column stays narrow on a phone."""
    for size, unit in ((1e9, "miliar"), (1e6, "juta")):
        if shares >= size:
            return f"{_id_num(shares / size)} {unit} lembar"
    return f"{_id_num(shares, 0)} lembar"


def _fetched_lines(parts: list[tuple[str, Fetched]], command: str) -> list[str]:
    lines = []
    times = {_when(f.fetched_at) for _, f in parts}
    if len(times) == 1:
        cached = " (cache)" if any(f.from_cache for _, f in parts) else ""
        lines.append(f"Waktu pengambilan: {times.pop()}{cached}")
    else:
        lines += [
            f"Waktu pengambilan {label}: {_when(f.fetched_at)}{' (cache)' if f.from_cache else ''}"
            for label, f in parts
        ]
    for label, f in parts:
        where = f" {label}" if len(parts) > 1 else ""
        if f.stale_reason == "api_failed":
            lines.append(f"⚠️ Sectors API gagal saat memperbarui{where}; ini data cache dari {_when(f.fetched_at)}.")
        elif f.stale_reason == "aged":
            lines.append(f"ℹ️ Data{where} dari cache sudah lewat masa segar. Kirim {command} untuk memperbarui.")
    return lines


def render_movers(result: MoversResult) -> list[str]:
    q = result.query
    lists = [result.parts[w].value for w in q.classes if w in result.parts]
    dates = sorted({d for lst in lists for d in lst.ref_dates})
    single_date = dates[0] if len(dates) == 1 else None
    if single_date:
        ref = f"Tanggal acuan: {_day(single_date)}"
    elif dates:
        ref = "Tanggal acuan berbeda per emiten (lihat tiap baris)."
    else:
        ref = "Tanggal acuan tidak tersedia di respons API."
    blocks = [
        f"📊 <b>Top Movers — {e(q.period.upper())}</b>\n"
        f"<i>Perubahan harga penutupan, {_PERIOD_TEXT[q.period]}.</i>\n{ref}"
    ]

    notes: list[str] = []
    for which in q.classes:
        title = _MOVER_TITLES[which]
        if which in result.errors:
            blocks.append(f"{title}\n⚠️ Bagian ini gagal dimuat. {e(result.errors[which])}")
            continue
        lst: MoverList = result.parts[which].value
        if not lst.rows:
            blocks.append(f"{title}\nTidak ada emiten yang dikembalikan API untuk bagian ini.")
        else:
            blocks.append("\n".join([title, *(_mover_line(m, single_date) for m in lst.rows)]))
        label = "Top Gainers" if which == "gainers" else "Top Losers"
        if lst.rows and len(lst.rows) + lst.dropped < 5:
            notes.append(f"{label}: API hanya mengembalikan {len(lst.rows) + lst.dropped} emiten.")
        if lst.dropped:
            notes.append(f"{label}: {lst.dropped} baris diabaikan karena perubahan harganya kosong/tidak valid.")
        if lst.resorted:
            notes.append(f"{label}: urutan dari API tidak konsisten, diurutkan ulang menurut perubahan (seri: ticker A–Z).")

    footer = [
        "Sumber: Sectors API v2 · data akhir hari, bukan real-time",
        f"Hanya emiten dengan market cap ≥ Rp {_id_num(settings.movers_min_mcap_billion, 0)} miliar (filter API).",
        *_fetched_lines(
            [(("Top Gainers" if w == "gainers" else "Top Losers"), result.parts[w]) for w in q.classes if w in result.parts],
            "/movers",
        ),
        *(f"ℹ️ {e(n)}" for n in notes),
        "<i>Pergerakan harga masa lalu, bukan rekomendasi beli/jual.</i>",
    ]
    blocks.append("\n".join(footer))
    return _pack(blocks)


def _mover_line(m, single_date: str | None) -> str:
    line = f"{m.rank}. <code>{e(m.symbol)}</code>"
    if m.name:
        line += f" {e(m.name)}"
    line += f" — <b>{_pct(m.change)}</b>"
    if m.close is not None and m.close_date:  # a close only ever appears with its own date
        line += f" · tutup {_idr(m.close)}"
        if m.close_date != single_date:
            line += f" ({_day(m.close_date)})"
    return line


def render_active(fetched: Fetched, day: ActiveDay | None, q: ActiveQuery) -> list[str]:
    title = "🔥 <b>Saham Teraktif Berdasarkan Volume</b>"
    if day is None:
        if q.day is not None:
            body = f"Data tidak tersedia untuk tanggal {_day(q.day.isoformat())}."
        else:
            body = "Data tidak tersedia dalam 30 hari terakhir (rentang bawaan API)."
        return _pack([title, body, "\n".join(_fetched_lines([("", fetched)], "/active"))])

    when = "" if q.day is not None else " (terbaru yang tersedia)"
    rows = day.rows[: q.n]
    blocks = [
        f"{title}\nTanggal perdagangan: {_day(day.day)}{when}\n"
        "<i>Peringkat menurut volume (jumlah lembar saham), bukan nilai transaksi atau frekuensi.</i>",
        "\n".join(
            f"{r.rank}. <code>{e(r.symbol)}</code>{' ' + e(r.name) if r.name else ''} — {_volume(r.volume)}"
            + (f" · tutup {_idr(r.close)}" if r.close is not None else "")
            for r in rows
        ),
    ]
    notes = []
    if len(rows) < q.n:
        notes.append(f"API hanya mengembalikan {len(rows)} emiten untuk tanggal ini.")
    if day.dropped:
        notes.append(f"{day.dropped} baris diabaikan karena volumenya kosong/tidak valid.")
    if day.resorted:
        notes.append("Urutan dari API tidak konsisten, diurutkan ulang menurut volume (seri: ticker A–Z).")
    blocks.append(
        "\n".join(
            [
                "Sumber: Sectors API v2 · data akhir hari · harga = penutupan tanggal tersebut",
                *_fetched_lines([("", fetched)], "/active"),
                *(f"ℹ️ {e(n)}" for n in notes),
                "<i>Volume tinggi bukan bukti akumulasi atau sinyal beli/jual.</i>",
            ]
        )
    )
    return _pack(blocks)


def _pack(blocks: list[str], limit: int = SAFE_LEN) -> list[str]:
    """Greedily join blocks with a blank line, starting a new message before the limit."""
    messages: list[str] = []
    current = ""
    for block in _fit(blocks, limit):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                messages.append(current)
            current = block
    if current:
        messages.append(current)
    return messages


def _fit(blocks: list[str], limit: int) -> list[str]:
    """Split any block over the limit at line breaks, so no tag is ever cut in half.

    Every renderer here opens and closes its tags on the same line, so a line boundary is
    always a safe place to cut. A single line that is still too long loses its tags and
    is shortened as plain text.
    """
    out: list[str] = []
    for block in blocks:
        if len(block) <= limit:
            out.append(block)
            continue
        current = ""
        for line in block.split("\n"):
            if len(line) > limit:
                line = _shorten_line(line, limit)
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= limit:
                current = candidate
            else:
                out.append(current)
                current = line
        if current:
            out.append(current)
    return out


def _shorten_line(line: str, limit: int) -> str:
    text = re.sub(r"<[^>]+>", "", line)  # already-escaped text, minus the tags
    cut = text[: limit - 1]
    amp = cut.rfind("&")
    if amp != -1 and ";" not in cut[amp:]:  # don't leave half an entity like "&am"
        cut = cut[:amp]
    return cut + "…"
