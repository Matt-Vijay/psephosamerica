# Current assignment

Build a fresh, useful US legal-information service for LLMs. No inherited code,
data, tests, or compatibility requirements.

1. Verify official inventories, access conditions, clocks, and citation anchors.
2. Implement immutable acquisition, a small versioned evidence model, SQLite
   retrieval, and read-only MCP tools.
3. Ingest substantial complete federal/state collections and multiple local
   systems, including actual zoning geometry and preserved source documents.
4. Verify real passages against independent source representations, temporal
   exclusions, geographical results, resumability, and real MCP calls.
5. Publish measured coverage, limitations, examples, install commands, and a
   coherent working checkpoint. No nationwide-completeness claim without evidence.

Completed baseline: acquisition, source projections, real corpus, geometry,
read-only MCP, real-data receipts and fresh-install verification.

Completed continuation: literal phrase navigation, source-guided NYC/Portland
workflows and the bounded regional collection wave. All accepted batches are
published, the writer is quiescent, and held/blocked stores remain isolated.
See docs/collection-wave.md for the measured result and limitations. Final retrieval
review fixed bounded image metadata, source list hierarchy and exact publisher links;
docs/retrieval-review.json records the verification. No collection restart or legal
applicability engine was added.

Completed geographic checkpoint: pinned 2025 Census source discovery, separate
from zoning/legal applicability; see docs/geographic-discovery.md and its receipt.

Completed municipal/coverage addition: all Clerk-listed Portland titles are
accounted for (33 new HTML titles plus retained Title 33), with native section/body
reconciliation and real MCP routes. The maintained Oregon adapter completed its
92 known gaps without rewriting the 597 accepted versions. See
docs/portland-code.md, docs/oregon-completion-verification.json and the single
docs/coverage-ledger.json. The ledger uses retained July 2025 Census estimates for
separate population-reach metrics; it does not certify complete current legal layers.

Completed Georgia continuation: all 154 department PDFs now share the August 21,
2026 filing-through date. Original 124 versions remain. All 23,232 physical pages
are represented, but 90 unverified OCR pages and two source-media-only pages do not
count as verified publisher text. See docs/georgia-edition.md and its verification
receipt; the single coverage ledger records the closed-inventory population gain.

Deferred lead, not accepted corpus or a collector assignment: Virginia administrative
code. Parent's September 7 publisher probes found the advertised developer services
at https://law.lis.virginia.gov/developers/ and /xmlapi/ link to operational **/api/**
endpoints; `AdministrativeCodeGetTitleListOfXml/` and
`AdministrativeCodeGetSectionListOfXml/9/25/875/` returned JSON, not XML. The latter
supplies membership/hierarchy but sampled Body=null. Actual section bodies are in
https://law.lis.virginia.gov/admincodefull/title9/agency25/chapter875/ (675,102 bytes;
reported SHA-256 2a037d27ebf70686449e6bc0c8a5514ac50e4f8a06272f144477a66eb1e1023a).
Its HTML title is misleading; inspect the chapter heading, seven tables, four images,
IBR, part-scoped definitions and history before any fidelity claim. Broader inventory
completeness is unverified; these memory-only probes are not canonical receipts.
The publisher FAQ at https://codecommission.dls.virginia.gov/faq_va_admin_code.shtml
describes daily permanent-code updates at effectiveness (some exempt actions lag)
and exclusion of emergency regulations. Developer access is not blanket reuse
permission: Commonwealth copyright/IBR notices require a separate bounded preflight.
Do not launch this or the deferred Florida administrative-code work automatically.
