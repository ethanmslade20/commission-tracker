"""
Auto-generated end-of-month PDF report ("Book & Performance Report").

Runs from launchd on the 1st of each month (scripts/monthly_report.sh), builds
last month's report to ~/Desktop, and texts Ethan the path. Same skeleton as
the hand-built June 2026 report he approved: KPI banner, income trend chart,
month-by-carrier table, book composition, bottom line.

Manual run:  .venv/bin/python -m tracker.monthly_report [YYYY-MM]
"""
import glob
import json
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent


def _money_data(settings):
    from tracker.commissions import parse_payments_sheet, monthly_summary
    from tracker.sheets import _open_sheet
    imp = next((settings[k] for k in settings if "impersonat" in k.lower()), None)
    pay = parse_payments_sheet(_open_sheet(settings["payments_sheet_url"], imp))
    pay["payment_month"] = pd.to_datetime(pay["payment_month"], errors="coerce")
    pay["amount"] = pd.to_numeric(pay["amount"], errors="coerce")
    return pay, monthly_summary(pay)


def build_monthly_pdf(month: str = None, out_dir: Path = None) -> Path:
    """month='YYYY-MM' (default: last calendar month). Returns the PDF path."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (HRFlowable, Image, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    from tracker.config import load_settings
    from tracker.dashboard import _build_mom_from_all_clients

    if month is None:
        month = (pd.Timestamp.today().normalize().replace(day=1)
                 - pd.offsets.MonthBegin(1)).strftime("%Y-%m")
    m_ts = pd.Timestamp(month + "-01")
    m_label = m_ts.strftime("%B %Y")
    out_dir = Path(out_dir or (Path.home() / "Desktop"))
    out = out_dir / f"Book_Report_{m_ts.strftime('%b_%Y')}.pdf"
    scratch = _ROOT / "data" / ".report_charts"
    scratch.mkdir(parents=True, exist_ok=True)

    settings = load_settings()
    pay, ms = _money_data(settings)
    snap = pd.read_parquet(sorted(glob.glob(str(_ROOT / "snapshots" / "*healthsherpa*.parquet")))[-1])

    # ---- numbers ----
    _ACT = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
    act = snap[snap["status"].isin(_ACT)]
    n_pol = len(act)
    n_mem = int(pd.to_numeric(act["applicant_count"], errors="coerce").fillna(1).sum())
    row = ms[pd.to_datetime(ms["Month"]).dt.strftime("%Y-%m") == month]
    m_net = float(row["Net"].iloc[0]) if len(row) else 0.0
    m_cb = float(row["Chargebacks"].iloc[0]) if len(row) else 0.0
    ytd = float(pay[pay["payment_month"].dt.year == m_ts.year]["amount"].sum())
    sub = pd.to_datetime(snap["submission_date"], errors="coerce")
    sold = snap[sub.dt.strftime("%Y-%m") == month]
    sold_mem = int(pd.to_numeric(sold["applicant_count"], errors="coerce").fillna(1).sum())
    mom = _build_mom_from_all_clients(snap)
    lrow = mom[mom["Month"] == month]
    lost_p = int(lrow["Policies Lost"].iloc[0]) if len(lrow) else 0
    lost_m = int(lrow["Members Lost"].iloc[0]) if len(lrow) else 0
    jun_c = (pay[pay["payment_month"].dt.strftime("%Y-%m") == month]
             .groupby("carrier")["amount"].sum().sort_values(ascending=False))

    # ---- chart: monthly net ----
    GREEN = "#16a34a"
    mc = ms.copy()
    mc["Lbl"] = pd.to_datetime(mc["Month"]).dt.strftime("%b")
    fig, ax = plt.subplots(figsize=(7.2, 2.6), dpi=200)
    bars = ax.bar(mc["Lbl"], mc["Net"], color=GREEN, width=0.62, zorder=3)
    hi = [i for i, m in enumerate(pd.to_datetime(mc["Month"]).dt.strftime("%Y-%m")) if m == month]
    for i in hi:
        bars[i].set_color("#f59e0b")
    for b, v in zip(bars, mc["Net"]):
        ax.text(b.get_x() + b.get_width() / 2, v + 400, f"${v/1000:.1f}k",
                ha="center", va="bottom", fontsize=8, color="#0f172a", fontweight="bold")
    ax.set_ylim(0, mc["Net"].max() * 1.2)
    ax.grid(axis="y", color="#e2e8f0", zorder=0); ax.set_axisbelow(True)
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    ax.tick_params(length=0, labelsize=9); ax.set_yticks([])
    plt.tight_layout()
    chart1 = scratch / "monthly.png"
    plt.savefig(chart1, transparent=True, bbox_inches="tight"); plt.close()

    # ---- pdf ----
    NAVY = colors.HexColor("#0f2740"); INK = colors.HexColor("#0f172a")
    SLATE = colors.HexColor("#475569"); LINE = colors.HexColor("#e2e8f0")
    LIGHT = colors.HexColor("#f1f5f9"); GRN = colors.HexColor("#16a34a")
    SEC = ParagraphStyle("SEC", fontName="Helvetica-Bold", fontSize=13, textColor=INK,
                         spaceBefore=6, spaceAfter=2)
    BODY = ParagraphStyle("BODY", fontName="Helvetica", fontSize=9.5, textColor=SLATE, leading=14)
    KN = ParagraphStyle("KN", fontName="Helvetica-Bold", fontSize=18, textColor=colors.white,
                        alignment=1, leading=20)
    KL = ParagraphStyle("KL", fontName="Helvetica", fontSize=7.5, textColor=colors.white,
                        alignment=1, leading=9)

    def header(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY); canvas.rect(0, letter[1] - 1.15 * inch, letter[0], 1.15 * inch, fill=1, stroke=0)
        canvas.setFillColor(GRN); canvas.rect(0, letter[1] - 1.18 * inch, letter[0], 0.05 * inch, fill=1, stroke=0)
        canvas.setFillColor(colors.white); canvas.setFont("Helvetica-Bold", 20)
        canvas.drawString(0.75 * inch, letter[1] - 0.62 * inch, "Book & Performance Report")
        canvas.setFillColor(colors.HexColor("#cbd5e1")); canvas.setFont("Helvetica", 10.5)
        canvas.drawString(0.75 * inch, letter[1] - 0.86 * inch,
                          f"{m_label}  •  Ethan Slade  •  auto-generated")
        canvas.setFillColor(colors.HexColor("#64748b")); canvas.setFont("Helvetica", 7.5)
        canvas.drawString(0.75 * inch, 0.5 * inch, "Slade Insurance — confidential")
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
        canvas.restoreState()

    def kpi(num, lbl, bg):
        t = Table([[Paragraph(num, KN)], [Paragraph(lbl, KL)]], colWidths=[1.62 * inch])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg),
                               ("TOPPADDING", (0, 0), (0, 0), 10),
                               ("BOTTOMPADDING", (0, -1), (-1, -1), 10)]))
        return t

    def sec(title):
        return [Spacer(1, 12), Paragraph(title, SEC),
                HRFlowable(width="100%", thickness=1.2, color=GRN, spaceAfter=6, spaceBefore=1)]

    def table(data, widths, right_cols=()):
        TH = ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=8.5, textColor=colors.white)
        TD = ParagraphStyle("TD", fontName="Helvetica", fontSize=8.5, textColor=INK)
        rows = [[Paragraph(str(c), TH) for c in data[0]]] + \
               [[Paragraph(str(c), TD) for c in r] for r in data[1:]]
        t = Table(rows, colWidths=widths, repeatRows=1)
        cmds = [("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("GRID", (0, 1), (-1, -1), 0.4, LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 4.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5)]
        for c in right_cols:
            cmds.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
        t.setStyle(TableStyle(cmds))
        return t

    story = [Spacer(1, 0.55 * inch)]
    cards = Table([[kpi(f"${m_net:,.0f}", f"{m_ts.strftime('%b').upper()} NET INCOME", GRN),
                    kpi(f"${ytd:,.0f}", f"{m_ts.year} NET (YTD)", colors.HexColor("#2563eb")),
                    kpi(f"{n_mem:,}", f"ACTIVE MEMBERS ({n_pol:,} POLICIES)", colors.HexColor("#0891b2")),
                    kpi(f"{len(sold)}", f"POLICIES SOLD IN {m_ts.strftime('%b').upper()} ({sold_mem} MEMBERS)",
                        colors.HexColor("#d97706"))]],
                  colWidths=[1.75 * inch] * 4)
    cards.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 3),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    story += [cards]

    story += sec("1.  Monthly Income Trend")
    story += [Paragraph(f"<b>{m_label}: ${m_net:,.0f} net</b> after ${abs(m_cb):,.0f} in chargebacks. "
                        f"Year-to-date you've collected <b>${ytd:,.0f}</b>.", BODY),
              Spacer(1, 6), Image(str(chart1), width=6.7 * inch, height=2.4 * inch)]

    story += sec(f"2.  {m_label} Income by Carrier")
    cd = [["Carrier", "Net Paid"]] + [[c, f"${v:,.0f}"] for c, v in jun_c.items() if abs(v) >= 1]
    cd.append(["TOTAL", f"${jun_c.sum():,.0f}"])
    story += [table(cd, [3.2 * inch, 1.6 * inch], right_cols=(1,))]

    story += sec("3.  Book Health")
    story += [Paragraph(
        f"•  Active book: <b>{n_pol:,} policies / {n_mem:,} members</b><br/>"
        f"•  Sold in {m_label}: <b>{len(sold)} policies / {sold_mem} members</b><br/>"
        f"•  Lost in {m_label} (real termination dates): <b>{lost_p} policies / {lost_m} members</b><br/>"
        f"•  Net member change: <b>{sold_mem - lost_m:+,}</b>", BODY)]

    doc = SimpleDocTemplate(str(out), pagesize=letter, topMargin=1.35 * inch,
                            bottomMargin=0.75 * inch, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            title=f"Book & Performance Report — {m_label}")
    doc.build(story, onFirstPage=header, onLaterPages=header)
    return out


def main():
    month = sys.argv[1] if len(sys.argv) > 1 else None
    out = build_monthly_pdf(month)
    print(f"Monthly report written: {out}")
    # Text Ethan that it's ready (same alert config as the other texts).
    try:
        cfg = json.loads((_ROOT / "data" / "alert_config.json").read_text())
        phone = cfg.get("phone")
        if phone and cfg.get("lapse_alerts", True):
            from tracker.digest import send_imessage
            send_imessage(f"📊 Your monthly Book & Performance Report is ready:\n{out}", phone)
            print(f"Texted {phone}")
    except Exception as e:
        print(f"(text skipped: {e})")


if __name__ == "__main__":
    main()
