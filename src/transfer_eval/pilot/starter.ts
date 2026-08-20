// Psephos Transfer Eval V0 starter: stdin request -> JSONL stdout.
const request = JSON.parse(await new Response(Deno.stdin.readable).text());

// This minimal executable emits only source provenance. Replace it with a reusable
// CSV normalization procedure following TASK.md; do not hardcode case IDs or answers.
for (const artifact of request.artifacts) {
  console.log(JSON.stringify({
    record_type: "source",
    source_id: artifact.source_id,
    source_url: artifact.source_url,
    content_sha256: artifact.content_sha256,
    available_at: artifact.available_at,
  }));
}
