"""Render the canonical project specification (requires ReportLab).

Usage: python3 scripts/render_spec.py /absolute/path/specification.pdf
Documentation tooling only; never required by the running application.
"""
from pathlib import Path
import html
import re
import sys
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, Image, KeepTogether,
)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(sys.argv[1]).resolve()
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
FONT = Path('/usr/share/fonts/truetype/dejavu')
for name, file in [('Spec', 'DejaVuSans.ttf'), ('SpecBold', 'DejaVuSans-Bold.ttf'),
                   ('SpecMono', 'DejaVuSansMono.ttf')]:
    pdfmetrics.registerFont(TTFont(name, str(FONT / file)))
pdfmetrics.registerFontFamily('Spec', normal='Spec', bold='SpecBold', italic='Spec', boldItalic='SpecBold')
INK = colors.HexColor('#152033')
MUTED = colors.HexColor('#526174')
BLUE = colors.HexColor('#15658c')
PALE = colors.HexColor('#edf4f8')
WIDTH, HEIGHT = A4
CONTENT = WIDTH - 100
styles = {
    'body': ParagraphStyle('body', fontName='Spec', fontSize=9.4, leading=13.6,
                           textColor=INK, spaceAfter=7, splitLongWords=True),
    'h1': ParagraphStyle('h1', fontName='SpecBold', fontSize=17, leading=22,
                         textColor=INK, spaceBefore=16, spaceAfter=12, keepWithNext=True),
    'h2': ParagraphStyle('h2', fontName='SpecBold', fontSize=11.1, leading=16,
                         textColor=BLUE, spaceBefore=12, spaceAfter=7, keepWithNext=True),
    'cell': ParagraphStyle('cell', fontName='Spec', fontSize=8.1, leading=11.5,
                           textColor=INK, splitLongWords=True),
    'th': ParagraphStyle('th', fontName='SpecBold', fontSize=8.1, leading=11.5,
                         textColor=colors.white, splitLongWords=True),
    'bullet': ParagraphStyle('bullet', fontName='Spec', fontSize=9.4, leading=13.6,
                             textColor=INK, leftIndent=13, firstLineIndent=-10,
                             spaceAfter=6, splitLongWords=True),
    # A list item nested under another (two or more leading spaces in the Markdown).
    'bullet2': ParagraphStyle('bullet2', fontName='Spec', fontSize=9.2, leading=13.2,
                              textColor=INK, leftIndent=27, firstLineIndent=-10,
                              spaceAfter=4, splitLongWords=True),
}

def normal(text):
    return text.replace('\u2011', '-').replace('\u2013', '-').replace('\u2014', '-')

def inline(text):
    text = html.escape(normal(text))
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    parts = text.split('`')
    for n in range(0, len(parts), 2):  # outside code spans only
        # *emphasis* (the font family has no italic face): the words themselves, without the asterisks.
        parts[n] = re.sub(r'(?<![\w*])\*(?!\s)([^*]+?)(?<!\s)\*(?![\w*])', r'\1', parts[n])
    for n in range(1, len(parts), 2):
        parts[n] = '<font name="SpecMono" size="8.2">' + parts[n] + '</font>'
    text = ''.join(parts)
    # The canonical spec uses named sibling files; don't create broken sandbox URLs.
    text = re.sub(r'\[([^]]+)\]\(([^)]+)\)', r'\1', text)
    return text

class SpecDoc(BaseDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and hasattr(flowable, 'section_title'):
            label, key = flowable.section_title, flowable.section_key
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(label, key, level=0, closed=False)
            self.notify('TOCEntry', (0, label, self.page, key))

def chrome(canvas, doc):
    canvas.saveState()
    if doc.page > 1:
        canvas.setStrokeColor(colors.HexColor('#d6e0e8'))
        canvas.setLineWidth(.6)
        canvas.line(50, HEIGHT-40, WIDTH-50, HEIGHT-40)
        canvas.setFont('SpecBold', 8)
        canvas.setFillColor(INK)
        canvas.drawString(50, HEIGHT-30, 'VROSTBRIDGE  /  PROJECT SPECIFICATION')
        canvas.setFont('Spec', 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(WIDTH-50, HEIGHT-30, 'Host and client 1.0.0.12')
    canvas.setFont('Spec', 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(50, 29, 'Documentation 2026-10-13 / 1.0.0.12')
    canvas.drawRightString(WIDTH-50, 29, f'{doc.page}')
    canvas.restoreState()

doc = SpecDoc(str(OUTPUT), pagesize=A4, title='VROSTBridge - Full Project Specification',
              author='JagerBombastic', subject='VROSTBridge host, Bazzite client and Windows client 1.0.0.12; documentation 2026-10-13',
              leftMargin=50, rightMargin=50, topMargin=59, bottomMargin=49)
frame = Frame(50, 49, CONTENT, HEIGHT-108, leftPadding=0, rightPadding=0,
              topPadding=0, bottomPadding=0)
doc.addPageTemplates(PageTemplate(id='body', frames=[frame], onPage=chrome))
story = [Spacer(1, 56)]
MARK = ROOT/'brand/assets/logo/vrostbridge-mark.png'  # the symbol, no text
from reportlab.lib.utils import ImageReader  # noqa: E402
mark_width, mark_height = ImageReader(str(MARK)).getSize()
logo = Image(str(MARK), width=110, height=110 * mark_height / mark_width)
logo.hAlign = 'LEFT'
story += [logo, Spacer(1, 24)]
cover_title = ParagraphStyle('cover', fontName='SpecBold', fontSize=34, leading=40, textColor=INK, spaceAfter=12)
subtitle = ParagraphStyle('subtitle', fontName='Spec', fontSize=21, leading=29, textColor=BLUE, spaceAfter=26)
story += [Paragraph('VROSTBridge', cover_title), Paragraph('Full project specification', subtitle)]
story += [Paragraph('An open-source remote PCVR bridge<br/><i>VR Rendering OutSide The Box – Bridge</i><br/>Windows rendering host <b>1.0.0.12</b><br/>Bazzite headset client <b>1.0.0.12</b><br/>Windows headset client <b>1.0.0.12</b><br/>MIT licence, by JagerBombastic', styles['body']), Spacer(1, 22)]
box = Table([[Paragraph('<b>Current handoff</b><br/>Complete architecture, requirements, implementation boundaries, build and install contracts, and release acceptance, revised for 1.0.0.12 (from 1.0.0.11\'s first session on hardware: the automatic bitrate back to where the session started once the headset PC\'s GPU clock is held higher, frames that never arrived not counted against it, the GPU clock the last session needed held from the start, and the add-on warning limited to add-ons from outside SteamVR), 1.0.0.11 (a lighter picture on the headset PC, so a headset\'s full panel resolution fits lighter headset PCs: the frame pass with about a third less work per pixel and one shader for every GPU, the pixels the lenses never show skipped, each eye shown a step smaller rather than at half the frame rate when the graphics fall behind, the pass measured on the GPU, and the Beyond\'s 2560 size called Native), 1.0.0.10 (from the 1.0.0.9 test: the menu button forwarded as one clean press with the other controls kept through a short loss of focus; Game Mode\'s SteamVR started without gamescope\'s display marker and given the headset display under a plain name, a failure to take it explained at once, and the last Game Mode start kept for reports; a bitrate step for uneven arrival undone when it did not help; three tries for a base station, on Bazzite with a search before each retry and its signal), 1.0.0.9 (clean game audio: a player of VROSTBridge\'s own on both clients and the host\'s capture read straight from Windows; lost video and audio pieces rebuilt from parity; Baballonia yielding a busy processor; base stations one at a time; the 3 newest client reports kept on the host), 1.0.0.8 (steadier streams: automatic bitrate held to what the picture needs and changed on the host while the stream runs, pacing below the loss-free rate, the Bazzite headset PC balanced during sessions, base stations on at Start and off at closing, the gaze centred per calibration, and recommended settings in one step), 1.0.0.7 (a Windows client for the headset PC, one version for every package) and the releases before it, which section 2 summarises.', styles['body'])]], colWidths=[CONTENT])
box.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),PALE),('BOX',(0,0),(-1,-1),.5,colors.HexColor('#c4d8e5')),('LEFTPADDING',(0,0),(-1,-1),16),('RIGHTPADDING',(0,0),(-1,-1),16),('TOPPADDING',(0,0),(-1,-1),14),('BOTTOMPADDING',(0,0),(-1,-1),9)]))
story += [box, Spacer(1, 22), Paragraph('<b>Validation boundary</b><br/>Index operation is confirmed by the maintainer, and so are the Beyond 2e picture and tracking through the bridge (RC4.5 test); the 1.0.0.2 test confirmed Baballonia\'s calibration over the stream and the eye-tracked foveated stream in Desktop Mode; the RC1.3 projection checks remain on the acceptance list. The 1.0.0.4 test confirmed true blacks with 10-bit colour off and the High size (now Native) looking and tracking reasonably well; the 1.0.0.5 test a more responsive sharp region (the eye cameras at 90 pictures a second) and black blacks with the 8-bit fallback; the 1.0.0.8 test much steadier motion and picture; the 1.0.0.9 test clean game audio and, over Wi-Fi, every frame that would have been lost rebuilt from parity; the first session of 1.0.0.11 Native with eye-tracked foveation at the headset\'s refresh and the frame pass at 1.9 ms of GPU time a frame. Other RC2 to 1.0.0.12 features (including the network, power and wake integration, and the RC4.1 to 1.0.0.12 changes) are contract-tested only; the RC4.5 eye tracking was also run end to end in a sandbox with the real Baballonia. The Windows client (1.0.0.7) is built and tested in code only (unit tests, the native contract tests under Wine with a software Vulkan driver, and the installer, companion and page under Wine): it has not run on Windows hardware. Game Mode, calibrated gaze and the Windows client have explicit remaining gates.', styles['body']), PageBreak()]
story += [Paragraph('Contents', styles['h1'])]
toc = TableOfContents()
toc.levelStyles = [ParagraphStyle('toc', fontName='Spec', fontSize=10, leading=15,
                                  textColor=INK, spaceBefore=6, leftIndent=0,
                                  firstLineIndent=0, rightIndent=20)]
story += [toc, Spacer(1, 20), Paragraph('Canonical text: docs/PROJECT_SPEC.md in the source archive. The PDF presents that specification with a cover and navigation; dated release reports remain in docs/evidence/.', styles['body']), PageBreak()]

lines = (ROOT/'docs/PROJECT_SPEC.md').read_text().splitlines()
i = 1  # title already represented on cover
while i < len(lines):
    line = lines[i].strip()
    if not line:
        i += 1
        continue
    if line.startswith('## '):
        title = normal(line[3:])
        p = Paragraph(inline(title), styles['h1'])
        p.section_title = title
        p.section_key = 'section-' + title.split('.')[0]
        story.append(p); i += 1
    elif line.startswith('### '):
        story.append(Paragraph(inline(line[4:]), styles['h2'])); i += 1
    elif line.startswith('#### '):
        story.append(Paragraph('<b>' + inline(line[5:]) + '</b>', styles['body'])); i += 1
    elif line.startswith('|'):
        rows = []
        while i < len(lines) and lines[i].strip().startswith('|'):
            cells = [x.strip() for x in lines[i].strip().strip('|').split('|')]
            if not all(re.fullmatch(r'[:\- ]+', x) for x in cells): rows.append(cells)
            i += 1
        count = len(rows[0])
        if count == 2:
            widths = [CONTENT*.32, CONTENT*.68]
        elif rows[0][0] == 'ID':
            widths = [CONTENT*.105, CONTENT*.535, CONTENT*.36]
        elif rows[0][0] == 'Area':  # the release baseline: long current-state cells (1.0.0.4, 1.0.0.5)
            widths = [CONTENT*.15, CONTENT*.55, CONTENT*.30]
        else:
            widths = [CONTENT*.26, CONTENT*.37, CONTENT*.37]
        data = [[Paragraph(inline(cell), styles['th' if n == 0 else 'cell']) for cell in row] for n, row in enumerate(rows)]
        table = Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT', splitInRow=1)  # a row taller than a page splits
        table.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),INK),('VALIGN',(0,0),(-1,-1),'TOP'),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white, PALE]),
            ('LINEBELOW',(0,0),(-1,0),.4,INK),
            ('LINEBELOW',(0,1),(-1,-1),.3,colors.HexColor('#dce5eb')),
            ('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),
            ('TOPPADDING',(0,0),(-1,-1),6.5),('BOTTOMPADDING',(0,0),(-1,-1),6.5),
        ]))
        story += [table, Spacer(1, 12)]
    elif line.startswith('- ') or re.match(r'^\d+\. ', line):
        nested = lines[i].startswith('  ')
        content = (('\u2013 ' if nested else '- ') + inline(line[2:])) if line.startswith('- ') else inline(line)
        story.append(Paragraph(content, styles['bullet2' if nested else 'bullet'])); i += 1
    else:
        para = [line]; i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(('#','|','- ')) and not re.match(r'^\d+\. ',lines[i]):
            para.append(lines[i].strip()); i += 1
        story.append(Paragraph(inline(' '.join(para)), styles['body']))
doc.multiBuild(story)
print(OUTPUT)
