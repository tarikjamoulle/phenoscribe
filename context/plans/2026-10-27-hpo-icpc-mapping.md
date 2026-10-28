# Plan: add ICPC-2 and ICPC-3 codes next to each HPO code

Date: 2026-10-27 (revised the same day after a second literature search). Status: plan only, nothing built. Asked for by Marc Jamoulle.
Small first version: `2026-10-27-icpc-columns.md` (Marc's table + about 50 drafted codes).

## Goal

Each row in the Codes sheet gets an ICPC-2 code and an ICPC-3 code next to the HPO code.
Marc can then count the corpus with the classification that family medicine uses.

## State of the art (checked 2026-10-27)

No public HPO to ICPC table exists. The closest work:

| Work | What it does | Use for us |
|---|---|---|
| Jamoulle and van Boven (Nijmegen), unpublished, cited in Marc's draft "From Long COVID Patient Wording to Terminological Biomarker" | Maps the 286 Long COVID HPO terms of Deer et al. to ICPC-3 | The best start: done by hand, by the ICPC-3 team, for Long COVID. Ask Marc for the file. |
| de Almeida et al., JAMIA Open 2026, "LLMs as medical code selectors" (van Boven is a co-author) | Free text to ICPC-2. Embeds 73,563 thesaurus terms (text-embedding-3-large) in Chroma, takes the top 10-200, and an LLM picks one code. F1 0.87 for the best models, 0.80 for "first search result". | The method for our fallback. Code: github.com/almeidava93/llm-as-code-selectors-paper |
| Belgian 3BT thesaurus (SPF Santé publique) | More than 51,000 French and Dutch terms, each with an ICD-10 and an ICPC-2 code. Frozen. Now kept as the GP reference set of the Belgian SNOMED CT extension. | The term list for the fallback, in French. The Brazilian thesaurus in de Almeida comes from it. Free licence from the SPF. |
| UMLS (`ICPC2EENG`, `ICPC2P`, `ICPC2ICD10ENG`) and BioPortal | Links terms that share a UMLS concept (CUI). BioPortal already shows these links for ICPC2P. | Exact HPO to ICPC-2 links, because `hp.obo` has 12,842 `xref: UMLS` lines |
| HPO Mapper, Kadhim et al., medRxiv 2025 (Robinson is a co-author) | Text and ICD-10 to HPO with embeddings and an LLM check. F1 0.84-0.87. | Other direction, no ICPC. It confirms the "retrieve then LLM check" design that Phenoscribe already uses. |
| SNOMED CT to ICPC-2 map | Retired after January 2019 | Do not use |
| SNOClass (Ghent), SNOMED CT core primary care to ICPC-3 | Started January 2025, no public file | Check again later |

## Approach

ICPC-2 has about 700 codes. HPO has about 19,000 terms. So many HPO terms go to one ICPC code.
Fatigue (HP:0012378) goes to A04 "Weakness/tiredness general".

### Step 1: hand-made table from Marc (ICPC-3)

1. Ask Marc for the Jamoulle and van Boven table (286 HPO terms to ICPC-3).
2. Load it as `data/icpc/hpo_icpc3_manual.csv`, with `link type = manual`.
3. Count the share of our Codes rows that it covers. Our top 20 HPO terms are probably in it.

### Step 2: exact HPO to ICPC-2 through UMLS

1. Get a free UMLS licence and download `MRCONSO.RRF`.
2. Keep the rows with `SAB` in (`ICPC2EENG`, `ICPC2P`). Each row gives a CUI and an ICPC-2 code.
3. Join on the CUI from the `xref: UMLS` lines in `hp.obo`.
4. For a term with no link, the nearest HPO ancestor with a link gives the code (`link type = ancestor`).
5. Write `data/icpc/hpo_icpc2.csv`. Commit it only if the UMLS licence allows it.

### Step 3: ICPC-2 to ICPC-3

1. Ask Marc for the ICPC-2 to ICPC-3 conversion table of the ICPC Foundation ("ICPC-3 Conversions ICPC" on icpc-3.info).
2. A one-to-many link goes to review.

### Step 4: fallback with Chroma and the 3BT (the de Almeida method)

1. Get the 3BT, or the GP reference set of the Belgian SNOMED CT extension, from the SPF.
2. Embed the French terms in a new Chroma collection (`icpc_3bt`) in `data/chroma_db`. Each term keeps its ICPC-2 and ICD-10 code.
3. For a row with no link from steps 1-3, search with the French clinical term and the patient verbatim. Take the top 20.
4. The LLM picks one term, or none. Mark the row `approximate` and REVIEW.

This step codes from the patient's words, the same as Phenoscribe does for HPO. It does not need the HPO code to be right.

### Step 5: Excel output

Add these columns to the Codes sheet after `HPO Code`: `ICPC-2`, `ICPC-2 Title`, `ICPC-3`, `ICPC-3 Title`, `ICPC Link`.
`ICPC Link` is `manual`, `exact`, `ancestor`, `approximate` or empty.
The Provenance sheet records the source and the version of each table.

## Checks

- Compare steps 2 and 4 with Marc's manual table (step 1) on the terms they share. Report the agreement, not "% correct".
- Count how many Codes rows each step covers.

## Questions for Marc

1. Can we use the HPO to ICPC-3 table that he made with Kees van Boven?
2. Can he get the ICPC-2 to ICPC-3 conversion table?
3. Does he have the 3BT file, or does he know who at the SPF gives it out?
4. Does he want ICPC-3 only, or ICPC-2 and ICPC-3?

## Sources

- Jamoulle, draft "From Long COVID Patient Wording to Terminological Biomarker": https://orbi.uliege.be/bitstream/2268/332788/1/From_Long_COVID_Patient_Wording_to_Terminological_Biomarker20250706.pdf
- de Almeida et al., JAMIA Open 2026: https://academic.oup.com/jamiaopen/article/9/1/ooag017/8494400 (preprint https://arxiv.org/html/2507.14681v1)
- de Almeida et al., ICPC-2 and search engines: https://www.ovid.com/jnls/jmai/fulltext/10.21037/jmai-24-341~international-classification-of-primary-care-icpc-2-and
- HPO Mapper, Kadhim et al.: https://www.medrxiv.org/content/10.64898/2025.12.20.25342726v2.full
- 3BT at the SPF Terminology Centre: https://www.health.belgium.be/fr/centre-de-terminologie-systemes-de-terminologie-thesaurus-3bt-bilingual-biclassified-belgian
- Belgian SNOMED CT portal: https://apps.health.belgium.be/terminology-portal/standards/terminology_standards/snomed_ct/fr
- CMG, 3BT to SNOMED CT project: https://www.lecmg.be/recherche/mapping-3bt-bilingual-bicoded-belgian-thesaurus-terminologie/
- BioPortal mappings (CUI, LOOM): https://www.bioontology.org/wiki/BioPortal_Mappings
- UMLS ICPC2EENG: https://nlm.nih.gov/research/umls/sourcereleasedocs/current/ICPC2EENG/metadata.html
- SNOMED CT to ICPC-2 map retirement: https://conf.spaces.snomed.org/wiki/spaces/RMT/pages/131957467
- ICPC-3: https://www.icpc-3.info/ and licence https://terminology.hl7.org/CodeSystem-ICPC3.xml.html
- SNOClass, Ghent: https://www.snomed.org/snomed-in-action/ghent-university-1-of-2
