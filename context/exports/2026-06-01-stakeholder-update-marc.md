---
audience: Marc (clinical stakeholder)
date: 2026-06-01
type: project update
---

# Phenoscribe — what's new since May

Hi Marc,

Quick update on Phenoscribe. Four improvements shipped at the end of May, and a fifth — a browser app so you can use it without the terminal — is being finished this week.

## 1. The AI double-checks its own term labels

When the AI picks an HPO code for a symptom, it has to write down both the code (like `HP:0002027`) and the matching word (like "Abdominal pain"). Peter Robinson's recent email warned that AI models are reliable at picking codes but sloppy at writing the matching words — so the code can be right while the printed label is wrong.

Phenoscribe now overwrites whatever label the AI typed with the official HPO label for that code. We log every correction, so we can tell you how often the AI was about to mislabel something.

## 2. Smarter scoring using the official HPO library

The piece of code that judges how close two symptoms are to each other was hand-written. It missed cases where two symptoms share a near "parent" — like chest pain and abdominal pain both being kinds of pain. We replaced it with `hpo-toolkit`, the official library maintained by the HPO group, which already handles those cases correctly. Sibling matches like that now get partial credit instead of zero.

## 3. Cohort-level summaries

New command: `phenoscribe aggregate`. Feed it a folder of results and it produces a CSV plus a bar chart of how many patients had each symptom — the same shape of summary as the figure in your Plovdiv poster and the children's paper. Saves you the manual Excel pivot.

## 4. Comparison against ontoGPT

We ran ontoGPT (a competing open-source tool) on the same pseudonymized transcripts and scored both with the same ground truth. Phenoscribe still comes out ahead on the metrics we care about. Full report lives at `context/exports/2026-05-30-ontogpt-benchmark.md`. This was a one-off comparison; we're not integrating ontoGPT.

## 5. Web app (in progress)

The big one. Instead of running terminal commands, you'll double-click a launcher and a browser window opens with Phenoscribe inside it. You drag in recordings, pick options (language, which AI provider, whether to pseudonymise), paste your OpenAI API key into a password box, click **Run**, and an Excel appears in the output folder when it's done.

Built with Gradio inside a Docker container. What this gets you:

- **No terminal.** Same flow as opening any web tool — except it's running entirely on your own laptop.
- **Privacy preserved.** Audio files never leave your machine. Only the pseudonymized text is sent to OpenAI (same as today's setup).
- **Works on Mac and Windows** with a single Docker install. We're testing on Mac now; Windows launcher comes next week.
- **API key stays in memory.** You paste it once per session; it isn't written to disk.

We're finishing the last integration today — first end-to-end run is in progress as I write this. Once it completes successfully we'll send you a 5-minute video walkthrough plus a one-page install guide for your machine.

## What's coming next

- Finish the web app 
- Windows launcher 
- One-page install guide aimed at non-developers 

Anything you'd like added or reprioritised, just say the word.

— Tarik
