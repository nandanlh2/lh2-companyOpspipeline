# companyOps — LH2 ops-data procurement

Procurement flow for **company ops data** (SOPs, playbooks, process, tooling,
org design) from wound-down Indian companies. HubSpot is the source of truth.

- Flow spec: `OPSDATA_SOP_GoogleDocs_flowchart1.png` (the SOP flowchart)
- Operating context / hard rules: `context.md` — read it before writing any API call
- Pipeline + property contract: `docs/OPSDATA_PIPELINE.md`
- Sync scripts: `opsdata/pipeline_sync.py`, `opsdata/properties_sync.py`
  — dry-run by default, `--apply` to write, audit JSON in `audit/` (gitignored)

Secrets: `HUBSPOT_API_KEY` in `.env` (gitignored — never commit it). Scripts
read `.env` directly; no terminal env injection needed.
