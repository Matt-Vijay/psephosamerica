---
license: cc-by-nc-4.0
language:
- en
tags:
- law
- local-government
- ordinances
- knowledge-graph
pretty_name: OpenPact LOCUS Ordinance Graph
configs:
- config_name: default
  data_files:
  - split: train
    path: locus_ordinances.parquet
dataset_info:
  features:
  - name: canonical_id
    dtype: string
  - name: jurisdiction_id
    dtype: string
  - name: jurisdiction_level
    dtype: string
  - name: display_name
    dtype: string
  - name: function
    dtype: string
  - name: topic
    dtype: string
  - name: is_substantive
    dtype: bool
  - name: opacity
    dtype: float64
  - name: paternalism
    dtype: float64
  - name: enforcement_discretion
    dtype: float64
  - name: problem_salience
    dtype: float64
  - name: known_at
    dtype: string
  - name: source_url
    dtype: string
  - name: content_sha256
    dtype: string
  - name: license
    dtype: string
  splits:
  - name: train
    num_examples: 2207679
---

# OpenPact LOCUS Ordinance Graph

Version `v1.0`. A versioned, content-addressed Parquet release of U.S. municipal + county ordinances, each joined to a canonical jurisdiction entity in the OpenPact knowledge graph and carrying the LOCUS legal `function`, `topic`, `is_substantive` flag, and four dimension scores (`opacity`, `paternalism`, `enforcement_discretion`, `problem_salience`).

## Provenance & license

Derived from **LOCUS v1.0 (LocalLaws/LOCUS-v1), Peskoff, Barrow, Vu & Davenport, arXiv:2606.19334, licensed CC-BY-NC-4.0 (non-commercial).** Source: https://huggingface.co/datasets/LocalLaws/LOCUS-v1

This release is **CC-BY-NC-4.0 — NON-COMMERCIAL**, inheriting LOCUS's license. Do not use commercially.

## Content hash

`locus_ordinances.parquet` sha256: `6cf59179faa3015223481293943b62e98568d578014f8e0dee22e8ca1cccb020`

## Schema

| column | type |
|---|---|
| canonical_id | string |
| jurisdiction_id | string |
| jurisdiction_level | string |
| display_name | string |
| function | string |
| topic | string |
| is_substantive | bool |
| opacity | float64 |
| paternalism | float64 |
| enforcement_discretion | float64 |
| problem_salience | float64 |
| known_at | string |
| source_url | string |
| content_sha256 | string |
| license | string |
