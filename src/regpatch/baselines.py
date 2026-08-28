"""Executable weak baselines for the RegPatch environment.

These are intentionally shallow procedures.  They exercise the real candidate
contract and provide anti-shortcut measurements; none is a reference patcher.
"""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path
from typing import Literal

BaselineName = Literal[
    "copy-before",
    "copy-rule-replacement",
    "naive-regex",
    "public-hardcode",
    "damaging-partial",
]

BASELINE_NAMES: tuple[BaselineName, ...] = (
    "copy-before",
    "copy-rule-replacement",
    "naive-regex",
    "public-hardcode",
    "damaging-partial",
)

_COMMON = r"""
const request = JSON.parse(await new Response(Deno.stdin.readable).text());
const encoder = new TextEncoder();

async function digest(payload) {
  const value = await crypto.subtle.digest("SHA-256", payload);
  return Array.from(new Uint8Array(value), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function readBaseBytes() {
  return await Deno.readFile(request.base.path);
}

async function readRules() {
  const decoder = new TextDecoder();
  const values = [];
  for (const rule of request.rules) values.push(decoder.decode(await Deno.readFile(rule.path)));
  return values;
}

async function emitBytes(result) {
  await Deno.writeFile(request.output.result_path, result);
  const provenance = {
    schema_version: 1,
    episode_id: request.episode_id,
    base_sha256: request.base.sha256,
    rule_sha256s: request.rules.map((rule) => rule.sha256),
    result_sha256: await digest(result),
  };
  await Deno.writeTextFile(request.output.provenance_path, JSON.stringify(provenance) + "\n");
}

async function emitText(result) {
  await emitBytes(encoder.encode(result));
}

function xmlText(fragment) {
  return fragment.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function regexEscape(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
"""

_COPY_BEFORE = r"""
await emitBytes(await readBaseBytes());
"""

_COPY_RULE_REPLACEMENT = r"""
const rules = await readRules();
const replacements = rules.flatMap((rule) =>
  Array.from(rule.matchAll(/<SECTION\b[\s\S]*?<\/SECTION>/gi), (match) => match[0])
);
await emitText(
  "<?xml version=\"1.0\" encoding=\"UTF-8\"?><REGPATCH_RULE_TEXT>" +
  (replacements.length ? replacements.join("") : "<NO_REPLACEMENT_TEXT/>") +
  "</REGPATCH_RULE_TEXT>"
);
"""

_NAIVE_REGEX = r"""
let result = new TextDecoder().decode(await readBaseBytes());
for (const rule of await readRules()) {
  const instructions = Array.from(
    rule.matchAll(/<AMDPAR\b[^>]*>([\s\S]*?)<\/AMDPAR>/gi),
    (match) => xmlText(match[1]),
  );
  const additions = [];
  const removals = [];
  for (const instruction of instructions) {
    const quoted = instruction.match(/[“"]([^”"]+)[”"]/);
    if (!quoted) continue;
    if (/\badd(?:ing)?\b/i.test(instruction)) additions.push(quoted[1]);
    if (/\bremov(?:e|ing)\b/i.test(instruction)) removals.push(quoted[1]);
  }
  for (let index = 0; index < Math.min(additions.length, removals.length); index += 1) {
    const oldValue = regexEscape(removals[index]);
    result = result.replace(
      new RegExp(">" + oldValue + "<\\/TD>"),
      ">" + additions[index] + "</TD>",
    );
  }
}
await emitText(result);
"""

_DAMAGING_PARTIAL = r"""
let result = new TextDecoder().decode(await readBaseBytes());
let replacements = 0;
const sections = [];
for (const rule of await readRules()) {
  for (const match of rule.matchAll(/<SECTION\b[\s\S]*?<\/SECTION>/gi)) {
    const section = match[0];
    sections.push(section);
    const number = xmlText(section.match(/<SECTNO\b[^>]*>([\s\S]*?)<\/SECTNO>/i)?.[1] || "")
      .match(/\d+\.\d+/)?.[0];
    if (!number) continue;
    const pattern = new RegExp(
      "<DIV8\\b(?=[^>]*\\bN=\\\"" + regexEscape(number) + "\\\")[\\s\\S]*?<\\/DIV8>",
      "i",
    );
    if (pattern.test(result)) {
      result = result.replace(pattern, () => section);
      replacements += 1;
    }
  }
}
if (!replacements) {
  result = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><REGPATCH_PARTIAL>" +
    sections.join("") + "</REGPATCH_PARTIAL>";
}
await emitText(result);
"""


def baseline_source(
    name: BaselineName,
    *,
    public_episode_id: str | None = None,
    public_target: bytes | None = None,
) -> str:
    """Return one standalone TypeScript baseline using the public stdin contract."""
    if name == "copy-before":
        body = _COPY_BEFORE
    elif name == "copy-rule-replacement":
        body = _COPY_RULE_REPLACEMENT
    elif name == "naive-regex":
        body = _NAIVE_REGEX
    elif name == "damaging-partial":
        body = _DAMAGING_PARTIAL
    elif name == "public-hardcode":
        if not public_episode_id or public_target is None:
            raise ValueError("public-hardcode requires an episode id and exact public target bytes")
        compressed = gzip.compress(public_target, compresslevel=9, mtime=0)
        encoded = base64.b64encode(compressed).decode("ascii")
        body = f"""
if (request.episode_id === {json.dumps(public_episode_id)}) {{
  const compressed = Uint8Array.from(atob({json.dumps(encoded)}), (value) => value.charCodeAt(0));
  const stream = new Blob([compressed]).stream().pipeThrough(new DecompressionStream("gzip"));
  await emitBytes(new Uint8Array(await new Response(stream).arrayBuffer()));
}} else {{
  await emitBytes(await readBaseBytes());
}}
"""
    else:
        raise ValueError(f"unknown baseline: {name}")
    return _COMMON + body


def write_baseline(
    name: BaselineName,
    destination: Path,
    *,
    public_episode_id: str | None = None,
    public_target: bytes | None = None,
) -> Path:
    """Materialize a baseline as a candidate directory, refusing to overwrite."""
    source = baseline_source(
        name,
        public_episode_id=public_episode_id,
        public_target=public_target,
    )
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite baseline directory: {destination}")
    destination.mkdir(parents=True)
    entrypoint = destination / "solution.ts"
    entrypoint.write_text(source, encoding="utf-8")
    return entrypoint


__all__ = ["BASELINE_NAMES", "BaselineName", "baseline_source", "write_baseline"]
