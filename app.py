import os, re, json, uuid, random, traceback
from flask import Flask, render_template, request, jsonify, send_file, make_response
import pdfplumber
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                 Table, TableStyle, HRFlowable, PageBreak)
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
import io, datetime

from dotenv import load_dotenv

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
loaded = load_dotenv(_env_path, override=True)
print(f"[QPGen] .env loaded from {_env_path}: {loaded}")

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '').strip()
print(f"[QPGen] GEMINI_API_KEY present: {bool(GEMINI_API_KEY)} | length: {len(GEMINI_API_KEY)}")

gemini_model = None
gemini_model_name = None
gemini_error = None

def init_gemini():
    global gemini_model, gemini_model_name, gemini_error
    if not GEMINI_API_KEY:
        gemini_error = "GEMINI_API_KEY not set in .env file"
        print(f"[QPGen] ✗ {gemini_error}")
        return False
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)

        def get_available_model():
            try:
                print("[QPGen] Listing available models...")
                models = list(genai.list_models())
                print(f"Found {len(models)} models")
                supported_models = [
                    'models/gemini-2.5-flash',
                    'models/gemini-2.5-flash-lite',
                    'models/gemini-2.5-pro',
                    'models/gemini-2.0-flash',
                    'models/gemini-2.0-flash-lite'
                ]
                for i, model in enumerate(models[:5]):
                    print(f"  Model {i}: {model.name}, methods: {getattr(model, 'supported_generation_methods', 'N/A')}")
                for model in models:
                    if model.name in supported_models and 'generateContent' in getattr(model, 'supported_generation_methods', []):
                        print(f"Selected model: {model.name}")
                        return model.name
                for model in models:
                    if 'generateContent' in getattr(model, 'supported_generation_methods', []):
                        print(f"Fallback model: {model.name}")
                        return model.name
                print("No suitable model found")
                return None
            except Exception as e:
                print(f"Error listing models: {str(e)}")
                return None

        model_name = get_available_model()
        if model_name:
            gemini_model = genai.GenerativeModel(model_name)
            gemini_model_name = model_name
            gemini_error = None
            print(f"[QPGen] ✓ Using model: {model_name}")
            return True
        else:
            gemini_error = "No suitable Gemini model found"
            print(f"[QPGen] ✗ {gemini_error}")
            return False

    except ImportError:
        gemini_error = "google-generativeai not installed. Run: pip install google-generativeai"
        print(f"[QPGen] ✗ {gemini_error}")
        return False
    except Exception as e:
        gemini_error = str(e)
        print(f"[QPGen] ✗ Gemini init error: {e}")
        return False

init_gemini()

app = Flask(__name__)
app.secret_key = 'qpgen_secret_2024'
for d in ['uploads', 'generated', 'states']:
    os.makedirs(d, exist_ok=True)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

def get_client_id():
    return request.cookies.get('qpgen_id')

def save_state(cid, data):
    with open(os.path.join('states', f"{cid}.json"), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)

def load_state(cid):
    if not cid: return {}
    p = os.path.join('states', f"{cid}.json")
    if not os.path.exists(p): return {}
    try:
        with open(p, encoding='utf-8') as f: return json.load(f)
    except: return {}

def extract_text_from_pdf(path):
    text = ""
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t: text += t + "\n"
    except: pass
    if not text.strip():
        try:
            for page in PdfReader(path).pages:
                t = page.extract_text()
                if t: text += t + "\n"
        except: pass
    return text

def extract_topics_and_facts(text):
    lines = text.split('\n')
    topics, definitions = [], []
    def_pat  = re.compile(
        r'^([A-Za-z][A-Za-z\s\-]{2,40}?)\s+(?:is|are|refers to|means|defined as|can be defined as)\s+(.{15,})',
        re.IGNORECASE)
    head_pat = re.compile(r'^([A-Z][A-Za-z\s\-]{3,60})$')
    bad      = {'the','a','an','this','that','it','they','we','which','when','where'}
    current  = "General"
    for line in lines:
        line = line.strip()
        if not line or len(line) < 5: continue
        wc = len(line.split())
        if head_pat.match(line) and 1 <= wc <= 8:
            current = line
            if line not in topics: topics.append(line)
            continue
        m = def_pat.match(line)
        if m:
            term, defn = m.group(1).strip(), m.group(2).strip()
            tw = term.split()
            if 1 <= len(tw) <= 5 and tw[0].lower() not in bad and len(defn) > 10:
                definitions.append({'topic': current, 'term': term, 'definition': defn})
    return topics, definitions

def get_keywords(text):
    stop = set(['the','a','an','is','are','was','were','be','been','have','has','had',
        'do','does','did','will','would','could','should','may','might','this','that',
        'these','those','it','its','they','them','their','we','our','you','your',
        'and','but','or','for','so','in','on','at','by','from','with','about',
        'into','out','over','under','then','when','where','how','all','both','each',
        'few','more','most','some','such','no','not','only','same','than','too',
        'very','just','also','of','to','up','as','if','which','been'])
    freq = {}
    for w in re.findall(r'\b[A-Za-z][a-z]{2,}\b', text):
        wl = w.lower()
        if wl not in stop and len(wl) > 3:
            freq[wl] = freq.get(wl, 0) + 1
    return sorted(freq, key=freq.get, reverse=True)[:50]

def rule_based_questions(text, sections):
    topics, definitions = extract_topics_and_facts(text)
    keywords = get_keywords(text)
    def dedup(lst):
        seen, out = set(), []
        for q in lst:
            k = q[:60].lower()
            if k not in seen: seen.add(k); out.append(q)
        return out
    def q1():
        qs=[]
        for d in definitions: qs+=[f"Define {d['term']}.",f"What is {d['term']}?"]
        for kw in keywords[:30]: qs+=[f"What is {kw}?",f"Define {kw}."]
        for t in topics: qs+=[f"Name two types of {t}.",f"What does {t} refer to?"]
        random.shuffle(qs); return dedup(qs)
    def q2():
        qs=[]
        for d in definitions:
            t=d['term']
            qs+=[f"Define {t} and give one example.",f"What is {t}? State its significance.",
                 f"Briefly explain {t}.",f"What are the main properties of {t}?"]
        for t in topics:
            qs+=[f"List two key characteristics of {t}.",f"State two applications of {t}.",
                 f"What do you understand by {t}? Explain briefly."]
        for kw in keywords[:20]: qs+=[f"Explain the concept of {kw} briefly.",f"State two uses of {kw}."]
        random.shuffle(qs); return dedup(qs)
    def q4():
        qs=[]
        for d in definitions:
            t=d['term']
            qs+=[f"Explain {t} in detail with suitable examples.",
                 f"What is {t}? Discuss its types and applications."]
        for t in topics:
            qs+=[f"Explain {t} in detail with examples.",
                 f"What are the advantages and disadvantages of {t}? Explain.",
                 f"Classify the types of {t} and explain each briefly."]
        for kw in keywords[:20]:
            qs+=[f"Explain {kw} with a suitable example.",f"Discuss the types and applications of {kw}."]
        random.shuffle(qs); return dedup(qs)
    def q8():
        qs=[]
        for t in topics:
            qs+=[f"Explain {t} comprehensively: definition, types, working, advantages, disadvantages, and applications.",
                 f"Write an elaborate note on {t} covering all important aspects."]
        for kw in keywords[:15]: qs+=[f"Write an essay on {kw}: definition, working, types, advantages, and applications."]
        for d in definitions: qs+=[f"Elaborate on {d['term']}: definition, types, working, advantages, disadvantages, and significance."]
        random.shuffle(qs); return dedup(qs)
    def q10():
        qs=[]
        for t in topics:
            qs+=[f"Give a detailed account of {t}: definition, background, types, working, advantages, disadvantages, and applications.",
                 f"Write a comprehensive essay on {t} covering all theoretical and practical aspects."]
        for kw in keywords[:10]: qs+=[f"Write a detailed essay on {kw}: background, types, advantages, disadvantages, applications, and future scope."]
        random.shuffle(qs); return dedup(qs)
    g={1:q1,2:q2,3:q2,4:q4,5:q4,6:q4,7:q8,8:q8,10:q10}
    result={}
    for s in sections:
        marks,count=int(s['marks']),int(s['count'])
        pool=g.get(marks,q4)()
        while len(pool)<count: pool.append(f"Explain an important concept related to {random.choice(keywords) if keywords else 'the subject'}.")
        result[marks]=pool[:count]
    return result

def rule_based_mcqs(text, count):
    topics, definitions = extract_topics_and_facts(text)
    keywords = get_keywords(text)
    mcqs = []
    for d in definitions:
        if len(mcqs) >= count: break
        term = d['term']
        correct = d['definition'][:120]
        distractors = [x['definition'][:120] for x in definitions if x['term'] != term][:3]
        while len(distractors) < 3:
            distractors.append(f"A concept unrelated to {term}")
        options = [correct] + distractors
        random.shuffle(options)
        labels = ['A','B','C','D']
        ans_idx = options.index(correct)
        mcqs.append({
            'question': f"What is the correct definition of '{term}'?",
            'options': {labels[i]: options[i] for i in range(4)},
            'answer': labels[ans_idx],
            'explanation': f"'{term}' refers to: {correct[:80]}..."
        })
    for kw in keywords:
        if len(mcqs) >= count: break
        opts = [f"A key concept related to {kw}",
                f"An unrelated term in the subject",
                f"A different concept entirely",
                f"None of the above"]
        random.shuffle(opts)
        mcqs.append({
            'question': f"Which of the following best relates to the concept of '{kw}'?",
            'options': {'A': opts[0], 'B': opts[1], 'C': opts[2], 'D': opts[3]},
            'answer': 'A',
            'explanation': f"'{kw}' is an important concept discussed in the content."
        })
    return mcqs[:count]

# ── Gemini generation ─────────────────────────────────────────────────────────
def gemini_generate(text, sections, mcq_count):
    global gemini_model, gemini_model_name
    if not gemini_model:
        return None, None, "Gemini model not initialized"

    snippet = text[:4000]

    desc_parts = ""
    if sections:
        lines = []
        for s in sections:
            m, c = int(s['marks']), int(s['count'])
            lines.append(f"  - {c} question(s) worth {m} mark(s) each")
        desc_parts = "\n".join(lines)
    else:
        desc_parts = "  - None"

    mcq_part = (f"  - {mcq_count} MCQ questions (4 options A/B/C/D, 1 correct answer, include brief explanation)"
                if mcq_count > 0 else "  - None")

    # ── FIX: Split into two focused prompts so MCQs are never truncated ──
    # First generate descriptive questions
    desc_prompt = f"""Generate ONLY descriptive exam questions from this content. Reply with ONLY valid JSON, no markdown.

CONTENT:
{snippet}

REQUIRED:
{desc_parts}

JSON FORMAT:
{{
  "descriptive": [
    {{"marks": <int>, "question": "<question text>"}}
  ]
}}"""

    # Then generate MCQs separately
    mcq_prompt = f"""Generate ONLY {mcq_count} MCQ exam questions from this content. Reply with ONLY valid JSON, no markdown.

CONTENT:
{snippet}

RULES:
- Exactly 4 options per question labeled A, B, C, D
- One correct answer letter
- One brief explanation per question
- Questions must be factual and based strictly on the content above

JSON FORMAT:
{{
  "mcq": [
    {{
      "question": "<question>",
      "options": {{"A": "<opt>", "B": "<opt>", "C": "<opt>", "D": "<opt>"}},
      "answer": "<A|B|C|D>",
      "explanation": "<why this answer is correct>"
    }}
  ]
}}"""

    marks_q = {}
    mcqs = []

    # --- Generate descriptive questions ---
    if sections:
        try:
            print(f"[QPGen] Sending DESCRIPTIVE prompt to Gemini ({gemini_model_name})...")
            resp = gemini_model.generate_content(desc_prompt)
            raw = resp.text.strip()
            print(f"[QPGen] Descriptive raw response (first 300): {raw[:300]}")
            raw = re.sub(r'^```(?:json)?\s*\n?', '', raw, flags=re.MULTILINE)
            raw = re.sub(r'\n?```\s*$', '', raw, flags=re.MULTILINE)
            raw = raw.strip()
            data = json.loads(raw)
            for item in data.get('descriptive', []):
                m = int(item.get('marks', 0))
                if m > 0:
                    if m not in marks_q: marks_q[m] = []
                    marks_q[m].append(item['question'])
            print(f"[QPGen] ✓ Descriptive questions: {sum(len(v) for v in marks_q.values())}")
        except Exception as e:
            print(f"[QPGen] ✗ Descriptive generation error: {e}")
            traceback.print_exc()

    # --- Generate MCQs separately ---
    if mcq_count > 0:
        try:
            print(f"[QPGen] Sending MCQ prompt to Gemini ({gemini_model_name})...")
            resp = gemini_model.generate_content(mcq_prompt)
            raw = resp.text.strip()
            print(f"[QPGen] MCQ raw response (first 500): {raw[:500]}")
            raw = re.sub(r'^```(?:json)?\s*\n?', '', raw, flags=re.MULTILINE)
            raw = re.sub(r'\n?```\s*$', '', raw, flags=re.MULTILINE)
            raw = raw.strip()
            data = json.loads(raw)
            mcqs = data.get('mcq', [])
            print(f"[QPGen] ✓ MCQs from Gemini: {len(mcqs)}")
        except Exception as e:
            msg = str(e)
            if "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                msg = "API quota exceeded. Try again later or enable billing."
            print(f"[QPGen] ✗ MCQ generation error: {msg}")
            traceback.print_exc()

    if not marks_q and not mcqs:
        return None, None, "Gemini returned no usable questions"

    return marks_q if marks_q else None, mcqs, None


# ── PDF Builder ───────────────────────────────────────────────────────────────
def build_pdf(questions_by_marks, mcq_list, config):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=2.2*cm, bottomMargin=2*cm)
    SS = getSampleStyleSheet()
    S  = lambda n, **kw: ParagraphStyle(n, parent=SS['Normal'], **kw)

    title_s   = S('T',  fontSize=17, alignment=TA_CENTER, spaceAfter=3, fontName='Helvetica-Bold', textColor=colors.HexColor('#0f172a'))
    sub_s     = S('Su', fontSize=11, alignment=TA_CENTER, spaceAfter=2, fontName='Helvetica')
    instr_s   = S('I',  fontSize=9,  spaceAfter=10, leftIndent=8, fontName='Helvetica-Oblique', textColor=colors.HexColor('#475569'))
    sec_s     = S('Se', fontSize=12, spaceBefore=14, spaceAfter=5, fontName='Helvetica-Bold',
                  textColor=colors.HexColor('#1e3a5f'), backColor=colors.HexColor('#eef2ff'), borderPad=5, leading=18)
    mcq_sec_s = S('MS', fontSize=12, spaceBefore=14, spaceAfter=5, fontName='Helvetica-Bold',
                  textColor=colors.HexColor('#831843'), backColor=colors.HexColor('#fdf2f8'), borderPad=5, leading=18)
    q_s       = S('Q',  fontSize=11, spaceAfter=5, leftIndent=16, fontName='Helvetica', leading=16)
    opt_s     = S('O',  fontSize=10, spaceAfter=2, leftIndent=40, fontName='Helvetica', leading=14, textColor=colors.HexColor('#1e293b'))
    ans_hdr   = S('AH', fontSize=13, spaceBefore=14, spaceAfter=6, fontName='Helvetica-Bold',
                  textColor=colors.HexColor('#166534'), backColor=colors.HexColor('#dcfce7'), borderPad=5, leading=18)
    tot_s     = S('Tot', fontSize=11, alignment=TA_RIGHT, spaceBefore=5, fontName='Helvetica-Bold')

    story = []
    story.append(Paragraph(config.get('institution', 'EXAMINATION'), title_s))
    story.append(Paragraph(config.get('subject', 'Subject'), sub_s))
    story.append(Spacer(1, 4))

    date_str    = datetime.datetime.now().strftime('%d-%m-%Y')
    total_marks = sum(m * len(qs) for m, qs in questions_by_marks.items()) + len(mcq_list)
    total_q     = sum(len(qs) for qs in questions_by_marks.values()) + len(mcq_list)

    info = Table([[f"Date: {date_str}", f"Total Marks: {total_marks}", f"Total Questions: {total_q}"]],
                 colWidths=[5.5*cm, 5.5*cm, 5.5*cm])
    info.setStyle(TableStyle([
        ('FONTNAME',(0,0),(-1,-1),'Helvetica'), ('FONTSIZE',(0,0),(-1,-1),10),
        ('ALIGN',(0,0),(0,0),'LEFT'), ('ALIGN',(1,0),(1,0),'CENTER'),
        ('ALIGN',(2,0),(2,0),'RIGHT'), ('BOTTOMPADDING',(0,0),(-1,-1),4),
    ]))
    story.append(info)
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#0f172a')))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Instructions: Answer all questions. Marks are shown against each question. "
        "For MCQs, circle the correct option. Answer Key provided at end.",
        instr_s))

    q_num = 1

    # Descriptive sections
    for marks in sorted(questions_by_marks.keys()):
        qs = questions_by_marks[marks]
        if not qs: continue
        story.append(Paragraph(
            f"  SECTION — {marks} Mark{'s' if marks>1 else ''} Questions"
            f"  ({marks} × {len(qs)} = {marks*len(qs)} marks)", sec_s))
        story.append(HRFlowable(width="100%", thickness=0.4, color=colors.HexColor('#cbd5e1')))
        story.append(Spacer(1, 3))
        for q in qs:
            story.append(Paragraph(f"<b>Q{q_num}.</b> &nbsp;[{marks} mark{'s' if marks>1 else ''}] &nbsp; {q}", q_s))
            story.append(Spacer(1, 6))
            q_num += 1
        story.append(Spacer(1, 8))

    # ── FIX: Set mcq_start_num BEFORE the if block so Answer Key always has it ──
    mcq_start_num = q_num

    # MCQ section
    if mcq_list:
        story.append(Paragraph(
            f"  SECTION — Multiple Choice Questions  (1 × {len(mcq_list)} = {len(mcq_list)} marks)",
            mcq_sec_s))
        story.append(HRFlowable(width="100%", thickness=0.4, color=colors.HexColor('#f9a8d4')))
        story.append(Spacer(1, 3))
        for mcq in mcq_list:
            story.append(Paragraph(f"<b>Q{q_num}.</b> &nbsp;[1 mark] &nbsp; {mcq['question']}", q_s))
            for key in ['A','B','C','D']:
                story.append(Paragraph(f"&nbsp;&nbsp;&nbsp;({key})  {mcq['options'].get(key,'')}", opt_s))
            story.append(Spacer(1, 8))
            q_num += 1

    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#0f172a')))
    story.append(Paragraph(f"<b>Grand Total: {total_marks} Marks</b>", tot_s))

    # Answer Key
    if mcq_list:
        story.append(PageBreak())
        story.append(Paragraph("  MCQ ANSWER KEY  ", ans_hdr))
        story.append(HRFlowable(width="100%", thickness=0.4, color=colors.HexColor('#86efac')))
        story.append(Spacer(1, 8))

        ak_s = S('AK', fontSize=9, fontName='Helvetica', leading=14)
        rows, row = [], []
        for i, mcq in enumerate(mcq_list):
            qno  = mcq_start_num + i
            ck   = mcq.get('answer', '?')
            cv   = mcq['options'].get(ck, '')
            expl = mcq.get('explanation', '')
            cell_text = f"<b>Q{qno}.</b>  Ans: <b>({ck})</b>  {cv}"
            if expl:
                cell_text += f"<br/><font size='8' color='#166534'>{expl}</font>"
            row.append(Paragraph(cell_text, ak_s))
            if len(row) == 2: rows.append(row); row = []
        if row:
            while len(row) < 2: row.append(Paragraph("", ak_s))
            rows.append(row)

        if rows:
            at = Table(rows, colWidths=[8.25*cm, 8.25*cm])
            at.setStyle(TableStyle([
                ('FONTNAME',(0,0),(-1,-1),'Helvetica'), ('FONTSIZE',(0,0),(-1,-1),9),
                ('ROWBACKGROUNDS',(0,0),(-1,-1),[colors.HexColor('#f0fdf4'), colors.white]),
                ('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#bbf7d0')),
                ('TOPPADDING',(0,0),(-1,-1),6), ('BOTTOMPADDING',(0,0),(-1,-1),6),
                ('LEFTPADDING',(0,0),(-1,-1),8), ('VALIGN',(0,0),(-1,-1),'TOP'),
            ]))
            story.append(at)

    doc.build(story)
    buf.seek(0)
    return buf

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/status')
def status():
    return jsonify({
        'gemini_ready': gemini_model is not None,
        'model': gemini_model_name.replace('models/', '') if gemini_model_name else None,
        'error': gemini_error
    })

@app.route('/upload', methods=['POST'])
def upload_pdf():
    cid = get_client_id()
    new_client = not cid
    if new_client: cid = str(uuid.uuid4())

    if 'pdf' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'})
    file = request.files['pdf']
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'success': False, 'error': 'Only PDF files allowed'})

    fname = str(uuid.uuid4()) + '.pdf'
    path  = os.path.join('uploads', fname)
    file.save(path)

    text = extract_text_from_pdf(path)
    if len(text.strip()) < 50:
        return jsonify({'success': False, 'error': 'Could not extract text. Use a text-based PDF (not scanned).'})

    topics, definitions = extract_topics_and_facts(text)
    keywords = get_keywords(text)
    pages = 0
    try: pages = len(PdfReader(path).pages)
    except: pass

    save_state(cid, {'text': text, 'topics': topics[:20], 'keywords': keywords[:20]})

    resp = make_response(jsonify({
        'success': True, 'pages': pages,
        'topics': topics[:10], 'keywords': keywords[:15],
        'definitions': len(definitions), 'facts': len(text.split('\n')),
        'word_count': len(text.split()), 'gemini_ready': gemini_model is not None
    }))
    if new_client:
        resp.set_cookie('qpgen_id', cid, max_age=86400, samesite='Lax')
    return resp

@app.route('/generate', methods=['POST'])
def generate():
    cid  = get_client_id()
    text = load_state(cid).get('text', '')
    if not text:
        return jsonify({'success': False, 'error': 'No PDF found. Please re-upload your PDF.'})

    data      = request.get_json()
    sections  = [s for s in data.get('sections', []) if int(s.get('count', 0)) > 0 and int(s.get('marks', 0)) > 0]
    mcq_count = max(0, int(data.get('mcq_count', 0)))
    config    = data.get('config', {})

    if not sections and mcq_count == 0:
        return jsonify({'success': False, 'error': 'Add at least one section or MCQs.'})

    questions_by_marks = {}
    mcq_list           = []
    used_gemini        = False
    gen_error          = None

    # ── Try Gemini first ──
    if gemini_model:
        marks_q, mcqs, err = gemini_generate(text, sections, mcq_count)
        if marks_q is not None or mcqs is not None:
            used_gemini = True
            if marks_q:
                questions_by_marks = marks_q
            # ── FIX: use `is not None` so an empty list [] still gets assigned ──
            if mcqs is not None:
                mcq_list = mcqs[:mcq_count]
                print(f"[QPGen] Gemini MCQs assigned to mcq_list: {len(mcq_list)}")
        else:
            gen_error = err
            print(f"[QPGen] Gemini failed: {err} — using rule-based fallback")
    else:
        gen_error = gemini_error
        print(f"[QPGen] Gemini not ready ({gemini_error}) — using rule-based")

    # ── Rule-based fallback for any missing descriptive sections ──
    missing = [s for s in sections if int(s['marks']) not in questions_by_marks
               or len(questions_by_marks.get(int(s['marks']), [])) < int(s['count'])]
    if missing:
        rb = rule_based_questions(text, missing)
        for m, qs in rb.items():
            questions_by_marks[m] = qs

    # ── FIX: Rule-based fallback fills any shortfall in MCQs (not just zero) ──
    if mcq_count > 0 and len(mcq_list) < mcq_count:
        needed = mcq_count - len(mcq_list)
        print(f"[QPGen] Need {needed} more MCQs from rule-based fallback")
        mcq_list += rule_based_mcqs(text, needed)
        print(f"[QPGen] mcq_list now has {len(mcq_list)} MCQs")

    # Trim to exact requested counts
    for s in sections:
        m, c = int(s['marks']), int(s['count'])
        if m in questions_by_marks:
            questions_by_marks[m] = questions_by_marks[m][:c]
    mcq_list = mcq_list[:mcq_count]

    print(f"[QPGen] FINAL — descriptive sections: {list(questions_by_marks.keys())}, MCQs: {len(mcq_list)}")

    # ── Build PDF ──
    try:
        pdf_buf  = build_pdf(questions_by_marks, mcq_list, config)
        out_name = f"qp_{uuid.uuid4().hex[:8]}.pdf"
        with open(os.path.join('generated', out_name), 'wb') as f:
            f.write(pdf_buf.getvalue())
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'PDF generation failed: {e}'})

    total_marks = sum(m*len(qs) for m,qs in questions_by_marks.items()) + len(mcq_list)
    preview_desc = {str(m): list(qs) for m, qs in questions_by_marks.items()}
    preview_mcq  = [{'q': m['question'], 'options': m['options'],
                     'answer': m.get('answer','?'), 'explanation': m.get('explanation','')}
                    for m in mcq_list]

    return jsonify({
        'success': True, 'filename': out_name,
        'total_marks': total_marks,
        'total_questions': sum(len(qs) for qs in questions_by_marks.values()) + len(mcq_list),
        'used_gemini': used_gemini,
        'gemini_error': gen_error,
        'preview_desc': preview_desc,
        'preview_mcq': preview_mcq
    })

@app.route('/download/<filename>')
def download(filename):
    if not re.match(r'^qp_[a-f0-9]{8}\.pdf$', filename):
        return "Invalid filename", 400
    path = os.path.join('generated', filename)
    if not os.path.exists(path): return "File not found", 404
    return send_file(path, as_attachment=True, download_name='question_paper.pdf')

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)