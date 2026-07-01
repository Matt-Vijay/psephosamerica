# Psephos America Ontology Artifact Contract

This document defines the stable website and LLM-facing ontology surface. The
implementation is intentionally static-file first: publish builds deterministic
JSON and TypeScript artifacts, verification rejects drift, and consumers do not
need to understand internal database or pipeline modules.

## Design Rule

Psephos America's ontology is the product boundary. Backend code may change, but these
artifacts are the compatibility layer for the website, offline analysis, and LLM
context tooling.

The contract follows the useful part of ontology-backed systems such as
Palantir OSDK: typed object/link/action metadata, generated client surfaces,
and governed access through explicit artifacts instead of ad hoc prompt context.

## Palantir Pattern Review

Reviewed against Palantir's current public Foundry docs on 2026-05-06:

| Palantir pattern | Psephos America translation |
| --- | --- |
| Ontology as semantic objects, properties, links, and kinetic actions/functions | Keep Psephos America ontology artifacts as typed object/link/action contracts, not loose prompt context. |
| OSDK-generated access to object types, actions, functions, and AIP logic | Generate stable `ontology/client.ts`, command plans, and verifier artifacts from contracts instead of hand-maintained operator steps. |
| Functions in TypeScript/Python that run in operational contexts | Treat runtime commands as the function layer: deterministic inputs, JSON outputs, source hashes, and explicit failure gates. |
| AIP observability over functions, actions, models, automations, and ontology loads | Preserve detached verifier artifacts, SHA-256 links, `run_metadata`, and final run verifiers as Psephos America's local observability surface. |

Useful sources:

- https://www.palantir.com/docs/foundry/ontology/overview
- https://www.palantir.com/docs/foundry/ontology-sdk/typescript-osdk/
- https://www.palantir.com/docs/foundry/functions/overview
- https://www.palantir.com/docs/foundry/functions/language-feature-support
- https://www.palantir.com/docs/foundry/aip-observability/overview

The practical decision is to stay artifact-first. Psephos America should not require a
Foundry-like runtime, but it should keep the same separation of stable ontology
contracts, typed generated access, operational functions, and execution
evidence. The prediction eval-window plan follows that pattern: it is a typed
object that emits runnable function commands, expected artifact paths, and a
final verifier that checks the whole execution graph.

## Stable Artifacts

| Path | Purpose |
| --- | --- |
| `ontology/schema.json` | Static object, link, and action catalog. |
| `ontology/agent-tools.json` | Read-only LLM/agent tool manifest for safe ontology context access. |
| `ontology/contracts.json` | JSON Schema bundle for public ontology payloads. |
| `ontology/contracts.d.ts` | TypeScript declaration surface for artifact schemas and paths. |
| `ontology/client.ts` | Generated static TypeScript client with canonical fetch/path helpers. |
| `ontology/edges.json` | Global source-backed ontology graph. |
| `ontology/index.json` | Aggregate graph counts and available member slices. |
| `ontology/frontend-index.json` | Website-fast lookup tables by member, committee, sector, issuer, link type, and source key. |
| `ontology/members/{member_bioguide_id}.json` | Member-scoped graph slice. |
| `ontology/member-features/{member_bioguide_id}.json` | Member-scoped feature summary for prediction/readiness surfaces. |

## Versioned ABI

The following values are part of the public ABI and must be changed deliberately:

| Constant | Current value |
| --- | --- |
| `ONTOLOGY_STATIC_SCHEMA_VERSION` | `psephosamerica-ontology-v1` |
| `ONTOLOGY_AGENT_TOOL_MANIFEST_VERSION` | `psephosamerica-ontology-agent-tools-v1` |
| `ONTOLOGY_FRONTEND_CONTRACT_VERSION` | `psephosamerica-ontology-contract-v1` |
| `ONTOLOGY_FRONTEND_INDEX_VERSION` | `psephosamerica-ontology-frontend-index-v1` |
| `ONTOLOGY_FRONTEND_CLIENT_VERSION` | `psephosamerica-ontology-client-v1` |

Missing `schema_version` on legacy `ontology/frontend-index.json` is still
accepted through the Pydantic default. Unknown explicit versions are rejected.

## Generated Client

`ontology/client.ts` is generated from `src.ontology.frontend_contracts` and is
not hand-authored. It gives frontend code one canonical path and fetch layer:

```ts
import { createPsephosAmericaOntologyClient } from "./ontology/client";

const ontology = createPsephosAmericaOntologyClient({ baseUrl: "/snapshots/latest" });
const index = await ontology.getFrontendIndex();
const graph = await ontology.getMemberGraph("S000148");
```

The client is deliberately minimal. It does not encode product logic, scoring
logic, or prediction heuristics. It only provides stable artifact discovery,
URL construction, and typed fetch helpers.

## Verification Gates

Publish verification protects the artifact contract:

| Gate | Coverage |
| --- | --- |
| `verify_local_ontology_edges` | Loads every ontology artifact listed in the manifest. |
| Frontend contract verification | Rejects stale `artifact_paths` and wrong contract version. |
| Agent tool verification | Rejects stale agent-tool versions and stripped source-required read tools. |
| Frontend declarations verification | Rejects missing contract interface or version. |
| Frontend client verification | Rejects missing client version, client class, or artifact path map. |
| Frontend index verification | Rejects unknown explicit schema versions and count drift against graph artifacts. |
| Member saturation verification | Rejects member graphs without matching `ontology/member-features/{member_bioguide_id}.json`, rejects member feature artifacts without matching member graphs, and rejects member feature edge/source count drift from the graph. |
| Roundtrip verification | Recomputes expected ontology artifacts from DB rows and compares published bytes semantically. |

## Change Policy

Additive changes are preferred:

1. Add new artifact paths to `src.ontology.artifact_paths.ONTOLOGY_FRONTEND_ARTIFACT_PATHS`.
2. Generate corresponding declarations/client helpers.
3. Emit the new artifact from `src.export.writer.plan_snapshot`.
4. Load it through `src.export.local_store`.
5. Verify it in `src.runtime.publish_verify_ontology`.
6. Include it in `src.runtime.publish_roundtrip_ontology` if it derives from DB rows.
7. Add tests that fail on stale paths, stale versions, or missing generated surfaces.

Breaking changes require a new version constant and compatibility tests that
prove older published snapshots fail clearly or remain intentionally loadable.
