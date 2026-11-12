# Demo video script (2:00)

Recorded from cached runs only, no live model calls. Every number on screen comes from the database.

Framing question: "Why is the value of this property what it is, and can we trust the AI?"

| Time | On screen | Click / action | Voice-over |
| --- | --- | --- | --- |
| 0:00–0:15 | Data room folder of Acuitus sale particulars, then the TraceLease title | none | "An analyst gets a data room of sale particulars and has days to build a rent roll they can defend, and today none of those numbers trace back to a source." |
| 0:15–0:40 | **Documents & fields** tab, fixture document The Hidings, Cirencester | Pick the document in the sidebar; click Unit 2 passing rent | "Two extractors, V7 Go and Grok, read every tenancy schedule; each field comes with its verbatim quote and the page, highlighted right here." |
| 0:40–1:05 | **Evaluation** tab, then **Review queue** | Switch scope to "all documents"; click the invented Unit 2 break; click the Unit 1 footnote break Grok missed | "Against an automatic reference, we see what each model caught, missed, got wrong or invented; this break's quote isn't on the page, so it never reaches the model, and only fields that pass every check go in automatically." |
| 1:05–1:30 | **Proforma & Explain** tab | Click Value; expand to total rent and exit yield; click a unit rent, then the yield source | "Click the value: rent over yield, each rent back to its quote on page three, the yield back to a market snapshot with its query and timestamp." |
| 1:30–1:50 | **Review queue**, then **Changes** | Correct a break date with a reason; open the diff between versions | "The analyst corrects a break date with a reason; WAULT and value recompute, and the diff shows who changed what, when and why." |
| 1:50–2:00 | Grok Bot chat connected to the TraceLease MCP server | Ask "Why did the value drop?" | "Ask Grok why the value dropped, and it answers with the chain of sources, every link back to a page." |

Notes for recording:

- Show the sample size whenever a metric is on screen; do not quote a number that is not on screen.
- Do not say or imply protection against prompt injection.
- The value shown is a simple gross value (rent / yield); do not call it a net value.
