"""
Labeled dataset for the RAG retrieval benchmark (see eval/rag_eval.py).

What this measures: given the query text the real pipeline builds for one
impact (symbol + before/after code + call site, see
app.analysis._impact_query_text), does `retrieve_rules` surface the rule the
change actually violates?

Honesty constraints (do not weaken these when editing):
  * Gold labels are authored HERE, before retrieval is ever run. Never adjust
    a label because retrieval disagreed; if a label is wrong, fix it with a
    comment explaining why.
  * The corpus must stay large enough that chance is low (k=5 over 32 rules
    => ~15.6% chance hit@5 for a single-gold case).
  * Queries are code, rules are prose. Do not paste rule wording into the
    code snippets — the embedding model must bridge code -> prose, which is
    the actual production task.
  * A case may list more than one gold rule ONLY when the change genuinely
    violates both (e.g. `except Exception: pass` is both a broad catch and a
    swallowed error).

Rules that no case targets are deliberate distractors.

The corpus IS the shipped default ruleset (app.rules.DEFAULT_RULES) — the
benchmark measures retrieval over exactly what PRism deploys, and the case
labels below are pinned to its rule ids.
"""
from app.rules import DEFAULT_RULES as RULES
from app.rules import builtin_rules_markdown as rules_markdown  # noqa: F401 (re-export)

_BY_ID = {rid: (heading, bullet) for rid, heading, bullet in RULES}


def rule_text(rule_id: str) -> str:
    """The exact string parse_rules_markdown produces for this rule."""
    heading, bullet = _BY_ID[rule_id]
    return f"{heading}: {bullet}"


# Each case mirrors one impact: the changed symbol, its before/after body, and
# one call site. `gold` is the rule(s) the change violates.
CASES = [
    {
        "id": "swallowed-oserror", "gold": ["EH1"], "symbol": "load_state",
        "before": "def load_state(path):\n    with open(path) as f:\n        return json.load(f)\n",
        "after": "def load_state(path):\n    try:\n        with open(path) as f:\n            return json.load(f)\n    except OSError:\n        pass\n",
        "call_site": "state = load_state(STATE_PATH)\nresume(state)\n",
    },
    {
        "id": "return-none-failure", "gold": ["EH2"], "symbol": "fetch_user",
        "before": "def fetch_user(user_id):\n    row = db.get(user_id)\n    if row is None:\n        raise UserNotFound(user_id)\n    return User(**row)\n",
        "after": "def fetch_user(user_id):\n    row = db.get(user_id)\n    if row is None:\n        return None\n    return User(**row)\n",
        "call_site": "user = fetch_user(request.user_id)\nsend_welcome(user.email)\n",
    },
    {
        "id": "broad-except", "gold": ["EH3"], "symbol": "parse_config",
        "before": "def parse_config(text):\n    try:\n        return yaml.safe_load(text)\n    except yaml.YAMLError as e:\n        logger.error(\"bad config: %s\", e)\n        raise\n",
        "after": "def parse_config(text):\n    try:\n        return yaml.safe_load(text)\n    except Exception as e:\n        logger.error(\"bad config: %s\", e)\n        raise\n",
        "call_site": "cfg = parse_config(raw)\napply_settings(cfg)\n",
    },
    {
        "id": "assert-validation", "gold": ["EH4"], "symbol": "set_quota",
        "before": "def set_quota(account, limit):\n    if limit <= 0:\n        raise ValueError(\"limit must be positive\")\n    account.quota = limit\n",
        "after": "def set_quota(account, limit):\n    assert limit > 0\n    account.quota = limit\n",
        "call_site": "set_quota(acct, form.cleaned_data[\"limit\"])\n",
    },
    {
        "id": "signature-change", "gold": ["API1"], "symbol": "render_invoice",
        "before": "def render_invoice(order):\n    return template.render(order=order)\n",
        "after": "def render_invoice(order, currency):\n    return template.render(order=order, currency=currency)\n",
        "call_site": "html = render_invoice(order)\nmailer.send(html)\n",
    },
    {
        "id": "too-many-positional", "gold": ["API2"], "symbol": "create_report",
        "before": "def create_report(title, rows):\n    return Report(title, rows)\n",
        "after": "def create_report(title, rows, fmt, compress, notify):\n    r = Report(title, rows)\n    r.fmt = fmt\n    r.compress = compress\n    r.notify = notify\n    return r\n",
        "call_site": "report = create_report(\"Q3\", data, \"pdf\", False, True)\n",
    },
    {
        "id": "public-rename", "gold": ["API3"], "symbol": "compute_totals",
        "before": "def summarize(entries):\n    return sum(e.amount for e in entries)\n",
        "after": "def compute_totals(entries):\n    return sum(e.amount for e in entries)\n",
        "call_site": "total = summarize(ledger.entries)\n",
    },
    {
        "id": "kwargs-signature", "gold": ["API4"], "symbol": "start_job",
        "before": "def start_job(name, queue, priority):\n    broker.enqueue(name, queue=queue, priority=priority)\n",
        "after": "def start_job(name, **kwargs):\n    broker.enqueue(name, **kwargs)\n",
        "call_site": "start_job(\"reindex\", queue=\"low\", priority=2)\n",
    },
    {
        "id": "mutable-default", "gold": ["MUT1"], "symbol": "tag_release",
        "before": "def tag_release(version, labels=None):\n    labels = list(labels or [])\n    labels.append(version)\n    return labels\n",
        "after": "def tag_release(version, labels=[]):\n    labels.append(version)\n    return labels\n",
        "call_site": "tags = tag_release(\"2.4.0\")\n",
    },
    {
        "id": "mutates-argument", "gold": ["MUT2"], "symbol": "top_scores",
        "before": "def top_scores(scores, n):\n    return sorted(scores, reverse=True)[:n]\n",
        "after": "def top_scores(scores, n):\n    scores.sort(reverse=True)\n    return scores[:n]\n",
        "call_site": "best = top_scores(player.history, 3)\nplot(player.history)\n",
    },
    {
        "id": "global-mutation", "gold": ["MUT3"], "symbol": "handle_ping",
        "before": "def handle_ping(request):\n    return {\"ok\": True}\n",
        "after": "def handle_ping(request):\n    SEEN_HOSTS[request.host] = time.time()\n    return {\"ok\": True}\n",
        "call_site": "app.route(\"/ping\")(handle_ping)\n",
    },
    {
        "id": "fstring-logging", "gold": ["LOG1"], "symbol": "sync_repo",
        "before": "def sync_repo(name):\n    logger.info(\"syncing %s\", name)\n    mirror.pull(name)\n",
        "after": "def sync_repo(name):\n    logger.info(f\"syncing {name} at {time.time()}\")\n    mirror.pull(name)\n",
        "call_site": "sync_repo(repo.full_name)\n",
    },
    {
        "id": "token-logged", "gold": ["LOG2"], "symbol": "authenticate",
        "before": "def authenticate(token):\n    logger.info(\"auth attempt\")\n    return verify(token)\n",
        "after": "def authenticate(token):\n    logger.info(\"auth attempt with token %s\", token)\n    return verify(token)\n",
        "call_site": "session = authenticate(headers[\"Authorization\"])\n",
    },
    {
        "id": "fallback-at-info", "gold": ["LOG3"], "symbol": "get_rates",
        "before": "def get_rates():\n    try:\n        return provider.latest()\n    except ProviderDown:\n        logger.warning(\"provider down, using cached rates\")\n        return CACHED_RATES\n",
        "after": "def get_rates():\n    try:\n        return provider.latest()\n    except ProviderDown:\n        logger.info(\"provider down, using cached rates\")\n        return CACHED_RATES\n",
        "call_site": "rates = get_rates()\nconvert(order.total, rates)\n",
    },
    {
        "id": "shared-connection", "gold": ["CON1"], "symbol": "Worker.run",
        "before": "def run(self):\n    for job in self.jobs:\n        with pool.connection() as conn:\n            conn.execute(job.sql)\n",
        "after": "def run(self):\n    for job in self.jobs:\n        self.shared_conn.execute(job.sql)\n",
        "call_site": "for w in workers:\n    threading.Thread(target=w.run).start()\n",
    },
    {
        "id": "check-then-act", "gold": ["CON2"], "symbol": "get_or_build",
        "before": "def get_or_build(key):\n    with cache_lock:\n        if key not in cache:\n            cache[key] = build(key)\n        return cache[key]\n",
        "after": "def get_or_build(key):\n    if key not in cache:\n        cache[key] = build(key)\n    return cache[key]\n",
        "call_site": "graph = get_or_build(repo_key)\n",
    },
    {
        "id": "blocking-in-async", "gold": ["CON3"], "symbol": "fetch_status",
        "before": "async def fetch_status(url):\n    async with httpx.AsyncClient() as client:\n        r = await client.get(url, timeout=5)\n    return r.status_code\n",
        "after": "async def fetch_status(url):\n    r = requests.get(url, timeout=5)\n    return r.status_code\n",
        "call_site": "codes = await asyncio.gather(*(fetch_status(u) for u in urls))\n",
    },
    {
        "id": "sql-fstring", "gold": ["SEC1"], "symbol": "find_orders",
        "before": "def find_orders(email):\n    cur.execute(\"SELECT id FROM orders WHERE email = ?\", (email,))\n    return cur.fetchall()\n",
        "after": "def find_orders(email):\n    cur.execute(f\"SELECT id FROM orders WHERE email = '{email}'\")\n    return cur.fetchall()\n",
        "call_site": "orders = find_orders(request.args[\"email\"])\n",
    },
    {
        "id": "shell-true", "gold": ["SEC2"], "symbol": "convert_upload",
        "before": "def convert_upload(path):\n    subprocess.run([\"ffmpeg\", \"-i\", path, \"out.mp4\"], check=True)\n",
        "after": "def convert_upload(path):\n    subprocess.run(f\"ffmpeg -i {path} out.mp4\", shell=True, check=True)\n",
        "call_site": "convert_upload(upload.filename)\n",
    },
    {
        "id": "pickle-untrusted", "gold": ["SEC3"], "symbol": "load_session",
        "before": "def load_session(payload):\n    return json.loads(payload)\n",
        "after": "def load_session(payload):\n    return pickle.loads(payload)\n",
        "call_site": "session = load_session(request.cookies[\"session\"])\n",
    },
    {
        "id": "secret-equality", "gold": ["SEC4"], "symbol": "verify_signature",
        "before": "def verify_signature(body, sig):\n    expected = sign(body)\n    return hmac.compare_digest(expected, sig)\n",
        "after": "def verify_signature(body, sig):\n    expected = sign(body)\n    return expected == sig\n",
        "call_site": "if not verify_signature(raw, headers[\"X-Signature\"]):\n    abort(401)\n",
    },
    {
        "id": "unclosed-file", "gold": ["RES1"], "symbol": "append_audit",
        "before": "def append_audit(line):\n    with open(AUDIT_PATH, \"a\") as f:\n        f.write(line + \"\\n\")\n",
        "after": "def append_audit(line):\n    f = open(AUDIT_PATH, \"a\")\n    f.write(line + \"\\n\")\n",
        "call_site": "append_audit(f\"{user} deleted {doc_id}\")\n",
    },
    {
        "id": "no-timeout", "gold": ["RES2"], "symbol": "post_webhook",
        "before": "def post_webhook(url, payload):\n    return requests.post(url, json=payload, timeout=10)\n",
        "after": "def post_webhook(url, payload):\n    return requests.post(url, json=payload)\n",
        "call_site": "post_webhook(subscriber.url, event.to_dict())\n",
    },
    {
        "id": "tight-retry", "gold": ["RES3"], "symbol": "wait_ready",
        "before": "def wait_ready(host):\n    for attempt in range(5):\n        if ping(host):\n            return True\n        time.sleep(2 ** attempt)\n    return False\n",
        "after": "def wait_ready(host):\n    while True:\n        if ping(host):\n            return True\n",
        "call_site": "wait_ready(db_host)\n",
    },
    {
        "id": "naive-utcnow", "gold": ["DT1"], "symbol": "stamp_event",
        "before": "def stamp_event(event):\n    event.created_at = datetime.now(timezone.utc)\n    return event\n",
        "after": "def stamp_event(event):\n    event.created_at = datetime.utcnow()\n    return event\n",
        "call_site": "log.append(stamp_event(evt))\n",
    },
    {
        "id": "local-time-storage", "gold": ["DT2"], "symbol": "record_login",
        "before": "def record_login(user):\n    db.save(user.id, at=datetime.now(timezone.utc))\n",
        "after": "def record_login(user):\n    db.save(user.id, at=datetime.now().astimezone())\n",
        "call_site": "record_login(current_user)\n",
    },
    {
        "id": "string-concat-loop", "gold": ["PERF1"], "symbol": "render_csv",
        "before": "def render_csv(rows):\n    return \"\\n\".join(\",\".join(r) for r in rows)\n",
        "after": "def render_csv(rows):\n    out = \"\"\n    for r in rows:\n        out += \",\".join(r) + \"\\n\"\n    return out\n",
        "call_site": "body = render_csv(export_rows)\n",
    },
    {
        "id": "slurp-file", "gold": ["PERF2"], "symbol": "count_errors",
        "before": "def count_errors(log_path):\n    n = 0\n    with open(log_path) as f:\n        for line in f:\n            if \"ERROR\" in line:\n                n += 1\n    return n\n",
        "after": "def count_errors(log_path):\n    with open(log_path) as f:\n        data = f.read()\n    return data.count(\"ERROR\")\n",
        "call_site": "errors = count_errors(\"/var/log/app.log\")\n",
    },
    {
        "id": "n-plus-one", "gold": ["PERF3"], "symbol": "load_authors",
        "before": "def load_authors(post_ids):\n    posts = db.posts.find_many(post_ids)\n    return {p.id: p.author for p in posts}\n",
        "after": "def load_authors(post_ids):\n    result = {}\n    for pid in post_ids:\n        result[pid] = db.posts.find_one(pid).author\n    return result\n",
        "call_site": "authors = load_authors([p.id for p in page.posts])\n",
    },
    {
        "id": "positional-bool", "gold": ["STY1"], "symbol": "export_report",
        "before": "def export_report(report, include_drafts=False):\n    return exporter.run(report, include_drafts=include_drafts)\n",
        "after": "def export_report(report, include_drafts=False):\n    return exporter.run(report, include_drafts)\n",
        "call_site": "export_report(weekly, True)\n",
    },
]
