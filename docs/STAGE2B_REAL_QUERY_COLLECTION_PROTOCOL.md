# Stage 2B — Real Anonymized Query Set Collection Protocol

Purpose: let a human safely collect **50-100 real, anonymized query cases** to satisfy
the first Stage 2B blocker (`real_query_set`). This is a **privacy-aware protocol +
de-identification gate only** — no real dataset lives in this repo, and following this
protocol does **not** mark the parent ROADMAP item (line 97) complete.

**Boundary (read first):** this document and its helper validate a **collection packet's
declared metadata + checklist shape**. They never collect data, never read secret files,
and never contact a network. The presence of a completed protocol does not mean a real
set exists — the set is only real once a human collects it, de-identifies it, and it
passes `summarize_real_query_readiness` as `ready_real`.

## 1. Data minimization (collect only what is needed)

Collect **only**:
- `query` — the anonymized query text.
- `provenance` — `{source, collected_at, anonymized: true}` (channel/date, no identity).
- relevance targets — `relevant_titles` / `relevant_chunk_ids` / `relevant_chunk_markers`.

Do **not** collect names, phone numbers, emails, ID-card numbers, addresses, or free-form
sensitive detail. If a query contains such detail, generalize or drop it before intake.
(FTC "Protecting Personal Information": collect and keep only what you need; protect it;
dispose of it when done.)

### Forbidden data categories
`name`, `phone`, `email`, `id_card`, address, account/credential values, health/biometric
detail, and any free-form field that re-identifies an individual or organization.

## 2. De-identification checklist (all must be true before intake)

1. **Direct identifiers removed** — names, phones, emails, ID numbers, account handles.
2. **Quasi-identifiers generalized** — dates coarsened, precise locations/roles broadened
   so a record is not uniquely re-identifying.
3. **Rare facts reviewed** — unique or outlier facts that could single out a person/org
   are removed or generalized (NIST de-identification guidance).
4. **Source provenance retained without identity** — keep channel + date, never the
   contributor's identity.
5. **Reviewer signoff** — a named-by-role (not personal-identity) reviewer confirms the
   above before the case enters the set.

## 3. Pseudonymization note

If stable case ids are required, use **random ids** or a **salted/keyed hash** computed
**outside this repo**. Never commit the mapping table, salt, or pepper. Avoid weak
**unsalted** hashes of identifiers — they are reversible by lookup (ICO pseudonymisation
guidance). Prefer tokenization/keyed hashing with the key held separately.

## 4. Retention, disposal, and access control

- **Retention**: keep the raw/collection material only as long as needed to build the
  anonymized set; define a disposal date.
- **Disposal**: securely delete raw contributions and any mapping/salt after the
  anonymized set is finalized.
- **Access control**: restrict raw collection material to the named collector/reviewer
  roles; the committed set contains anonymized cases only.
- Align overall handling with the NIST Privacy Framework (Identify/Govern/Control/
  Communicate/Protect) as a process reference.

## 5. Handoff into the artifact contract

Once 50-100 cases pass de-identification and reviewer signoff:
1. Assemble them into the `real_query_set` shape from
   `docs/STAGE2B_ARTIFACT_CONTRACTS.md` (see `examples/stage2b_artifacts.example.json`).
2. Remove all template markers (`is_template`, placeholder tokens).
3. Validate with `summarize_real_query_readiness` until status is `ready_real`.
4. Only then is the `real_query_set` blocker satisfiable in
   `build_external_dependency_audit` — and a human may consider the ROADMAP parent.

## 6. Explicit boundary

- No real dataset is in this repo; `examples/real_query_collection_packet.example.json`
  is a placeholder collection packet, not real data.
- This protocol is process guidance + a metadata/checklist gate. It never collects,
  de-identifies, or validates real people's data on its own.

## References
- NIST De-Identification of Personal Information: https://www.nist.gov/publications/de-identification-personal-information
- NIST Privacy Framework: https://www.nist.gov/privacy-framework/privacy-framework
- FTC Protecting Personal Information: https://www.ftc.gov/business-guidance/resources/protecting-personal-information-guide-business
- ICO Pseudonymisation: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/data-sharing/anonymisation/pseudonymisation/
