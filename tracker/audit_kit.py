"""
Phase-B audit kit: the two documents used to sell manual commission audits.

  build_audit_pdf()        "Commission Audit" — the deliverable an agent PAYS for.
                           Leads with money owed, then poached clients, churn,
                           and book composition. Every section degrades
                           gracefully when that agent didn't provide a source
                           (e.g. no payments sheet -> carrier-evidence only).

  build_instructions_pdf() "Send Me These 5 Exports" — the one-page sheet a
                           prospect gets, with the exact clicks per website.

Both render from whatever is currently loaded in the pipeline, so running them
on Ethan's own data produces the sample/demo audit.

Manual run:  .venv/bin/python -m tracker.audit_kit  (both PDFs -> ~/Desktop)
"""
import glob
from pathlib import Path

import pandas as pd

from tracker.config import get_agent

_ROOT = Path(__file__).resolve().parent.parent
_PMPM = 23.0   # blended $/member/month commission estimate


# ── data gathering (every block optional) ────────────────────────────────────
def _gather():
    d = {"agent": get_agent()}
    snaps = sorted(glob.glob(str(_ROOT / "snapshots" / "*healthsherpa*.parquet")))
    if not snaps:
        raise SystemExit("No HealthSherpa snapshot ingested — the audit needs at least that.")
    snap = pd.read_parquet(snaps[-1])
    _ACT = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
    act = snap[snap["status"].isin(_ACT)]
    d["n_pol"] = len(act)
    d["n_mem"] = int(pd.to_numeric(act["applicant_count"], errors="coerce").fillna(1).sum())
    d["est_annual"] = round(d["n_mem"] * _PMPM * 12)
    d["snap"] = snap

    # Household mix
    sz = pd.to_numeric(act["applicant_count"], errors="coerce").fillna(1).astype(int).clip(lower=1)
    b = sz.where(sz < 6, 6)
    d["household"] = (pd.DataFrame({"n": b, "mem": sz.values})
                      .groupby("n").agg(Policies=("n", "size"), Members=("mem", "sum")))

    # Carrier mix (active book)
    d["carrier_mix"] = act.groupby(act["carrier"].astype(str))["carrier"].count() \
                          .sort_values(ascending=False).head(8)

    # Churn (real term dates)
    try:
        from tracker.dashboard import _build_mom_from_all_clients
        mom = _build_mom_from_all_clients(snap)
        d["mom_tail"] = mom.tail(4)[["Month", "New Policies", "Policies Lost", "Members Lost"]]
    except Exception:
        d["mom_tail"] = None

    # AOR at-risk (needs the scraped list — optional)
    try:
        from tracker.aor_defense import build_aor_defense
        adf = build_aor_defense()
        if adf is not None and not adf.empty:
            t = adf[adf["Type"] == "Taken"]
            d["aor_taken"] = len(t)
            d["aor_dollars"] = int(t[t["Handled"].fillna("") == ""]["Est $/yr"].sum())
            d["aor_top"] = t.head(8)[["Client", "Taken By", "Detected", "Carrier"]]
    except Exception:
        pass

    # Money owed (needs payments sheet + carrier books — optional)
    try:
        from tracker.commissions import parse_payments_sheet, build_gaps, audit_gaps
        from tracker.sheets import _open_sheet
        from tracker.config import load_settings
        s = load_settings()
        imp = next((s[k] for k in s if "impersonat" in k.lower()), None)
        pay = parse_payments_sheet(_open_sheet(s["payments_sheet_url"], imp))
        pay["payment_month"] = pd.to_datetime(pay["payment_month"], errors="coerce")
        pay["amount"] = pd.to_numeric(pay["amount"], errors="coerce")
        active = snap[snap["status"] == "Effectuated"].copy()
        eff = pd.to_datetime(active["effective_date"], errors="coerce")
        ref = pd.Timestamp.today().normalize()
        active["months_on_book"] = (ref.to_period("M").ordinal
                                    - eff.dt.to_period("M").map(
                                        lambda p: p.ordinal if pd.notna(p) else ref.to_period("M").ordinal)
                                    ).clip(lower=0)
        gaps = build_gaps(active, pay)
        books = _ROOT / "carrier_books"
        if books.exists() and any(books.glob("*.csv")):
            gaps = audit_gaps(gaps, pay, str(books))
            d["disputes"] = gaps[gaps.get("Dispute", pd.Series(dtype=str))
                                 .astype(str).str.contains("Dispute")]
        d["gaps_n"] = len(gaps)
    except Exception:
        pass
    return d


# ── shared PDF chrome ────────────────────────────────────────────────────────
def _styles():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    S = {}
    S["NAVY"] = colors.HexColor("#0f2740"); S["INK"] = colors.HexColor("#0f172a")
    S["SLATE"] = colors.HexColor("#475569"); S["LINE"] = colors.HexColor("#e2e8f0")
    S["LIGHT"] = colors.HexColor("#f1f5f9"); S["GRN"] = colors.HexColor("#16a34a")
    S["RED"] = colors.HexColor("#dc2626"); S["GOLD"] = colors.HexColor("#d97706")
    S["SEC"] = ParagraphStyle("SEC", fontName="Helvetica-Bold", fontSize=13, textColor=S["INK"],
                              spaceBefore=6, spaceAfter=2)
    S["BODY"] = ParagraphStyle("BODY", fontName="Helvetica", fontSize=9.5,
                               textColor=S["SLATE"], leading=14)
    S["KN"] = ParagraphStyle("KN", fontName="Helvetica-Bold", fontSize=17,
                             textColor=colors.white, alignment=1, leading=19)
    S["KL"] = ParagraphStyle("KL", fontName="Helvetica", fontSize=7.3,
                             textColor=colors.white, alignment=1, leading=9)
    S["TH"] = ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=8.5, textColor=colors.white)
    S["TD"] = ParagraphStyle("TD", fontName="Helvetica", fontSize=8.5, textColor=S["INK"])
    return S


def _header_fn(title, subtitle, S):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch

    def header(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(S["NAVY"]); canvas.rect(0, letter[1] - 1.15 * inch, letter[0], 1.15 * inch, fill=1, stroke=0)
        canvas.setFillColor(S["GRN"]); canvas.rect(0, letter[1] - 1.18 * inch, letter[0], 0.05 * inch, fill=1, stroke=0)
        canvas.setFillColor(colors.white); canvas.setFont("Helvetica-Bold", 20)
        canvas.drawString(0.75 * inch, letter[1] - 0.62 * inch, title)
        canvas.setFillColor(colors.HexColor("#cbd5e1")); canvas.setFont("Helvetica", 10.5)
        canvas.drawString(0.75 * inch, letter[1] - 0.86 * inch, subtitle)
        canvas.setFillColor(colors.HexColor("#64748b")); canvas.setFont("Helvetica", 7.5)
        canvas.drawString(0.75 * inch, 0.5 * inch, "Confidential — prepared for the named agent only")
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
        canvas.restoreState()
    return header


def _table(data, widths, S, right_cols=()):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle
    rows = [[Paragraph(str(c), S["TH"]) for c in data[0]]] + \
           [[Paragraph(str(c), S["TD"]) for c in r] for r in data[1:]]
    t = Table(rows, colWidths=widths, repeatRows=1)
    cmds = [("BACKGROUND", (0, 0), (-1, 0), S["NAVY"]),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, S["LIGHT"]]),
            ("GRID", (0, 1), (-1, -1), 0.4, S["LINE"]),
            ("TOPPADDING", (0, 0), (-1, -1), 4.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5)]
    for c in right_cols:
        cmds.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    t.setStyle(TableStyle(cmds))
    return t


def _kpi(num, lbl, bg, S):
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Table, TableStyle
    t = Table([[Paragraph(num, S["KN"])], [Paragraph(lbl, S["KL"])]], colWidths=[1.62 * inch])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg),
                           ("TOPPADDING", (0, 0), (0, 0), 10),
                           ("BOTTOMPADDING", (0, -1), (-1, -1), 10)]))
    return t


def _sec(title, S):
    from reportlab.platypus import HRFlowable, Paragraph, Spacer
    return [Spacer(1, 12), Paragraph(title, S["SEC"]),
            HRFlowable(width="100%", thickness=1.2, color=S["GRN"], spaceAfter=6, spaceBefore=1)]


# ── B1: the Commission Audit ─────────────────────────────────────────────────
def build_audit_pdf(out_dir: Path = None) -> Path:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    S = _styles()
    d = _gather()
    ag = d["agent"]
    out = Path(out_dir or (Path.home() / "Desktop")) / f"Commission_Audit_{ag['last_name']}.pdf"

    found = 0
    if "disputes" in d and d["disputes"] is not None:
        found += len(d["disputes"]) * _PMPM * 12          # rough $/yr per disputed policy
    found += d.get("aor_dollars", 0)

    story = [Spacer(1, 0.55 * inch)]
    cards = Table([[
        _kpi(f"${found:,.0f}", "RECOVERABLE $/YR FOUND", S["RED"], S),
        _kpi(f"{d['n_pol']:,}", f"ACTIVE POLICIES ({d['n_mem']:,} MEMBERS)", S["NAVY"], S),
        _kpi(f"${d['est_annual']:,}", "EST ANNUAL COMMISSION", S["GRN"], S),
        _kpi(f"{d.get('aor_taken', 0)}", "CLIENTS HELD BY ANOTHER AGENT", S["GOLD"], S),
    ]], colWidths=[1.75 * inch] * 4)
    cards.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 3),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    story += [cards]

    story += _sec("1.  Money You're Owed", S)
    if "disputes" in d and d["disputes"] is not None and len(d["disputes"]):
        dd = d["disputes"]
        rows = [["Client", "Carrier", "Policy #", "Situation"]]
        for _, r in dd.head(10).iterrows():
            rows.append([f"{r.get('First Name','')} {r.get('Last Name','')}",
                         r.get("Carrier", ""), r.get("Policy #", ""),
                         "Active with carrier — never paid"])
        story += [Paragraph(
            f"<b>{len(dd)} policies where the carrier confirms an active policy under your "
            f"NPN but no commission has ever been paid.</b> These are documented, "
            f"evidence-backed disputes ready to send to your commissions team.", S["BODY"]),
            Spacer(1, 4), _table(rows, [1.5 * inch, 1.3 * inch, 1.2 * inch, 2.2 * inch], S)]
    elif d.get("gaps_n"):
        story += [Paragraph(
            f"<b>{d['gaps_n']} active clients show no matching commission payment.</b> "
            f"Provide carrier book exports to verify which are documented disputes.", S["BODY"])]
    else:
        story += [Paragraph("Payments data not provided — this section is produced when you "
                            "share your commission statements (any spreadsheet format).", S["BODY"])]

    story += _sec("2.  Clients Being Taken From You", S)
    if d.get("aor_taken"):
        story += [Paragraph(
            f"<b>{d['aor_taken']} clients are actively insured but another agent now holds the "
            f"Agent of Record — ≈ ${d.get('aor_dollars', 0):,}/yr of commission redirected.</b> "
            f"Most clients don't know it happened; freshest changes are the most winnable.", S["BODY"])]
        if d.get("aor_top") is not None and len(d["aor_top"]):
            rows = [["Client", "Taken By", "When", "Carrier"]] + \
                   [[r["Client"], r["Taken By"] or "—", r["Detected"] or "—",
                     str(r["Carrier"])[:26]] for _, r in d["aor_top"].iterrows()]
            story += [Spacer(1, 4), _table(rows, [1.6 * inch, 1.5 * inch, 1.1 * inch, 2.0 * inch], S)]
    else:
        story += [Paragraph("Requires your HealthSherpa 'AOR at risk' list — included in the "
                            "standard export set.", S["BODY"])]

    story += _sec("3.  Retention — Where Your Book Leaks", S)
    if d.get("mom_tail") is not None:
        rows = [["Month", "New Policies", "Policies Lost", "Members Lost"]] + \
               [[r["Month"], int(r["New Policies"]), int(r["Policies Lost"]),
                 int(r["Members Lost"])] for _, r in d["mom_tail"].iterrows()]
        story += [_table(rows, [1.4 * inch, 1.5 * inch, 1.5 * inch, 1.5 * inch], S, right_cols=(1, 2, 3)),
                  Spacer(1, 4),
                  Paragraph("Most losses cluster at month-end non-payment. A grace-period call "
                            "list in the first week of each month recovers a large share.", S["BODY"])]

    story += _sec("4.  Book Composition", S)
    hh = d["household"]
    rows = [["Household Size", "Policies", "Members"]] + \
           [[("1 (single)" if int(i) == 1 else ("6+" if int(i) == 6 else str(int(i)))),
             int(r["Policies"]), int(r["Members"])] for i, r in hh.iterrows()]
    story += [_table(rows, [2.2 * inch, 1.5 * inch, 1.5 * inch], S, right_cols=(1, 2))]

    story += _sec("5.  What To Do With This", S)
    story += [Paragraph(
        "1. Send section 1 to your FMO/commissions contact — it's dispute-ready evidence.<br/>"
        "2. Call the section-2 clients newest-first: “I saw your plan got moved to a different "
        "agent — did you mean to do that?”<br/>"
        "3. Work the past-due list the first week of every month, before carriers term.<br/>"
        "4. Re-run this audit monthly — the leaks refill. That's the ongoing service.", S["BODY"])]

    doc = SimpleDocTemplate(str(out), pagesize=letter, topMargin=1.35 * inch,
                            bottomMargin=0.75 * inch, leftMargin=0.75 * inch,
                            rightMargin=0.75 * inch, title=f"Commission Audit — {ag['name']}")
    doc.build(story, onFirstPage=_header_fn("Commission Audit",
                                            f"Prepared for {ag['name']} (NPN {ag['npn']})", S),
              onLaterPages=_header_fn("Commission Audit", f"Prepared for {ag['name']}", S))
    return out


# ── B2: the instruction sheet ────────────────────────────────────────────────
def build_instructions_pdf(out_dir: Path = None) -> Path:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    S = _styles()
    out = Path(out_dir or (Path.home() / "Desktop")) / "Audit_Export_Instructions.pdf"
    story = [Spacer(1, 0.5 * inch)]
    story += [Paragraph("Five exports, ~15 minutes total. Send every file exactly as it downloads "
                        "— don't open or edit them. Only include the carriers you're appointed with.",
                        S["BODY"])]

    steps = [
        ("1 · HealthSherpa — client export (REQUIRED)",
         "Clients → <b>Export</b> → Date Range: <b>Custom</b> → type <b>01/01/2025</b> through today "
         "→ check <b>BOTH</b> boxes (“unsubmitted search &amp; claimed” and “3-way call-resolved”) → Export. "
         "⚠️ The date range is the #1 mistake — “Last 30 days” only exports a fraction of your book. "
         "Then: Exports → <b>Exports History</b> → wait for “Download Ready” → Download. "
         "Also: Clients → <b>AOR at risk</b> tab → screenshot every page of that list."),
        ("2 · Ambetter — broker.ambetterhealth.com",
         "Policies page → clear any search → click the <b>download icon</b> (top-right of the table) "
         "→ Download. You'll get a small .zip — send it as-is."),
        ("3 · Oscar — business.hioscar.com",
         "Individual book → <b>Export CSV</b> (top-right). It can take 5+ minutes to generate and "
         "downloads by itself — don't close the tab; check your Downloads folder."),
        ("4 · Anthem — brokerportal.anthem.com",
         "Book of Business → scroll to the client list → <b>Export Spreadsheet</b>."),
        ("5 · UnitedHealthcare — uhcjarvis.com",
         "Book of Business → <b>clear the “Plan Status: Active” filter</b> (important — keeps lapsed "
         "members visible) → Download → in the column picker, ALSO tick <b>“IFP – FFM APP ID”</b> "
         "under Additional Details → Download."),
        ("Commission statements (unlocks the “Money You're Owed” section)",
         "Whatever you have: carrier statement PDFs/CSVs, your FMO's payout report, or your own "
         "spreadsheet. Any format works — months, client names, and amounts are all we need."),
    ]
    for title, body in steps:
        story += _sec(title, S)
        story += [Paragraph(body, S["BODY"])]

    story += _sec("Sending your files", S)
    story += [Paragraph("These files contain client PII — don't email them unprotected. Use the "
                        "secure upload link you were given, or a password-protected zip.", S["BODY"])]

    doc = SimpleDocTemplate(str(out), pagesize=letter, topMargin=1.35 * inch,
                            bottomMargin=0.75 * inch, leftMargin=0.75 * inch,
                            rightMargin=0.75 * inch, title="Commission Audit — Export Instructions")
    hdr = _header_fn("Send Me These 5 Exports", "Everything I need to run your commission audit", S)
    doc.build(story, onFirstPage=hdr, onLaterPages=hdr)
    return out


if __name__ == "__main__":
    print("audit:", build_audit_pdf())
    print("instructions:", build_instructions_pdf())
