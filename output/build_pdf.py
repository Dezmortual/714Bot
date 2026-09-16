#!/usr/bin/env python3
"""Build the final PDF guide."""
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, Image, HRFlowable, KeepTogether, ListFlowable, ListItem
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os

OUT_DIR = "/home/user/714Bot/output"
PDF_PATH = "/home/user/714Bot/Most_Profitable_Trading_Strategy_Ever_Explained_Simply.pdf"

NAVY = colors.HexColor("#0B1D3A")
GOLD = colors.HexColor("#B8860B")
TEAL = colors.HexColor("#0B7A5F")
LIGHT_BG = colors.HexColor("#F4F6F9")
LIGHT_TEAL = colors.HexColor("#E6F4EF")
LIGHT_GOLD = colors.HexColor("#FFF8E1")
DARK = colors.HexColor("#1A1A1A")
GREY = colors.HexColor("#5D6D7E")
RED = colors.HexColor("#C0392B")

styles = getSampleStyleSheet()

sTitle = ParagraphStyle("Title2", parent=styles["Title"], fontSize=30, leading=34,
                        textColor=NAVY, alignment=TA_CENTER, spaceAfter=6, fontName="Helvetica-Bold")
sSubtitle = ParagraphStyle("Subtitle2", parent=styles["Normal"], fontSize=13, leading=18,
                           textColor=GREY, alignment=TA_CENTER, spaceAfter=4)
sH1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=17, leading=22, textColor=NAVY,
                     fontName="Helvetica-Bold", spaceBefore=16, spaceAfter=8, keepWithNext=True,
                     borderPadding=(0, 0, 6, 0))
sH2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=13, leading=17, textColor=TEAL,
                     fontName="Helvetica-Bold", spaceBefore=12, spaceAfter=6, keepWithNext=True)
sH3 = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=11, leading=15, textColor=NAVY,
                     fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=4)
sBody = ParagraphStyle("Body2", parent=styles["Normal"], fontSize=10, leading=15,
                       textColor=DARK, alignment=TA_JUSTIFY, spaceAfter=6)
sBodySmall = ParagraphStyle("BodySmall", parent=sBody, fontSize=9, leading=13.5)
sBullet = ParagraphStyle("Bullet2", parent=sBody, leftIndent=18, firstLineIndent=0, spaceAfter=4, bulletIndent=8)
sCenter = ParagraphStyle("Center2", parent=sBody, alignment=TA_CENTER)
sCaption = ParagraphStyle("Caption2", parent=styles["Normal"], fontSize=8.5, leading=12,
                          textColor=GREY, alignment=TA_CENTER, spaceAfter=8)
sBox = ParagraphStyle("Box", parent=sBody, fontSize=10, leading=15, textColor=NAVY)
sQuote = ParagraphStyle("Quote2", parent=sBody, fontSize=10, leading=15, textColor=colors.HexColor("#2C3E50"),
                        leftIndent=14, borderPadding=(8, 8, 8, 12), backColor=LIGHT_GOLD)

def header_footer(canvas, doc):
    canvas.saveState()
    # top bar
    canvas.setFillColor(NAVY)
    canvas.rect(0, A4[1] - 14*mm, A4[0], 14*mm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawString(15*mm, A4[1] - 8.5*mm, "THE MOST PROFITABLE TRADING STRATEGY EVER  •  EXPLAINED SIMPLY")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(A4[0] - 15*mm, A4[1] - 8.5*mm, "Sept 2026")
    # footer
    canvas.setFillColor(GREY)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawCentredString(A4[0]/2, 12*mm, f"Page {doc.page}  •  For education only — not financial advice  •  Sources listed at the end")
    canvas.restoreState()

def cover_footer(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica", 8)
    canvas.drawCentredString(A4[0]/2, 18*mm, "For education only — not financial advice. Sources listed inside.")
    canvas.restoreState()

def box_table(paras, bg=LIGHT_TEAL, border=TEAL):
    """paras: list of Paragraph objects"""
    data = [[paras]]
    t = Table(data, colWidths=[170*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 1.2, border),
        ("INNERPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
    ]))
    return t

def styled_table(headers, rows, col_widths=None):
    data = [headers] + rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#D5D8DC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    t.setStyle(TableStyle(style))
    return t

def P(text, style=sBody):
    return Paragraph(text, style)

story = []

# ================= COVER =================
# Build cover as part of story with spacers; we'll use first page func
story.append(Spacer(1, 28*mm))
story.append(P("THE MOST PROFITABLE", ParagraphStyle("pre", parent=sSubtitle, fontSize=14, textColor=TEAL, fontName="Helvetica-Bold", alignment=TA_CENTER)))
story.append(P("Trading Strategy<br/>Ever Documented", sTitle))
story.append(Spacer(1, 4*mm))
story.append(HRFlowable(width="22%", thickness=2.5, color=GOLD, spaceAfter=6, spaceBefore=6, hAlign="CENTER"))
story.append(P("The Jim Simons / Medallion Fund system — explained in plain English,<br/>with pictures, analogies, and lessons you can actually use.", sSubtitle))
story.append(Spacer(1, 6*mm))

cover_box = box_table([
    P("<b><font color=\"#0B7A5F\">THE 30-SECOND ANSWER:</font></b> From 1988–2018, Renaissance Technologies' <b>Medallion Fund</b> turned <b>$100 into ~$398.7 million</b> — about <b>66% per year before fees (~39–40% after fees)</b> — and <b>never had a losing year</b>. No other documented strategy comes close over 30+ years. This guide explains <b>how it works, in simple terms</b>.", sBox)
], bg=LIGHT_TEAL, border=TEAL)
story.append(cover_box)
story.append(Spacer(1, 6*mm))

cover_facts = [
    [P("<b><font color=\"#0B1D3A\">66%</font></b><br/><font color=\"#5D6D7E\" size=\"8\">avg / year before fees</font>", sCenter),
     P("<b><font color=\"#0B1D3A\">~40%</font></b><br/><font color=\"#5D6D7E\" size=\"8\">avg / year after fees</font>", sCenter),
     P("<b><font color=\"#0B1D3A\">0</font></b><br/><font color=\"#5D6D7E\" size=\"8\">losing years in 31 yrs</font>", sCenter),
     P("<b><font color=\"#0B1D3A\">150k+</font></b><br/><font color=\"#5D6D7E\" size=\"8\">trades on big days</font>", sCenter)],
]
tfacts = Table(cover_facts, colWidths=[42*mm]*4)
tfacts.setStyle(TableStyle([
    ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#D5D8DC")),
    ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#D5D8DC")),
    ("TOPPADDING", (0, 0), (-1, -1), 8),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
]))
story.append(tfacts)
story.append(Spacer(1, 8*mm))
story.append(P("Based on a deep web search: academic reconstructions, the book <i>The Man Who Solved the Market</i>, fund reporting &amp; data studies &nbsp;•&nbsp; September 2026", ParagraphStyle("covsmall", parent=sCaption, fontSize=8.5)))
story.append(P("Inside: proof &amp; numbers • how the strategy works • 7 secrets in simple words • a worked example • what YOU can copy • honest warnings • 30-day plan", ParagraphStyle("covsmall2", parent=sCaption, fontSize=8.5, textColor=TEAL)))

# ================= TOC =================
story.append(PageBreak())
story.append(P("What's inside", sH1))
story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D5D8DC"), spaceAfter=6, spaceBefore=4))
toc_items = [
    ("1", "The answer in 30 seconds — who wins?"),
    ("2", "The proof: numbers that broke Wall Street's brain"),
    ("3", "Who is Jim Simons? The math teacher who beat everyone"),
    ("4", "The strategy in ONE sentence (then in plain English)"),
    ("5", "The 7 secrets — explained like you're 12"),
    ("6", "A real example: the Coca-Cola vs Pepsi trade"),
    ("7", "Why you can't just copy-paste it"),
    ("8", "Your retail version: 5 rules you CAN copy"),
    ("9", "How the other legends compare"),
    ("10", "Honest warnings (read this)"),
    ("11", "Your 30-day starter plan"),
    ("12", "Sources & further reading"),
]
toc_rows = []
for n, t in toc_items:
    toc_rows.append([P(f"<b>{n}</b>", sBodySmall), P(t, sBodySmall)])
toc = Table(toc_rows, colWidths=[12*mm, 158*mm])
toc.setStyle(TableStyle([
    ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#EAEDED")),
    ("TOPPADDING", (0, 0), (-1, -1), 5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ("ALIGN", (2, 0), (2, -1), "RIGHT"),
]))
story.append(toc)
story.append(Spacer(1, 5*mm))
story.append(box_table([P("<b>How to read this guide:</b> You don't need math. Every idea comes with a <b>real-life analogy</b> and a <b>\"so what do I do?\"</b> line. Skim the green boxes for the key lessons if you're in a hurry.", sBox)], bg=LIGHT_GOLD, border=GOLD))

# ================= 1. ANSWER =================
story.append(P("1 &nbsp;•&nbsp; The answer in 30 seconds — who wins?", sH1))
story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D5D8DC"), spaceAfter=6, spaceBefore=4))
story.append(P("After searching across academic papers, fund records, books and data studies, <b>one winner stands alone</b>:", sBody))
story.append(box_table([P("<b>The Renaissance Technologies <font color=\"#0B7A5F\">Medallion Fund</font></b> (Jim Simons' quant fund, launched 1988) is the <b>most profitable trading strategy ever documented</b> over a long period: roughly <b>66% a year before fees / ~39–40% after fees</b>, compounded for <b>30+ years with no losing year</b>. One academic reconstruction: <b>$100 → $398.7 million (1988–2018)</b>.", sBox)]))
story.append(P("<b>What IS the strategy, in simple words?</b> Medallion is a <b>100% computer-run system</b> that scans hundreds of markets, finds <b>tiny, repeating price patterns</b> humans can't see, and trades them <b>thousands of times a day</b> — holding from minutes to a few days — while staying <b>hedged, diversified and strictly risk-controlled</b>, with <b>leverage</b> to turn small edges into huge returns. No gut feelings. No CNBC opinions. Just data + discipline + repetition.", sBody))
story.append(P("<b>Why it beats everything else:</b> Warren Buffett (~20% for 60 years) is the greatest <i>investor</i>. Larry Williams (+11,376% in one year) had the greatest <i>single year</i>. But nobody else has documented <b>~40% net for 30+ years without a down year</b> — including straight through the dot-com crash and the 2008 crisis, when Medallion was <i>up</i> while the world was down.", sBody))

# ================= 2. PROOF =================
story.append(P("2 &nbsp;•&nbsp; The proof: numbers that broke Wall Street's brain", sH1))
story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D5D8DC"), spaceAfter=6, spaceBefore=4))
story.append(P("Finance professor Bradford Cornell called Medallion \"<b>the ultimate counterexample</b>\" to the idea that markets can't be beaten — returns \"<b>far outstrip anything reported in the academic literature</b>.\" Here are the headline facts:", sBody))
headers = [P("<b>Metric</b>", sBodySmall), P("<b>Medallion Fund</b>", sBodySmall), P("<b>What it means (simply)</b>", sBodySmall)]
rows = [
    [P("Gross return 1988–2018", sBodySmall), P("<b>~63–66% / year</b>", sBodySmall), P("Before fees. The raw power of the system.", sBodySmall)],
    [P("Net return (what investors kept)", sBodySmall), P("<b>~39–40% / year</b>", sBodySmall), P("After Medallion's giant 5% + 44% fees. Still ~4× the S&P.", sBodySmall)],
    [P("$100 invested in 1988 → 2018", sBodySmall), P("<b>→ ~$398,700,000</b> (gross)", sBodySmall), P("Same $100 in S&P 500 → ~$1,910.", sBodySmall)],
    [P("Losing years (31 years)", sBodySmall), P("<b>Zero</b>", sBodySmall), P("Never down — even in 2000–02 and 2008.", sBodySmall)],
    [P("2008 financial crisis", sBodySmall), P("<b>~+82% net (~+152% gross)</b>", sBodySmall), P("S&P 500: −37%. Medallion <i>thrives</i> in chaos.", sBodySmall)],
    [P("Fees charged", sBodySmall), P("<b>5% + 44% of profits</b>", sBodySmall), P("Double the normal 2-and-20. Still worth it.", sBodySmall)],
    [P("Who can invest?", sBodySmall), P("<b>Employees only (since 1993)</b>", sBodySmall), P("You can't buy it. Fund capped ~$10–15B.", sBodySmall)],
]
story.append(styled_table(headers, rows, col_widths=[48*mm, 52*mm, 70*mm]))
story.append(Spacer(1, 4*mm))
story.append(Image(os.path.join(OUT_DIR, "_chart_growth.png"), width=170*mm, height=89*mm))
story.append(P("Figure 1 — $100 growing at Medallion's ~63% vs the S&P's ~10% (log scale). By 2018 the gap is $398.7M vs $1,910. Based on Cornell's reconstruction of reported gross returns.", sCaption))
story.append(Image(os.path.join(OUT_DIR, "_chart_year2008.png"), width=170*mm, height=76*mm))
story.append(P("Figure 2 — Crisis years: Medallion was up big when markets crashed. (Net figures widely reported; gross was even higher.)", sCaption))
story.append(box_table([P("<b>Plain-English takeaway:</b> Imagine two snowballs rolling for 30 years. The S&P snowball grows 10% a year. The Medallion snowball grows 40% a year <i>after</i> paying huge fees. Compounding turns that gap into a <b>mountain vs a pebble</b>.", sBox)], bg=LIGHT_TEAL, border=TEAL))

# ================= 3. WHO =================
story.append(P("3 &nbsp;•&nbsp; Who is Jim Simons? The math teacher who beat everyone", sH1))
story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D5D8DC"), spaceAfter=6, spaceBefore=4))
story.append(P("Jim Simons (1938–2024) wasn't a Wall Street banker. He was a <b>math professor, code-breaker and geometry prize-winner</b> who believed markets — like codes and shapes — hide <b>patterns you can find with math</b>. In 1982 he founded Renaissance Technologies and hired <b>mathematicians, physicists and computer scientists</b> instead of traders — including speech-recognition experts from IBM who had taught computers to understand human language.", sBody))
story.append(P("Their big insight: <b>don't hire people with market opinions — hire people who find patterns in data</b>. Early years were messy (the fund even lost money in 1989). But by the 1990s the system clicked — and then it compounded at ~40% net for decades. Simons stepped back around 2009–2010, yet returns <b>continued and even improved gross (75–80%)</b> — proof the <b>system</b>, not one man, was the edge. The story is told in Gregory Zuckerman's book <i>The Man Who Solved the Market</i>.", sBody))
story.append(P("\"<i>What you're really modeling is human behavior. Humans are most predictable in times of high stress — they act instinctively and panic.</i>\" — Kresimir Penavic, Renaissance", sQuote))
story.append(P("\"<i>There's no data like more data.</i>\" — Jim Simons' famous rule: collect everything, back to the 1800s if possible, and let the computer find what repeats.", sQuote))

# ================= 4. ONE SENTENCE =================
story.append(P("4 &nbsp;•&nbsp; The strategy in ONE sentence (then in plain English)", sH1))
story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D5D8DC"), spaceAfter=6, spaceBefore=4))
story.append(box_table([P("<b>ONE SENTENCE:</b> Medallion uses computers to find <b>small, statistically proven price patterns</b> across hundreds of markets and trades them <b>automatically, thousands of times</b>, hedged and risk-controlled, holding <b>minutes to days</b>.", sBox)], bg=LIGHT_GOLD, border=GOLD))
story.append(P("<b>The casino analogy (the whole strategy in 20 seconds):</b> A roulette wheel pays the casino only ~2.7% per spin — tiny. But the casino takes that tiny edge <b>thousands of times a day, every day, for years</b> — and gets rich with near-certainty. Medallion is the casino: its edge per trade is <b>tiny</b> (often well under 1%), but it trades <b>massive volume, diversified everywhere, with strict limits</b> — so the edge compounds into ~66% a year before fees. You don't need to be right big. You need to be <b>right slightly, very often</b>.", sBody))
story.append(Image(os.path.join(OUT_DIR, "_chart_edges.png"), width=170*mm, height=72*mm))
story.append(P("Figure 3 — Illustrative: casinos and Medallion both win with SMALL edges repeated at SCALE. Most human traders have no edge at all after costs.", sCaption))

# ================= 5. SEVEN SECRETS =================
story.append(P("5 &nbsp;•&nbsp; The 7 secrets — explained like you're 12", sH1))
story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D5D8DC"), spaceAfter=6, spaceBefore=4))
story.append(P("Nobody outside Renaissance knows the exact code — it's one of the best-kept secrets in finance. But from Zuckerman's book, employee quotes and academic work, the <b>framework</b> is well understood. Here are the 7 pillars, each with a simple analogy.", sBody))

secrets = [
    ("SECRET 1 — Don't ask WHY, ask WHAT happens next",
     "Forget the news story. Medallion doesn't care <i>why</i> a stock moved (CEO, earnings, wars). It only asks: <b>\"When this pattern happened 10,000 times before, what happened next?\"</b> If the answer repeats reliably, it bets on it.",
     "Weather app vs. weather philosopher: you don't need to know <i>why</i> it rains — you just need to know that <b>dark clouds + wind = rain 80% of the time</b>, so you carry an umbrella.",
     "So what do I do? Trade <b>rules and backtests</b>, not opinions. If you can't show a pattern worked 100+ times in the past, don't trade it."),
    ("SECRET 2 — Tiny edges × thousands of trades = fortune",
     "Each Medallion trade aims for a <b>tiny profit</b>. Win rate is often only ~50–55% — barely better than a coin flip. But with <b>hundreds of thousands of trades</b>, that sliver becomes enormous. Short holding periods (minutes to days) also mean <b>huge sample sizes</b>, so they can prove an edge is real, not luck.",
     "The shovel seller in a gold rush: he doesn't find one giant nugget — he sells <b>10,000 small shovels</b> and keeps a cut of each.",
     "So what do I do? Stop hunting one 10× trade. Build a system with a <b>small, repeatable edge</b> you can take 100s of times with low costs."),
    ("SECRET 3 — Prices snap back like rubber bands (mean reversion — the CORE engine)",
     "For over a decade, Medallion's bedrock was <b>reversion to the mean</b>: when a price stretches too far from its usual level or from its relatives, it tends to <b>snap back</b>. Classic example: futures that open far below yesterday's close tend to bounce up that day — and vice versa. One insider summed it up: <b>\"We make money from the reactions people have to price moves\"</b> — i.e., from panic and overreaction.",
     "A stretched rubber band: pull it too far and <b>let go — it flies back</b>. Medallion bets on the snap-back, with computers measuring exactly how far is \"too far.\"",
     "So what do I do? Learn basic mean-reversion tools (Bollinger Bands, RSI extremes, pairs). Only fade extremes <b>with a stop</b> — rubber bands can snap <i>and keep going</i>."),
    ("SECRET 4 — Sometimes, ride the wave (trend / momentum)",
     "Not everything snaps back — sometimes winners keep winning for a while. Medallion also runs <b>trend models</b> (e.g., stocks that rallied last week tend to drift further short-term). They blend reversion + trend so the system makes money in <b>both choppy and trending</b> markets. When a style stops working (their momentum model bled in the dot-com bust), they <b>switch it off automatically</b>.",
     "Surfing: most waves fizzle (fade them), but when a big one builds, you <b>hop on and ride</b> — then jump off before it crashes.",
     "So what do I do? Don't marry one style. Have <b>one trend rule + one range rule</b>, and track which regime you're in."),
    ("SECRET 5 — Bet on everything, a little bit (extreme diversification)",
     "Medallion trades <b>hundreds of markets</b> — stocks, futures, currencies, commodities worldwide — long AND short. Each bet is small and <b>uncorrelated</b>, so no single trade can hurt. Net market exposure stays near zero (<b>market-neutral</b>): they profit from <b>relative</b> moves, not from guessing whether the market goes up or down. That's why they made money in 2008 while holding almost no directional risk.",
     "1,000 lottery tickets in 1,000 different draws vs. all your money on one ticket. One loss means nothing; the <b>average</b> is what pays you.",
     "So what do I do? Never go all-in. Risk <b>1–2% per trade</b>, spread across a few uncorrelated ideas, and use hedges (or simply smaller size)."),
    ("SECRET 6 — Let the robot trade. Never your feelings (100% systematic)",
     "Every Medallion trade is dictated by the model — <b>zero human override</b>. Humans panic, get greedy, and \"feel\" the market is different this time. The computer doesn't. They even <b>hide their own trades</b> (scaling in/out at random times) so competitors can't copy them. Rule: if you override the system, you can't backtest it — so you no longer <i>have</i> a system.",
     "Autopilot on a plane: in a storm, the panicking passenger would yank the controls and crash. The <b>autopilot just follows its instruments</b> and lands safely.",
     "So what do I do? Write your rules on one page. <b>If it's not in the rules, you don't trade it.</b> Journal every override — you'll quickly see they cost you money."),
    ("SECRET 7 — Borrow carefully + automatic brakes (leverage + risk engine)",
     "Because each edge is small but high-probability and diversified, Medallion uses <b>big leverage (~12–20×)</b> to magnify it — like a magnifying glass on sunlight. Crucially, leverage sits on top of an <b>automatic risk engine</b>: when volatility spikes or a model misbehaves, positions <b>shrink automatically</b>. Contrast LTCM (blew up): \"<i>LTCM believed its models were truth. We never believed ours reflected reality — just some aspects of it.</i>\"",
     "A race car with amazing brakes: the engine (leverage) makes it fast, but the <b>brakes (risk controls)</b> are why the driver survives. Amateurs add engine with no brakes — and crash.",
     "So what do I do? Beginners: <b>NO leverage</b> until profitable for 6–12 months. Then tiny leverage, always with a <b>max daily loss + per-trade stop</b> that cuts you off automatically."),
]

for title, body, analogy, action in secrets:
    story.append(P(title, sH2))
    story.append(P(body, sBody))
    story.append(box_table([P(f"<b>Analogy:</b> {analogy}<br/><br/><b>Your move:</b> {action}", sBox)], bg=colors.white, border=colors.HexColor("#D5D8DC")))
    story.append(Spacer(1, 2*mm))

# ================= 6. EXAMPLE =================
story.append(P("6 &nbsp;•&nbsp; A real example: the Coca-Cola vs Pepsi trade", sH1))
story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D5D8DC"), spaceAfter=6, spaceBefore=4))
story.append(P("The cleanest illustration of Medallion-style <b>statistical arbitrage / pairs trading</b> — no jargon needed:", sBody))
steps = [
    "<b>Step 1 — Find two twins.</b> Coca-Cola and Pepsi almost always move together (same industry, same customers). The computer studies 20 years of data and learns their normal gap.",
    "<b>Step 2 — Wait for a weird stretch.</b> One day Coke jumps +3% on hype while Pepsi sits flat. History says: <b>9 times out of 10, this gap closes within hours/days</b>.",
    "<b>Step 3 — Bet on the snap-back (hedged).</b> The system <b>shorts the expensive one (Coke) + buys the cheap one (Pepsi)</b> in matched sizes. If the whole market crashes, both fall together — the <i>gap</i> is what matters, not the direction.",
    "<b>Step 4 — Exit when normal returns.</b> Gap closes → both legs closed for a <b>small profit</b>. Gap widens past a limit → <b>auto stop-out</b>, tiny loss. No debating, no hoping.",
    "<b>Step 5 — Repeat 10,000×.</b> Coke/Pepsi is one pair. Medallion runs this logic on <b>thousands of pairs and patterns across the globe, simultaneously</b> — that's the fortune.",
]
for st in steps:
    story.append(P("• &nbsp;" + st, sBullet))
story.append(Spacer(1, 2*mm))
story.append(box_table([P("<b>Why this is genius:</b> You're not predicting whether soda stocks go UP or DOWN (hard). You're predicting that <b>two twins will move back together</b> (much easier, statistically). Multiply by leverage + thousands of twins = Medallion.", sBox)], bg=LIGHT_TEAL, border=TEAL))

# ================= 7. WHY CANT COPY =================
story.append(P("7 &nbsp;•&nbsp; Why you can't just copy-paste it (5 barriers)", sH1))
story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D5D8DC"), spaceAfter=6, spaceBefore=4))
story.append(P("If it's so good, why doesn't everyone do it? Because Medallion's moat is enormous:", sBody))
barriers = [
    [P("<b>#</b>", sBodySmall), P("<b>Barrier</b>", sBodySmall), P("<b>What it means for you</b>", sBodySmall)],
    [P("1", sBodySmall), P("<b>Secret code + top scientists</b>", sBodySmall), P("90+ PhDs, decades of research, one giant secret model. You can't download it.", sBodySmall)],
    [P("2", sBodySmall), P("<b>Insane data + computing</b>", sBodySmall), P("Every tick, order-book depth, news sentiment — back to the 1800s. Costs millions.", sBodySmall)],
    [P("3", sBodySmall), P("<b>Super-cheap trading costs</b>", sBodySmall), P("Tiny edges die if fees/slippage are high. Medallion executes better than any retail broker.", sBodySmall)],
    [P("4", sBodySmall), P("<b>Bank leverage on tap</b>", sBodySmall), P("12–20× leverage at elite rates because their Sharpe ratio (~2–7) is absurd. You won't get those terms.", sBodySmall)],
    [P("5", sBodySmall), P("<b>Capacity cap</b>", sBodySmall), P("Capped at ~$10–15B and closed since 1993 — the edges <i>only</i> work at limited size. Scale kills them.", sBodySmall)],
]
bt = Table(barriers, colWidths=[10*mm, 55*mm, 105*mm], repeatRows=1)
bt.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), NAVY),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
    ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#D5D8DC")),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
]))
story.append(bt)
story.append(Spacer(1, 3*mm))
story.append(P("Bottom line: <b>you can't BE Medallion. But you can THINK like Medallion</b> — and that alone puts you ahead of 90% of traders. That's next.", sBody))

# ================= 8. RETAIL VERSION =================
story.append(P("8 &nbsp;•&nbsp; Your retail version: 5 rules you CAN copy", sH1))
story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D5D8DC"), spaceAfter=6, spaceBefore=4))
story.append(P("You don't need a PhD or a supercomputer. Steal the <b>principles</b>, shrink them to your size:", sBody))
rules = [
    ("RULE 1 — Trade rules, not feelings (be the casino)",
     "Write a 1-page plan: <b>when you enter, when you exit, how much you risk</b>. Backtest it on 100+ past trades (even by hand on TradingView). If it has no proven edge, don't fund it. Medallion's version: <i>\"Don't override the computer.\"</i> Your version: <b>don't override the plan</b>."),
    ("RULE 2 — Small edge, many reps, low costs",
     "Pick <b>ONE simple setup</b> (e.g., RSI pullback in an uptrend, or opening-range breakout) and trade only that, 100s of times. Keep costs tiny: liquid markets, limit orders, no overtrading. Track win rate + average win/loss — your \"casino math.\""),
    ("RULE 3 — Mean-reversion starter (the Medallion flavor)",
     "Example template (educational, not advice): on a daily uptrend (price above 200-day average), when RSI(2) drops below 10 (stretched rubber band), buy small with a stop below the recent low and exit at the 5-day average. Backtest first. Expect <b>many small wins + occasional small losses</b> — exactly the Medallion shape, minus leverage."),
    ("RULE 4 — Risk like a paranoid robot",
     "Risk <b>1% per trade, max 3–5% per day</b>. If you hit the daily stop, <b>you're done — computer says no</b>. No averaging down, no \"it'll come back.\" Size = Risk ÷ Stop distance. Beginners: <b>no leverage</b>. This single rule prevents 90% of blow-ups."),
    ("RULE 5 — Diversify + review weekly",
     "Trade 2–4 uncorrelated ideas/markets instead of one. Every weekend, review: <b>did I follow rules? What's my edge this month? What should I switch OFF?</b> (Medallion kills dead models fast — you should kill dead setups fast too.)"),
]
for title, body in rules:
    story.append(P(title, sH2))
    story.append(P(body, sBody))
story.append(Spacer(1, 2*mm))
story.append(box_table([P("<b>Your one-page plan template:</b> (1) Market + timeframe &nbsp; (2) Setup + entry trigger &nbsp; (3) Stop-loss &nbsp; (4) Take-profit &nbsp; (5) Position size (1% risk) &nbsp; (6) Max trades/day &nbsp; (7) When I STOP trading (daily loss / 3 losses in a row). <b>Print it. Sign it. Follow it like code.</b>", sBox)], bg=LIGHT_GOLD, border=GOLD))

# ================= 9. COMPARE =================
story.append(P("9 &nbsp;•&nbsp; How the other legends compare", sH1))
story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D5D8DC"), spaceAfter=6, spaceBefore=4))
story.append(P("Medallion is #1 on long-term documented returns. But other legends teach different lessons:", sBody))
cheaders = [P("<b>Legend / Strategy</b>", sBodySmall), P("<b>Record</b>", sBodySmall), P("<b>Style in simple words</b>", sBodySmall), P("<b>Lesson</b>", sBodySmall)]
crows = [
    [P("<b>Medallion (Simons)</b>", sBodySmall), P("~39–40% net, 30+ yrs, 0 down yrs", sBodySmall), P("Computer patterns, minutes–days, hedged", sBodySmall), P("Systems + data beat gut feeling", sBodySmall)],
    [P("Warren Buffett", sBodySmall), P("~20% for 60 yrs", sBodySmall), P("Buy great companies cheap, hold forever", sBodySmall), P("Patience + compounding wins", sBodySmall)],
    [P("Turtle Traders (Dennis)", sBodySmall), P("$1,600 → $200M; trend rules", sBodySmall), P("Ride big trends with breakouts", sBodySmall), P("Simple rules + discipline work", sBodySmall)],
    [P("George Soros", sBodySmall), P("£1B in 1 day (1992)", sBodySmall), P("Huge macro bets when odds skew", sBodySmall), P("Bet big when you're certain", sBodySmall)],
    [P("Paul Tudor Jones", sBodySmall), P("+125% in 1987", sBodySmall), P("Macro + charts + strict stops", sBodySmall), P("Defense first: cut losses fast", sBodySmall)],
    [P("Larry Williams", sBodySmall), P("+11,376% in 1 yr (1987)", sBodySmall), P("Short-term seasonal/commodity swings", sBodySmall), P("Small accounts can sprint — then bank it", sBodySmall)],
    [P("Dalio / Bridgewater", sBodySmall), P("Largest hedge fund; All-Weather", sBodySmall), P("Balanced across economies", sBodySmall), P("Diversify across regimes", sBodySmall)],
]
story.append(styled_table(cheaders, crows, col_widths=[36*mm, 42*mm, 50*mm, 42*mm]))
story.append(Spacer(1, 3*mm))
story.append(P("Notice the pattern: <b>every legend has strict rules + risk control</b>. None of them \"YOLO trade on vibes.\" Style differs; <b>discipline doesn't</b>.", sBody))

# ================= 10. WARNINGS =================
story.append(P("10 &nbsp;•&nbsp; Honest warnings (read this before you trade)", sH1))
story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D5D8DC"), spaceAfter=6, spaceBefore=4))
warnings = [
    "<b>Past ≠ future.</b> Even Medallion's edges decay — they constantly research new ones and kill old ones. Any strategy you copy can stop working.",
    "<b>Leverage kills.</b> Medallion's 12–20× leverage works because of elite hedging, costs and auto-brakes. Retail leverage without those = fast account death. Start with zero leverage.",
    "<b>Costs eat small edges.</b> Spreads, commissions and slippage destroy high-frequency ideas at retail size. Trade liquid markets, use limits, trade less often than you want to.",
    "<b>Survivorship bias is real.</b> You hear about Larry Williams' +11,376% year — not the thousands who blew up trying. Trade money you can afford to lose.",
    "<b>No course can sell you Medallion.</b> Anyone claiming to sell \"the Medallion strategy\" is lying — the code has never left Renaissance. Learn principles, not promises.",
]
for w in warnings:
    story.append(P("• &nbsp;" + w, sBullet))
story.append(Spacer(1, 2*mm))
story.append(box_table([P("<b><font color=\"#C0392B\">THE GOLDEN RULE:</font></b> If you can't explain your edge in one sentence, backtest it on 100+ trades, and state your max loss before you click — <b>don't click</b>.", sBox)], bg=colors.HexColor("#FDEDEC"), border=RED))

# ================= 11. PLAN =================
story.append(P("11 &nbsp;•&nbsp; Your 30-day starter plan", sH1))
story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D5D8DC"), spaceAfter=6, spaceBefore=4))
plan = [
    [P("<b>Week</b>", sBodySmall), P("<b>Do this</b>", sBodySmall), P("<b>Done when…</b>", sBodySmall)],
    [P("Week 1", sBodySmall), P("Read this guide twice. Pick <b>ONE</b> market + <b>ONE</b> setup. Open a demo account.", sBodySmall), P("1-page plan written", sBodySmall)],
    [P("Week 2", sBodySmall), P("Backtest your setup on <b>100 past trades</b> (TradingView replay). Log win% + avg win/loss.", sBodySmall), P("You know your edge (or lack of it)", sBodySmall)],
    [P("Week 3", sBodySmall), P("Demo-trade the plan with <b>1% risk</b> rules. No overrides. Journal every trade.", sBodySmall), P("20+ demo trades, rules followed", sBodySmall)],
    [P("Week 4", sBodySmall), P("Review: keep, tweak or kill the setup. Only fund a tiny live account if demo was disciplined + profitable after costs.", sBodySmall), P("Go / no-go decision, in writing", sBodySmall)],
]
story.append(styled_table(plan[0], plan[1:], col_widths=[22*mm, 95*mm, 53*mm]))
story.append(Spacer(1, 3*mm))
story.append(P("Recommended next reads: <i>The Man Who Solved the Market</i> (Zuckerman) for the story • Cornell's \"Medallion Fund: The Ultimate Counterexample\" for the math • <i>Market Wizards</i> (Schwager) for trader psychology • Your broker's demo + TradingView for practice.", sBody))

# ================= 12. SOURCES =================
story.append(P("12 &nbsp;•&nbsp; Sources & further reading", sH1))
story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D5D8DC"), spaceAfter=6, spaceBefore=4))
story.append(P("Key figures in this guide (66% gross / ~39–40% net, $100 → $398.7M, zero losing years, 2008 +82% net, 5-and-44 fees, 150k+ trades/day, 12–20× leverage) are the widely-reported numbers from the sources below. Medallion is private and doesn't publish audited statements — figures are reconstructions from reporting + academia, but they are consistent across sources.", sBody))
sources = [
    "Cornell, B. — “Medallion Fund: The Ultimate Counterexample” (Cornell Capital Group). $100 → $398.7M (1988–2018), 63.3% CAGR gross, 66.1% mean, zero down years, beta ≈ −1.",
    "Zuckerman, G. — <i>The Man Who Solved the Market</i> (2019) + interviews: reversion core, 5-minute patterns, leverage via high Sharpe, auto risk cuts, “basket” trades.",
    "Quartr / TrendSpider / Acquired.fm deep-dives on Renaissance: 39.9–40% net CAGR 1988–2022, $1,000 → ~$90M net, IBM speech-team hires, Kelly sizing, capped fund.",
    "247WallSt (2026) + IWP Finance case study: 5% + 44% fees, gross rising to 75–80% post-Simons, Senate/tax history, Sharpe > 2.",
    "Novel Investor notes on <i>The Man Who Solved the Market</i>: pair/reversion quotes, “we make money from reactions to price moves,” momentum switch-off in 2000.",
    "HyroTrader (2026, data roundup): trend-following 29–58% CAGR studies, mean-reversion 68–71% win rates, breakout +1,600% (2016–23) — context for Sections 5 & 9.",
    "Strike.Money / Plus500 GOAT lists: Buffett ~20%, Soros £1B day, Tudor +125.9% (1987), Williams +11,376% (1987), Lynch 29.2% — context for Section 9.",
]
for i, s in enumerate(sources, 1):
    story.append(P(f"<b>[{i}]</b> &nbsp;{s}", sBullet))
story.append(Spacer(1, 4*mm))
story.append(box_table([P("<b>DISCLAIMER:</b> This PDF is <b>education only — not financial advice</b>. Trading involves substantial risk of loss. Nothing here recommends any security or strategy. Figures about private funds are estimates from public sources. Do your own research and consider a licensed professional before risking money.", sBodySmall)], bg=colors.white, border=GREY))
story.append(Spacer(1, 6*mm))
story.append(P("Made for you — September 2026 &nbsp;•&nbsp; deep web research, explained simply &nbsp;•&nbsp; good luck, and trade like the casino, not the gambler.", sCaption))

# Build with cover page func handling: first page different? Simplify: use header_footer for all after cover by custom onFirstPage
doc = SimpleDocTemplate(PDF_PATH, pagesize=A4,
                        topMargin=20*mm, bottomMargin=16*mm,
                        leftMargin=15*mm, rightMargin=15*mm,
                        title="The Most Profitable Trading Strategy Ever — Explained Simply",
                        author="Arena.ai Agent")

# We need cover without header bar: trick — build cover pages separately? Simplest: keep header on all; cover still looks fine.
doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
print("PDF saved:", PDF_PATH)
