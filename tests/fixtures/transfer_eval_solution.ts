// Gold behavioral fixture for focused runner/scorer tests; never copied to the public bundle.
type Row = Record<string, string>;

const clean = (value: unknown): string => typeof value === "string" ? value.trim() : "";
const nullable = (value: unknown): string | null => clean(value) || null;
const unique = (values: string[]): string[] => [...new Set(values.filter(Boolean))].sort();
const date = (value: unknown): string | null => {
  const match = clean(value).match(/^(\d{4}-\d{2}-\d{2})/);
  if (!match || Number.isNaN(Date.parse(`${match[1]}T00:00:00Z`))) return null;
  return match[1];
};

function csv(text: string): Row[] {
  const matrix: string[][] = [];
  let row: string[] = [], field = "", quoted = false;
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (quoted) {
      if (char === '"' && text[index + 1] === '"') { field += '"'; index++; }
      else if (char === '"') quoted = false;
      else field += char;
    } else if (char === '"') quoted = true;
    else if (char === ",") { row.push(field); field = ""; }
    else if (char === "\n") {
      row.push(field.replace(/\r$/, "")); matrix.push(row); row = []; field = "";
    } else field += char;
  }
  if (field || row.length) { row.push(field.replace(/\r$/, "")); matrix.push(row); }
  const headers = matrix.shift() ?? [];
  return matrix.filter((values) => values.some(Boolean)).map((values) =>
    Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""]))) as Row[];
}

function stringList(value: unknown): string[] {
  const raw = clean(value);
  if (!raw || raw === "[]" || raw === "()") return [];
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return unique(parsed.map(clean));
  } catch { /* Python repr follows. */ }
  if (!"[(".includes(raw[0]) || !"] )".replace(" ", "").includes(raw.at(-1)!)) return [raw];
  const body = raw.slice(1, -1), values: string[] = [];
  let token = "", quote = "", escaped = false;
  for (const char of body) {
    if (escaped) { token += char; escaped = false; }
    else if (char === "\\") escaped = true;
    else if (quote && char === quote) quote = "";
    else if (!quote && (char === "'" || char === '"')) quote = char;
    else if (!quote && char === ",") { if (clean(token)) values.push(clean(token)); token = ""; }
    else token += char;
  }
  if (clean(token)) values.push(clean(token));
  return unique(values);
}

async function rows(path: string): Promise<Row[]> {
  try { return csv(await Deno.readTextFile(path)); }
  catch (error) { if (error instanceof Deno.errors.NotFound) return []; throw error; }
}

function urlIndex(input: Row[], keyName: string): Map<string, string[]> {
  const grouped = new Map<string, string[]>();
  for (const row of input) {
    const key = clean(row[keyName]), url = clean(row.url);
    if (!key || !/^https?:\/\//i.test(url)) continue;
    grouped.set(key, unique([...(grouped.get(key) ?? []), url]));
  }
  return grouped;
}

const request = JSON.parse(await new Response(Deno.stdin.readable).text());
const cutoffDate = date(request.cutoff)!;
const emit = (record: unknown) => console.log(JSON.stringify(record));

for (const artifact of request.artifacts) {
  if (Date.parse(artifact.available_at) > Date.parse(request.cutoff)) continue;
  const base = artifact.path;
  const [bills, actions, billSourceRows, votes, people, voteSourceRows, orgRows] =
    await Promise.all([
      rows(`${base}/bills.csv`), rows(`${base}/bill_actions.csv`),
      rows(`${base}/bill_sources.csv`), rows(`${base}/votes.csv`),
      rows(`${base}/vote_people.csv`), rows(`${base}/vote_sources.csv`),
      rows(`${base}/organizations.csv`),
    ]);
  const billSources = urlIndex(billSourceRows, "bill_id");
  const voteSources = urlIndex(voteSourceRows, "vote_event_id");
  const chambers = new Map(orgRows.map((row) => [clean(row.id), nullable(row.classification)]));
  const billChambers = new Map(bills.map((row) =>
    [clean(row.id), nullable(row.organization_classification)]));
  const urlsForBill = (id: string | null): string[] =>
    (id && billSources.get(id)) || [artifact.source_url];

  emit({
    record_type: "source", source_id: artifact.source_id, source_url: artifact.source_url,
    content_sha256: artifact.content_sha256, available_at: artifact.available_at,
  });
  for (const row of bills) emit({
    record_type: "bill", bill_id: clean(row.id), jurisdiction_id: artifact.jurisdiction_id,
    session: clean(row.session_identifier) || artifact.session, identifier: nullable(row.identifier),
    title: nullable(row.title), classifications: stringList(row.classification),
    subjects: stringList(row.subject), chamber: nullable(row.organization_classification),
    source_id: artifact.source_id, source_urls: urlsForBill(clean(row.id)),
  });
  for (const row of actions) {
    const eventDate = date(row.date), billId = clean(row.bill_id);
    if (!eventDate || eventDate > cutoffDate) continue;
    emit({
      record_type: "action", action_id: clean(row.id), bill_id: billId,
      organization_id: nullable(row.organization_id), description: nullable(row.description),
      classifications: stringList(row.classification), event_date: eventDate,
      source_id: artifact.source_id, source_urls: urlsForBill(billId),
    });
  }
  const eligibleRolls = new Map<string, {session: string, urls: string[]}>();
  for (const row of votes) {
    const eventDate = date(row.start_date), rollId = clean(row.id);
    if (!eventDate || eventDate > cutoffDate) continue;
    const billId = nullable(row.bill_id), organizationId = nullable(row.organization_id);
    const sourceUrls = voteSources.get(rollId) ?? urlsForBill(billId);
    const session = clean(row.session_identifier) || artifact.session;
    emit({
      record_type: "roll_call", roll_call_id: rollId,
      jurisdiction_id: artifact.jurisdiction_id, session, bill_id: billId,
      organization_id: organizationId,
      chamber: chambers.get(organizationId ?? "") ?? billChambers.get(billId ?? "") ?? null,
      identifier: nullable(row.identifier), motion: nullable(row.motion_text),
      result: nullable(row.result), event_date: eventDate, source_id: artifact.source_id,
      source_urls: sourceUrls,
    });
    eligibleRolls.set(rollId, {session, urls: sourceUrls});
  }
  for (const row of people) {
    const rollId = clean(row.vote_event_id), roll = eligibleRolls.get(rollId);
    if (!roll || !clean(row.id) || !clean(row.option)) continue;
    emit({
      record_type: "member_vote", member_vote_id: clean(row.id), roll_call_id: rollId,
      jurisdiction_id: artifact.jurisdiction_id, session: roll.session,
      person_id: nullable(row.voter_id), member_name: nullable(row.voter_name),
      choice: clean(row.option), source_id: artifact.source_id, source_urls: roll.urls,
    });
  }
}
